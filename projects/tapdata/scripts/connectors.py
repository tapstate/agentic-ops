#!/usr/bin/env python3
"""当前工位连接器的选择、编译和原生 PDK 注册；不管理任务状态。"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import uuid
import xml.etree.ElementTree as ET
import zipfile

from test_environment import Environment, EnvironmentError, contained, read_json, write_json


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def specification(value):
    return isinstance(value, dict) and isinstance(value.get('properties'), dict) and 'id' in value['properties']


def modules(repo):
    """按实际 POM 和连接器规格发现，不维护名称清单。"""
    result = {}
    for pom in sorted(repo.rglob('pom.xml')):
        relative = pom.relative_to(repo)
        if any(part in {'target', '.git', 'node_modules'} for part in relative.parts):
            continue
        resources = pom.parent / 'src/main/resources'
        if not resources.exists():
            continue
        found = False
        for file in resources.rglob('*.json'):
            try:
                found = found or specification(json.loads(file.read_text()))
            except (ValueError, UnicodeError):
                pass
        if not found:
            continue
        element = ET.parse(pom).getroot()
        artifact = element.find('{*}artifactId')
        if artifact is not None and artifact.text:
            result[pom.parent.relative_to(repo).as_posix()] = artifact.text.strip()
    return result


def select(available, requested):
    chosen = []
    for name in requested:
        matches = [path for path, artifact in available.items()
                   if name.casefold() in {path.casefold(), artifact.casefold(), artifact.removesuffix('-connector').casefold()}]
        if len(matches) != 1:
            raise EnvironmentError('连接器名称不存在或有歧义：' + name)
        if matches[0] not in chosen:
            chosen.append(matches[0])
    if not chosen:
        raise EnvironmentError('至少指定一个连接器')
    return chosen


def jar_for(module):
    candidates = []
    for file in sorted((module / 'target').glob('*.jar')):
        if file.name.startswith('original-') or file.name.endswith(('-sources.jar', '-javadoc.jar', '-tests.jar')):
            continue
        with zipfile.ZipFile(file) as jar:
            valid = False
            for name in jar.namelist():
                if name.endswith('.json') and not name.startswith('META-INF/'):
                    try:
                        valid = valid or specification(json.loads(jar.read(name)))
                    except (ValueError, UnicodeError):
                        pass
            if valid:
                candidates.append(file)
    if len(candidates) > 1:
        pom = ET.parse(module / 'pom.xml').getroot()
        artifact = pom.findtext('{*}artifactId')
        version = pom.findtext('{*}version') or pom.findtext('{*}parent/{*}version')
        shade = pom.findall('.//{*}plugin')
        if artifact and version and any(plugin.findtext('{*}artifactId') in {'maven-shade-plugin', 'maven-assembly-plugin'}
                                       and plugin.findtext('{*}configuration/{*}finalName') for plugin in shade):
            # 本分支 Shade finalName 与普通 Maven Jar 并存；普通依赖 Jar 不能代替完整插件。
            candidates = [file for file in candidates if file.name != artifact + '-' + version + '.jar']
    if len(candidates) != 1:
        raise EnvironmentError('构建制品不唯一或缺少连接器规格；不选择历史 Jar')
    return candidates[0]


def execute(argv, cwd, log, env=None):
    with log.open('wb') as stream:
        os.chmod(log, 0o600)
        return subprocess.run(argv, cwd=cwd, env=env, stdout=stream, stderr=stream).returncode


def build(environment, repo, chosen, args):
    for value in (args.maven, args.java_home, args.local_repository):
        if not value or not Path(value).is_absolute():
            raise EnvironmentError('Agent 需提供核验后的 Maven、Java Home 与任务隔离仓库绝对路径')
    before = environment.snapshot()
    directory = contained(environment.station / 'runtime', 'tapdata-connectors/' + uuid.uuid4().hex)
    directory.mkdir(parents=True, mode=0o700)
    helper = Path(__file__).with_name('test-environment-maven.py')
    argv = [os.sys.executable, str(helper), '--maven', args.maven, '--java-home', args.java_home,
            '--local-repository', args.local_repository, '--', 'clean', 'package', '-pl', ','.join(chosen), '-am']
    if args.profile:
        argv.append('-P' + ','.join(args.profile))
    protoc = getattr(args, 'protoc_command', None)
    if protoc:
        if not Path(protoc).is_absolute() or not Path(protoc).is_file() or not os.access(protoc, os.X_OK):
            raise EnvironmentError('protocCommand 需要已核验可执行的绝对路径')
        argv.append('-DprotocCommand=' + protoc)
    if args.skip_tests:
        argv.append('-DskipTests')
    record = {'schema_version': 1, 'station_id': environment.identity,
              'repository': str(repo.relative_to(environment.source)), 'source': before,
              'modules': chosen, 'artifacts': [], 'tests_skipped': args.skip_tests}
    record_path = directory / 'build.json'
    write_json(record_path, record)
    if execute(argv, repo, directory / 'build.log'):
        raise EnvironmentError('连接器构建失败；未注册，私有现场已保留')
    if before != environment.snapshot():
        raise EnvironmentError('构建期间源码变化；不注册候选')
    for module in chosen:
        jar = jar_for(contained(repo, module))
        target = directory / jar.name
        if target.exists():
            raise EnvironmentError('多个连接器制品同名；不覆盖')
        shutil.copyfile(jar, target)
        target.chmod(0o600)
        record['artifacts'].append({'module': module, 'file': target.name, 'sha256': sha(target)})
    record['built'] = True
    write_json(record_path, record)
    return record_path


def url(value):
    from urllib.parse import urlsplit
    parsed = urlsplit(value)
    if parsed.scheme not in {'http', 'https'} or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in {'', '/'}:
        raise EnvironmentError('TM 地址必须是无凭据、无查询参数的 HTTP(S) 服务地址')
    return value.rstrip('/')


def target_url(environment, args):
    if args.env:
        name, config = environment.config(args.env)
        state = environment.state()
        if not state or state['environment'] != name:
            raise EnvironmentError('指定开发环境尚未活动；不会自动部署或切换')
        if config != state['config']:
            raise EnvironmentError('目标运行配置与活动现场不同；先核对实际 TM 地址')
        ports = [config['ports'][node]['tm'] for node, roles in config['nodes'].items() if 'TM' in roles]
        if not ports:
            raise EnvironmentError('目标环境没有 TM')
        return 'http://127.0.0.1:' + str(ports[0])
    if not args.tm_url:
        raise EnvironmentError('注册需要指定环境名或明确 TM 地址')
    return url(args.tm_url)


def argfile(arguments):
    # Picocli 原生 @file 解析；密码不进入进程 argv。
    if any('\n' in value or '\r' in value or '\x00' in value for value in arguments):
        raise EnvironmentError('参数不得含换行或空字符')
    return '\n'.join('"' + value.replace('\\', '\\\\').replace('"', '\\"') + '"' for value in arguments) + '\n'


def register(environment, repo, chosen, record_path, args):
    record_path = contained(environment.station / 'runtime', str(Path(record_path).resolve().relative_to(environment.station / 'runtime')))
    record = read_json(record_path)
    if record.get('station_id') != environment.identity or not record.get('built') or record.get('repository') != str(repo.relative_to(environment.source)):
        raise EnvironmentError('构建记录不属于当前工位或构建未完成')
    if record['source'] != environment.snapshot():
        raise EnvironmentError('当前源码与构建记录不一致；需重新编译后注册')
    artifacts = {item['module']: item for item in record['artifacts']}
    selected = []
    for module in chosen:
        if module not in artifacts:
            raise EnvironmentError('构建记录没有所选连接器')
        item = artifacts[module]
        file = contained(record_path.parent, item['file'])
        if sha(file) != item['sha256']:
            raise EnvironmentError('制品校验值不一致；未上传')
        selected.append((module, file))
    target = target_url(environment, args)
    credential_name = args.credentials
    if not credential_name and args.env:
        credential_name = 'tapdata-test-env/' + args.env + '/admin-password'
    if not credential_name or not args.pdk_cli or not args.java_home:
        raise EnvironmentError('注册需原生 PDK CLI、Java Home 和秘密凭据文件')
    credentials_path = contained(environment.station / 'config/secrets', credential_name)
    if credentials_path.stat().st_mode & 0o077:
        raise EnvironmentError('凭据文件必须仅当前用户可读')
    if credentials_path.name == 'admin-password':
        password = credentials_path.read_text(encoding='utf-8').rstrip('\r\n')
        credentials = {'password': password}
    else:
        credentials = read_json(credentials_path)
    if set(credentials) == {'access_code'}:
        auth = ['-a', credentials['access_code']]
    elif set(credentials) == {'password'}:
        auth = ['-u', 'admin@admin.com', '-p', credentials['password']]
    elif set(credentials) == {'access_key', 'secret_key'}:
        auth = ['-ak', credentials['access_key'], '-sk', credentials['secret_key']]
    else:
        raise EnvironmentError('凭据使用 password、access_code 或 access_key/secret_key 三选一')
    if not all(isinstance(value, str) and value for value in auth):
        raise EnvironmentError('注册凭据无效')
    cli = Path(args.pdk_cli)
    java = Path(args.java_home) / 'bin/java'
    if not cli.is_absolute() or not cli.is_file() or not java.is_absolute() or not java.is_file():
        raise EnvironmentError('PDK CLI 与 Java 需已核验的绝对路径')
    receipts = []
    output = record_path.parent / ('registration-' + uuid.uuid4().hex + '.json')
    for module, file in selected:
        # 原生 CLI 在上传后原地加密 Jar；只处理部署副本，保留可复用编译制品。
        upload_dir = output.with_suffix('') / str(len(receipts))
        upload_dir.mkdir(parents=True, mode=0o700)
        upload_file = upload_dir / file.name
        shutil.copyfile(file, upload_file)
        upload_file.chmod(0o600)
        fd, temporary = tempfile.mkstemp(prefix='.pdk-args-', dir=record_path.parent)
        try:
            with os.fdopen(fd, 'w') as stream:
                stream.write(argfile(['-t', target, '--latest=' + str(args.latest).lower()] + auth + [str(upload_file)]))
            code = execute([str(java), '-jar', str(cli), 'register', '@' + temporary], repo,
                           output.with_suffix('.' + str(len(receipts)) + '.log'))
        finally:
            Path(temporary).unlink(missing_ok=True)
        receipts.append({'module': module, 'sha256': sha(file),
                         'uploaded_md5': hashlib.md5(file.read_bytes()).hexdigest(),
                         'deployment_copy_sha256': sha(upload_file), 'exit_code': code,
                         'submitted': code == 0, 'verified': False})
        write_json(output, {'target': target, 'latest': args.latest, 'results': receipts})
        if code:
            raise EnvironmentError('注册失败或结果未明；已保留逐项回执，先回查，不重试整个批次')
    return {'target': target, 'results': receipts, 'receipt': str(output), 'verification': '需按项目指引回查 TM 版本和文件校验值'}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=['list', 'build', 'register', 'build-register'])
    parser.add_argument('--station', required=True)
    parser.add_argument('--repository', default='tapdata/tapdata-connectors')
    parser.add_argument('--connector', action='append', default=[])
    parser.add_argument('--maven')
    parser.add_argument('--java-home')
    parser.add_argument('--local-repository')
    parser.add_argument('--profile', action='append', default=[])
    parser.add_argument('--protoc-command', help='Mac Debezium 原生 protocCommand 可执行文件绝对路径')
    parser.add_argument('--skip-tests', action='store_true')
    parser.add_argument('--record')
    target = parser.add_mutually_exclusive_group()
    target.add_argument('--env')
    target.add_argument('--tm-url')
    parser.add_argument('--pdk-cli')
    parser.add_argument('--credentials', help='config/secrets 内相对路径')
    parser.add_argument('--latest', action='store_true', help='明确更新目标最新版本；缺省仅上传')
    args = parser.parse_args(argv)
    try:
        environment = Environment(args.station)
        repo = contained(environment.source, args.repository)
        available = modules(repo)
        if args.operation == 'list':
            print(json.dumps({'repository': args.repository, 'connectors': available}, ensure_ascii=False))
            return 0
        chosen = select(available, args.connector)
        with environment.lock():
            record = args.record
            if args.operation in {'build', 'build-register'}:
                if args.operation == 'build-register':
                    target_url(environment, args)
                record = build(environment, repo, chosen, args)
            if args.operation in {'register', 'build-register'}:
                if not record:
                    raise EnvironmentError('单独注册需已有构建记录，不隐式编译')
                result = register(environment, repo, chosen, record, args)
            else:
                result = {'built': True, 'modules': chosen, 'record': str(record)}
            print(json.dumps(result, ensure_ascii=False))
        return 0
    except (ValueError, OSError, KeyError, TypeError, ET.ParseError, zipfile.BadZipFile) as error:
        message = str(error) if isinstance(error, EnvironmentError) else '配置、制品或工具核验失败；私有现场保留'
        print(json.dumps({'error': message}, ensure_ascii=False))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
