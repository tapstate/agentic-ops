"""原生仓库清理的配方、证据与回执；不执行构建工具或删除源码。"""
from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from workflow import engineering_baseline as baseline, project_rules, station_directories as directories
from workflow import station_source as source, station_resources as resources, station_operation as operations, task_store


def _unique_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError('清理配方包含重复 JSON 键')
        value[key] = item
    return value


def validate_configuration(value):
    """完整配置先校验，不能在保全报告途中才发现下一仓配方无效。"""
    if (not isinstance(value, dict) or set(value) != {'schema_version', 'repositories'}
            or type(value['schema_version']) is not int or value['schema_version'] != 1
            or not isinstance(value['repositories'], dict)):
        raise ValueError('清理配方结构或版本无效')
    for name, recipe in value['repositories'].items():
        baseline.repository_id(name)
        if not isinstance(recipe, dict) or recipe.get('kind') not in ('maven', 'web', 'project-script'):
            raise ValueError('清理配方类型无效：' + name)
        expected = {'kind', 'generated', 'reports', 'source_ref'}
        if recipe['kind'] == 'project-script':
            expected.add('script')
        if set(recipe) != expected:
            raise ValueError('清理配方字段无效：' + name)
        baseline.text(recipe['source_ref'], '清理配方 source_ref')
        for field in ('generated', 'reports'):
            patterns = recipe[field]
            if not isinstance(patterns, list) or (field == 'generated' and not patterns):
                raise ValueError('清理配方模式列表无效：' + name + '/' + field)
            for pattern in patterns:
                baseline.text(pattern, '清理配方路径模式')
                parts = pattern.split('/')
                if ('\\' in pattern or any(part in ('', '.', '..') for part in parts)
                        or (field == 'generated' and any('**' in part and part != '**' for part in parts))):
                    raise ValueError('清理配方需要仓库内相对路径模式：' + name)
        if recipe['kind'] == 'project-script':
            script = baseline.text(recipe['script'], '清理配方 script')
            parts = script.split('/')
            if (len(parts) != 2 or parts[0] != 'scripts' or parts[1] in ('.', '..')
                    or not parts[1] or '\\' in script):
                raise ValueError('清理配方脚本必须位于项目 scripts 目录：' + name)


def configuration(base):
    root, project = project_rules.station_context(base)
    path = project_rules.project_root(root, project) / 'repo-cleanup.json'
    if path.is_symlink():
        raise ValueError('清理配方不能为链接')
    raw = path.read_bytes()
    value = json.loads(raw, object_pairs_hook=_unique_object)
    validate_configuration(value)
    return root, value, hashlib.sha256(raw).hexdigest()


def sha(path):
    if path.is_symlink() or not path.is_file():
        raise ValueError('配方输入或报告不是普通文件：' + str(path))
    return hashlib.sha256(path.read_bytes()).hexdigest()


def commands(base, task, name, recipe, paths):
    root, project_id = project_rules.station_context(base)
    project = project_rules.project_root(root, project_id)
    repository = source.repository_path(base, name)
    kind = recipe['kind']
    if kind == 'maven':
        inputs = {'pom.xml': sha(repository / 'pom.xml')}
        local = directories.runtime_child(base, task, 'maven-local')
        argv = ['mvn', '-Dmaven.repo.local=' + str(local), 'clean']
    elif kind == 'web':
        package = repository / 'package.json'
        value = json.loads(package.read_text())
        if not isinstance(value.get('scripts', {}).get('clean'), str) or not value['scripts']['clean'].strip():
            raise ValueError('Web 工程没有已声明的 clean 脚本：' + name)
        manager = str(value.get('packageManager', '')).split('@')[0]
        if manager not in ('pnpm', 'npm'):
            raise ValueError('Web 包管理器未明确声明，不能猜测：' + name)
        inputs = {'package.json': sha(package)}
        argv = [manager, 'run', 'clean']
    elif kind == 'project-script':
        script_directory = project / 'scripts'
        script = project / recipe['script']
        if (script.parent != script_directory or script_directory.is_symlink()
                or script.resolve().parent != script_directory.resolve()):
            raise ValueError('项目清理脚本越界或父目录为链接')
        inputs = {str(script): sha(script)}
        argv = ['python3', str(script), '--repository', str(repository)]
    else:
        raise ValueError('未知原生清理类型')
    return {'cwd': 'source/' + name, 'argv': argv, 'inputs': inputs, 'paths': paths,
            'source_ref': recipe['source_ref']}


def report_path(base, relative):
    parent, name = relative.rsplit("/", 1)
    path = directories.path_at(base, parent) / name
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise ValueError("报告目标不是普通文件")
    return path


