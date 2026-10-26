"""TapData 配置草稿：从备份提取确定输入，不执行旧构建配方。"""
import copy
from pathlib import Path
import shutil
from workflow.environment_config import read, write, path, inventory

FIELDS = {'schema_version', 'nodes', 'image', 'platform', 'mongo_uri_file', 'ports', 'java', 'external_tm_url'}
PREPARATION = {'mongo_driver', 'node_binary', 'build', 'assets', 'heartbeat', 'fe_heartbeat'}


def candidates(store, name, external_tm_url=None):
    root = path(store.root, 'backups')
    found = []
    if not root.exists():
        return found
    for batch in sorted(root.iterdir()):
        if batch.is_symlink() or not batch.is_dir() or not (batch / ".complete.json").is_file():
            continue
        for relative in ('tapdata-test-env/' + name + '.json', 'tapdata-test-env/drafts/' + name + '.json'):
            file = path(batch, relative)
            if file.is_file():
                config = read(file)
                if not isinstance(config, dict):
                    continue
                if external_tm_url is not None and config.get('external_tm_url') != external_tm_url:
                    continue
                found.append((file.relative_to(store.root).as_posix(), config, batch))
    return found


def fill_missing(target, source):
    """仅补缺省/空字段，已有输入包括 False、0 均不覆盖。"""
    for key, value in source.items():
        if key not in target or (key in ('platform', 'mongo_uri_file', 'image') and target[key] == ''):
            target[key] = copy.deepcopy(value)
        elif isinstance(target[key], dict) and isinstance(value, dict):
            fill_missing(target[key], value)


def prepare(env, name, external_tm_url=None, backup=None):
    store = env.store
    target = store.environment(name)
    formal = target / 'config.json'
    draft = path(target, 'drafts/config.json')
    # 不合当前合同的正式输入仍是待修补材料，保留原件并沿草稿路径处理。
    if formal.exists() and not draft.exists():
        config = read(formal)
        if complete(env, config, name, draft=False):
            return result(env, name, formal, config, [], [])
    config = read(draft) if draft.exists() else (read(formal) if formal.exists() else {})
    if not isinstance(config, dict):
        raise ValueError('已有配置损坏，不按空配置覆盖')
    if external_tm_url is not None and config.get('external_tm_url', external_tm_url) != external_tm_url:
        raise ValueError('已有草稿目标不同，请核验原草稿；不覆盖')
    # 旧格式先隔离，查找转换只在输入不足时执行。
    if not complete(env, config, name, draft=True):
        if (env.root / 'active.json').exists():
            legacy = [p for p in store.root.iterdir() if p.name not in ('environments', 'shared', 'backups', '.lock')]
            if legacy:
                raise ValueError('存在活动现场，保留旧配置；先核验原环境使用关系')
        store.backup_legacy()
        found = candidates(store, name, external_tm_url)
        if backup:
            found = [row for row in found if row[0] == backup]
            if not found:
                raise ValueError('指定备份不匹配目标环境')
        if len(found) > 1:
            return {'environment': name, 'applied': False, 'conversion': 'selection_required',
                    'candidates': [row[0] for row in found], 'missing': ['backup_selection']}
    else:
        found = []
    reused, warnings = [], []
    if found:
        origin, legacy, batch = found[0]
        converted = {k: copy.deepcopy(v) for k, v in legacy.items() if k in FIELDS}
        converted['schema_version'] = 2
        secret_map = {}
        refs = [('mongo_uri_file', 'secrets/' + str(legacy.get('mongo_uri_file', '')), 'mongodb-uri')]
        for node, port in legacy.get('ports', {}).items():
            if port.get('license'):
                refs.append((node, port['license'], 'license.txt'))
        for field, old_ref, filename in refs:
            old = path(batch, old_ref)
            new_ref = 'environments/' + name + '/drafts/secrets/' + filename
            new = store.reference(new_ref, name, draft=True, secret=True)
            if old.is_file():
                inventory(old)
                new.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                if not new.exists():
                    with new.open('xb') as out:
                        new.chmod(0o600)
                        out.write(old.read_bytes())
                elif new.read_bytes() != old.read_bytes():
                    warnings.append('候选秘密与已填文件不同，保留已填内容：' + filename)
            else:
                warnings.append('备份秘密缺失：' + filename)
            secret_map[field] = new_ref
        converted['mongo_uri_file'] = secret_map.get('mongo_uri_file', '')
        for node in converted.get('ports', {}):
            if node in secret_map:
                converted['ports'][node]['license'] = secret_map[node]
        fill_missing(config, converted)
        # 其它环境秘密只按明确旧环境归属复制，散放文件留给 Agent 核验。
        old_secrets = path(batch, 'secrets/tapdata-test-env/' + name)
        if old_secrets.is_dir():
            inventory(old_secrets)
            for old in old_secrets.iterdir():
                if old.is_file():
                    new = path(target, 'drafts/secrets/' + old.name)
                    if not new.exists():
                        new.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                        shutil.copyfile(old, new)
                        new.chmod(0o600)
        prep = {k: copy.deepcopy(v) for k, v in legacy.items() if k in PREPARATION}
        old_prep = path(batch, 'tapdata-test-env/preparation/' + name + '.json')
        if old_prep.is_file():
            prep = read(old_prep)
        if prep:
            prep['schema_version'] = 1
            candidate = path(target, 'drafts/preparation-candidate.json')
            if not candidate.exists():
                write(candidate, prep)
            warnings.append('preparation-candidate.json 仅供 Agent 核验，不能直接当成当前有效配方')
        reused.append(origin)
    defaults = read(Path(__file__).resolve().parent.parent / 'templates/station/test-env.json')
    defaults['mongo_uri_file'] = 'environments/' + name + '/drafts/secrets/mongodb-uri'
    defaults['ports']['node1']['license'] = 'environments/' + name + '/drafts/secrets/license.txt'
    if external_tm_url:
        defaults['external_tm_url'] = external_tm_url
        defaults['nodes'] = {'node1': ['FE'], 'node2': ['FE']}
        defaults['ports'] = {n: {'tm': None, 'api': None, 'license': defaults['ports']['node1']['license']} for n in defaults['nodes']}
    # 只在全新配置使用完整默认节点；已有结构不凭空增加 node1 或 TM。
    for key, value in defaults.items():
        if key not in config:
            config[key] = copy.deepcopy(value)
    if contract_problem(env, config):
        write(draft, config)
        return result(env, name, draft, config, reused, warnings)
    for secret in env.input_secrets(config, name=name, draft=True):
        secret.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if not secret.exists():
            with secret.open('xb'):
                secret.chmod(0o600)
        elif not secret.is_file() or secret.stat().st_mode & 0o077:
            raise ValueError('已有秘密文件必须为仅当前用户可读的普通文件')
    write(draft, config)
    return result(env, name, draft, config, reused, warnings)