def inspect(base, task, roots, preserve=False, previous=None):
    base = Path(base).resolve()
    product, config, config_digest = configuration(base)
    errors, plans, reports = [], {}, {}
    if previous and previous['config_digest'] != config_digest:
        errors.append('清理配方已变化，需要重新确认完整范围')
    grouped = {}
    report_bytes = 0
    for row in roots.values():
        if row['kind'] == 'source-generated':
            grouped.setdefault('/'.join(row['path'].split('/')[1:3]), []).append(row['path'])
    for name, paths in sorted(grouped.items()):
        recipe = config['repositories'].get(name)
        if not recipe:
            errors.append('仓库缺少清理配方：' + name); continue
        repository = source.repository_path(base, name)
        try:
            for pattern in recipe['generated']:
                for candidate in repository.glob(pattern):
                    relative = candidate.relative_to(Path(base)).as_posix()
                    if '.git' not in candidate.relative_to(repository).parts and not directories.covered(relative, roots):
                        errors.append('原生命令可能清理未登记目录：' + relative)
            command = commands(base, task, name, recipe, sorted(paths))
            command['refs'] = source.git(repository, 'show-ref').stdout
            files = {}
            for relative in paths:
                path = directories.path_at(base, relative)
                if not path.exists():
                    continue
                directories.validate(base, roots[relative])
                if source.git(repository, 'ls-files', '-z', '--', str(path.relative_to(repository))).stdout:
                    raise ValueError('生成目录包含跟踪源码：' + relative)
                for candidate in sorted(path.rglob('*')):
                    if candidate.is_symlink():
                        # node_modules 的原生 clean 可处理链接；不跟随或读取目标。
                        files[candidate.relative_to(Path(base)).as_posix()] = {'kind': 'symlink'}
                        continue
                    if candidate.is_mount() or candidate.stat().st_dev != path.stat().st_dev:
                        raise ValueError('生成目录包含挂载对象：' + relative)
                    if not candidate.is_file() and not candidate.is_dir():
                        raise ValueError('生成目录包含特殊文件：' + str(candidate))
                    if candidate.is_file():
                        filename = candidate.relative_to(Path(base)).as_posix()
                        files[filename] = {'kind': 'file'}
                        relrepo = candidate.relative_to(repository).as_posix()
                        if any(fnmatch.fnmatchcase(relrepo, pattern) for pattern in recipe['reports']):
                            if candidate.stat().st_size > 16 * 1024 * 1024:
                                raise ValueError('报告超过保全上限，请先显式导出：' + filename)
                            report_bytes += candidate.stat().st_size
                            if report_bytes > 64 * 1024 * 1024:
                                raise ValueError('报告总量超过 64MiB，请先显式导出')
                            digest = sha(candidate)
                            backup = 'runtime/reports/native-clean/' + baseline.digest(name)[:16] + '/' + digest + '/' + candidate.name
                            target = report_path(base, backup)
                            if preserve:
                                target.parent.mkdir(parents=True, exist_ok=True)
                                if not target.exists():
                                    temporary = target.with_name('.' + target.name + '.tmp')
                                    if temporary.is_symlink():
                                        raise ValueError('报告暂存不能为链接')
                                    with temporary.open('wb') as stream:
                                        stream.write(candidate.read_bytes()); stream.flush(); os.fsync(stream.fileno())
                                    os.replace(temporary, target)
                                    directories.sync(target.parent)
                            if not target.is_file() or sha(target) != digest:
                                errors.append('报告尚未保全，请执行 native_cleanup.py preserve：' + filename)
                            reports[filename] = {'sha256': digest, 'backup': backup}
            command['files'] = files
            plans[name] = command
        except (ValueError, OSError, KeyError) as exc:
            errors.append(name + '：' + str(exc))
    # 所有仓一次列出未登记产物，不能第一个文件报错就停止盘点。
    for name in task.get('engineering_baseline', {}).get('repositories', {}):
        try:
            repository = source.repository_path(base, name)
            ignored = source.git(repository, 'ls-files', '--others', '--ignored', '--exclude-standard', '-z').stdout.split('\0')
            errors += ['未登记生成物：source/' + name + '/' + filename for filename in ignored
                       if filename and not directories.covered('source/' + name + '/' + filename, roots)]
        except (ValueError, OSError) as exc:
            errors.append(name + '：' + str(exc))
    value = {'config_digest': config_digest, 'repositories': plans, 'reports': reports}
    if previous:
        # 原命令只能减少已授权对象；脚本、引用和新增路径变化需要完整新确认。
        for name, original in previous['repositories'].items():
            current = plans.get(name)
            if not current or any(current[k] != original[k] for k in ('cwd', 'argv', 'inputs', 'paths', 'refs')):
                errors.append('原生命令、脚本或 Git 引用变化：' + name)
            elif not set(current['files']) <= set(original['files']):
                errors.append('清理中出现新增路径：' + name)
        for filename, row in previous['reports'].items():
            target = report_path(base, row['backup'])
            if not target.is_file() or sha(target) != row['sha256']:
                errors.append('保全报告变化：' + filename)
        value = previous
    return value, sorted(set(errors))


def preserve(base, issue, run_id):
    with task_store.task_state_lock(base):
        task = task_store.check_expected_run(base, issue, run_id)
        task_store.require_development(base, task)
        resources.verify_stopped(base, task)
        resources.verify_known_external(base, task)
        value, errors = inspect(base, task, directories.load(base, task), preserve=True)
        return {'run_id': run_id, 'reports': value['reports'], 'problems': errors}


def pending(base, task, operation):
    plan = operation['cleanup_plan']
    value, errors = inspect(base, task, {r['path']: r for r in plan['directories']}, previous=plan['native_clean'])
    receipts = (operation.get('native_receipts', {}) if operation.get('native_plan_digest') == baseline.digest(value) else {})
    from workflow import station_artifacts
    for name, original in plan['source'].items():
        if original.get('initial_checkout'):
            continue
        try:
            current = station_artifacts.snapshot(base, name, {e['path']: e for e in plan['directories']},
                {e['path']: e['preservation'] for e in plan['entries']}, original['neutral']['sha'])
            expected_entries = [e for e in plan['entries'] if e['repository'] == name]
            if current['entries'] != expected_entries or any(current[k] != original[k] for k in ('head', 'branch', 'index_sha256')):
                errors.append('原生清理前后源码指纹变化：' + name)
        except (ValueError, OSError) as exc:
            errors.append(name + '：' + str(exc))
    try:
        resources.verify_active(base, plan, operation)
        if resources.task_fingerprint(task) != plan['active_state']['task_digest']:
            errors.append('当前任务范围或事实变化')
    except (ValueError, OSError) as exc:
        errors.append(str(exc))
    for name, command in value['repositories'].items():
        receipt = receipts.get(name, {})
        if receipt.get('exit_code') != 0 or not receipt.get('source_ref'):
            errors.append('原生清理未成功回读：' + name)
        errors += ['原生清理残留：' + path for path in command['paths'] if directories.path_at(base, path).exists()]
    return sorted(set(errors))


def receipt(base, issue, run_id, operation_id, results):
    with task_store.task_state_lock(base):
        task = task_store.check_expected_run(base, issue, run_id)
        operation = operations.read(base)
        if (not operation or operation['operation_id'] != operation_id or operation['run_id'] != run_id
                or operation['status'] != 'running' or operation.get('phase') != 'awaiting_native_clean'
                or operation.get('cleanup_plan', {}).get('schema_version') != 5):
            raise ValueError('回执必须属于当前等待原生清理的操作')
        expected = operation['cleanup_plan']['native_clean']['repositories']
        if not isinstance(results, dict) or set(results) != set(expected):
            raise ValueError('请一次提供全部仓库原生清理结果')
        for row in results.values():
            if not isinstance(row, dict) or set(row) != {'exit_code', 'source_ref'} or type(row['exit_code']) is not int or not isinstance(row['source_ref'], str) or not row['source_ref'].strip():
                raise ValueError('原生回执需要 exit_code 和 source_ref')
        if project_rules.scan_sensitive(project_rules.load_admission(station=base), json.dumps(results)):
            raise ValueError('原生回执含敏感信息')
        operation.setdefault('native_history', []).append(results)
        operation['native_receipts'] = results
        operation['native_plan_digest'] = baseline.digest(operation['cleanup_plan']['native_clean'])
        operations.save(base, operation)
        return {'problems': pending(base, task, operation), 'operation_id': operation_id}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('preserve', 'receipt'))
    parser.add_argument('--dir', required=True)
    parser.add_argument('--issue-key', required=True)
    parser.add_argument('--expected-run-id', required=True)
    parser.add_argument('--operation-id')
    parser.add_argument('--input')
    args = parser.parse_args()
    try:
        if args.action == 'preserve':
            result = preserve(args.dir, args.issue_key, args.expected_run_id)
        else:
            if not args.operation_id or not args.input:
                raise ValueError('receipt 需要 operation-id 和 input')
            result = receipt(args.dir, args.issue_key, args.expected_run_id, args.operation_id, json.loads(Path(args.input).read_text()))
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 3 if result.get('problems') else 0
    except (ValueError, OSError, KeyError, TypeError) as exc:
        print('错误：' + str(exc), file=sys.stderr)
        return 4


if __name__ == '__main__':
    sys.exit(main())