def complete(env, config, name, draft):
    try:
        env.validate_runtime(config)
        for secret in env.input_secrets(config, name=name, draft=draft):
            if not secret.is_file() or secret.stat().st_mode & 0o077 or not secret.read_bytes().strip():
                return False
        return True
    except (ValueError, KeyError, TypeError, OSError):
        return False


def result(env, name, file, config, reused, warnings, reused_saved=False, **kwargs):
    problem = contract_problem(env, config)
    if problem:
        return {'environment': name, 'config_file': str(file), 'applied': False, 'reused': reused,
                'warnings': warnings, 'missing': ['configuration_contract'], 'differences': [problem],
                'secret_files': [], 'inputs': [],
                'guidance': '依据本功能依赖描述和项目事实核实差异，经研发确认修补草稿后重新 check-inputs/configure；正式配置未改动'}
    missing = [key for key in ('platform', 'nodes', 'image', 'java') if not config.get(key)]
    secrets = env.input_secrets(config, name=name, draft='drafts' in file.parts)
    missing.extend(str(p) for p in secrets if not p.is_file() or not p.read_bytes().strip())
    return {'environment': name, 'config_file': str(file), 'applied': False, 'reused': reused,
            'warnings': warnings, 'missing': missing, 'secret_files': [str(p) for p in secrets],
            'inputs': [{'field': k, 'value': config.get(k)} for k in ('platform', 'nodes', 'external_tm_url', 'ports', 'java')],
            'preparation': '由 Agent 核验当前源码与工具链后提供'}


def contract_problem(env, config):
    candidate = copy.deepcopy(config)
    if not candidate.get('platform'):
        candidate['platform'] = 'linux/amd64'  # 空平台仍由 missing 展示，不隐式保存。
    try:
        env.validate_runtime(candidate)
    except (ValueError, KeyError, TypeError) as error:
        return str(error)
    return None
