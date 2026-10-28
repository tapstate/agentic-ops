#!/usr/bin/env python3
"""安装版本定点切换与有限续接；只读取产品配置、兼容清单和工位索引。"""

# 产品入口：先取得租约，再重新加载本版脚本；导入库模块不启动 CLI。
if __name__ == "__main__" and not globals().get("_AO_PRODUCT_ENTRY"):
    from pathlib import Path as _AoPath
    _ao_loader = _AoPath(__file__).resolve().parents[1] / "bootstrap/product_access.py"
    _ao_namespace = {"__file__": str(_ao_loader), "_AO_LOADER_SOURCE": _ao_loader.read_bytes()}
    exec(compile(_ao_namespace["_AO_LOADER_SOURCE"], str(_ao_loader), "exec"), _ao_namespace)
    raise SystemExit(_ao_namespace["python_entry"](__file__))

import argparse
from contextlib import contextmanager
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from bootstrap import product_state, station_compatibility, product_access
from workflow.git_environment import git_environment
from bootstrap import python_runtime

INSTALL_PATHS = ('adapters', 'bootstrap', 'contracts', 'docs', 'gate', 'policies', 'projects', 'workflow')
GIT_OPERATIONS = {'fetch': '下载版本材料', 'cat-file': '核验本地对象', 'ls-tree': '读取版本文件清单',
    'rev-list': '读取提交历史', 'rev-parse': '读取提交引用', 'merge-base': '核验提交关系',
    'status': '检查工作目录', 'ls-files': '检查受管文件', 'remote': '核验来源远端',
    'check-ref-format': '核验分支名称', 'reflog': '核验切换记录',
    'sparse-checkout': '准备安装文件范围', 'merge': '切换产品版本', 'checkout': '检出产品版本'}


def git(root, *arguments, action=None, input_text=None):
    env = git_environment()
    if action:
        env['GIT_REFLOG_ACTION'] = action
    operation = GIT_OPERATIONS.get(arguments[0], '执行 Git 操作')
    try:
        result = subprocess.run(['git', '-C', str(root), *arguments], capture_output=True, text=True,
                                env=env, timeout=120, input=input_text)
    except subprocess.TimeoutExpired as error:
        raise ValueError('%s超时；保留现场，请先核验原操作结果' % operation) from error
    except OSError as error:
        raise ValueError('%s无法启动；请检查 Git 和安装目录。\n系统原始诊断：%s' % (operation, error)) from error
    if result.returncode:
        raise ValueError('%s失败（退出码=%s）。\nGit 原始诊断：%s' %
                         (operation, result.returncode, result.stderr.strip() or '未提供诊断'))
    return result.stdout.strip()


def exact(value):
    if not isinstance(value, str) or not re.fullmatch(r'[0-9a-f]{40}|[0-9a-f]{64}', value):
        raise ValueError('生命周期引用必须是完整 SHA')
    return value


@contextmanager
def lifecycle_lock(root, mode):
    directory = root / '.local/lifecycle.lock'
    directory.parent.mkdir(mode=0o700, exist_ok=True)
    try:
        directory.mkdir(mode=0o700)
    except FileExistsError:
        raise ValueError('已有生命周期锁；先核验原操作，不自动抢锁或清理')
    try:
        for name, text in (('owner', str(os.getpid())), ('operation', mode)):
            path = directory / name
            path.write_text(text + '\n'); path.chmod(0o600)
        yield
    finally:
        for name in ('owner', 'operation'):
            (directory / name).unlink(missing_ok=True)
        directory.rmdir()


def clean(root):
    if git(root, 'status', '--porcelain', '--untracked-files=all'):
        raise ValueError('安装目录存在未提交修改，拒绝更新；先用 git status 核对并保全修改，处理完后重试原命令；更新器不会自动 stash 或覆盖文件')
    for row in git(root, 'ls-files', '-v').splitlines():
        if row and row[0].islower():
            raise ValueError('安装目录使用 assume-unchanged 隐藏受管文件，拒绝更新')


def ancestor(root, earlier, later):
    result = subprocess.run(['git', '-C', str(root), 'merge-base', '--is-ancestor', earlier, later],
                            capture_output=True, env=git_environment(), timeout=30)
    if result.returncode:
        raise ValueError('目标不可向前到达、越出来源分支或历史不足，拒绝切换（待核验祖先=%s，后继=%s）；核对记录的来源分支与完整 SHA，补齐历史后重试。若维护补丁已偏离原通道，保留旧安装，按更新与回退指引处理；不要 reset 或强制合并' % (earlier, later))


def install_paths(root, target):
    paths = list(INSTALL_PATHS)
    shared = git(root, 'ls-tree', target, '--', 'skills/shared')
    if shared:
        if not shared.startswith('040000 tree '):
            raise ValueError('目标共享 Skill 根不是目录')
        paths.append('skills/shared')
    return paths


def refresh_scope(root, target):
    git(root, 'sparse-checkout', 'set', *install_paths(root, target))


def missing_blobs(root, objects):
    if not objects:
        return []
    rows = git(root, 'cat-file', '--batch-check', input_text='\n'.join(objects) + '\n').splitlines()
    if len(rows) != len(objects):
        raise ValueError('本地对象核验回读数量不一致')
    missing = []
    for reference, row in zip(objects, rows):
        parts = row.split()
        if parts == [reference, 'missing']:
            missing.append(reference)
        elif len(parts) != 3 or parts[0] != reference or parts[1] != 'blob' or not parts[2].isdigit():
            raise ValueError('本地文件对象核验回读无效：%s' % reference)
    return missing


def prepare_materials(root, original, target):
    """只补对象库；不检出、不放宽核验环境、不修改版本记录或工位。"""
    # cone 模式同时检出根文件；范围扩展会先作用于当前版本，因此两端都必须准备。
    # epoch 是端点状态兼容合同，不读取或下载中间提交的兼容清单。
    # 不下载 tests/internal/维护 Skill 等排除目录；显式 fetch 仍受 NO_LAZY_FETCH=1 约束。
    objects = set()
    # 目标范围可能新纳入当前版本已有的祖先文件，需以两端范围的并集准备两端对象。
    directories = set(install_paths(root, original)) | set(install_paths(root, target))
    for reference in set((original, target)):
        for row in git(root, 'ls-tree', '-r', '-z', reference).split('\0'):
            if not row:
                continue
            metadata, path = row.split('\t', 1)
            _, kind, object_ref = metadata.split()
            # cone 的祖先目录文件也会检出，例如选择 skills/shared 时的 skills/README.md。
            if kind == 'blob' and ('/' not in path or any(path.startswith(directory + '/')
                    or '/' in directory and path.rsplit('/', 1)[0] == directory.rsplit('/', 1)[0]
                    for directory in directories)):
                objects.add(exact(object_ref))
    objects = sorted(objects)
    missing = missing_blobs(root, objects)
    if missing:
        print('[AgenticOps][更新][进行] 补齐版本材料（缺失文件对象=%s）' % len(missing), file=sys.stderr)
        # 来源与提交归属已由 prepare 核验；只向该 origin 请求从 Git 树读取的完整对象 SHA。
        # stdin 避免大安装的命令行长度限制；不改远端过滤器，也不自动重试其它远端。
        try:
            git(root, 'fetch', '--no-tags', '--no-write-fetch-head', '--no-recurse-submodules',
                'origin', '--stdin', input_text='\n'.join(missing) + '\n')
        except ValueError as error:
            raise ValueError('版本材料补齐失败，本次未执行版本切换；检查来源远端与连接后重试原命令。\n%s' % error) from error
        if missing_blobs(root, objects):
            raise ValueError('版本材料补齐后仍缺少文件对象，本次未执行版本切换；保留现场并检查来源远端')


def prepare(root, args):
    document = product_state.load(root)
    if document['mode'] != 'installed':
        raise ValueError('定点、预检和恢复只适用于安装目录；源码使用原 update')
    clean(root)
    if git(root, 'remote', 'get-url', 'origin') != document['repository']:
        raise ValueError('origin 与产品记录不一致；核对 .local/product.json 的 repository 与 git remote get-url origin，确认可信来源后再修复配置，不要直接覆盖产品文件')
    original = exact(args.expected_current_ref or document['current_ref'])
    if args.to_ref and not args.expected_current_ref:
        raise ValueError('--to-ref 必须同时绑定 --expected-current-ref')
    if args.resume and (not args.to_ref or not args.expected_current_ref):
        raise ValueError('--resume 必须绑定原/目标完整 SHA')
    branch = args.source_branch if args.mode == 'recover-install' else document['tracking_branch']
    if args.mode == 'recover-install' and (not branch or not args.to_ref or not args.expected_current_ref):
        raise ValueError('恢复须明确维护分支、原 SHA 和补丁 SHA')
    if args.mode != 'recover-install' and args.source_branch:
        raise ValueError('普通 update 不允许换通道')
    git(root, 'check-ref-format', '--branch', branch)
    print('[AgenticOps][更新][进行] 获取来源分支', file=sys.stderr)
    git(root, 'fetch', 'origin', 'refs/heads/%s:refs/remotes/origin/%s' % (branch, branch))
    tip = git(root, 'rev-parse', 'refs/remotes/origin/' + branch)
    target = exact(args.to_ref or tip)
    git(root, 'cat-file', '-e', target + '^{commit}')
    ancestor(root, original, target)
    ancestor(root, target, tip)
    # 材料下载前先核验安装身份及原操作；恢复不得为第三个 HEAD 补材料后再猜测续接。
    head = git(root, 'rev-parse', 'HEAD')
    current = document['current_ref']
    marker = 'agenticops-update:%s:%s:%s:%s' % (args.mode, branch, original, target)
    if not args.resume:
        if head != original or current != original:
            raise ValueError('安装 HEAD/产品记录与原 SHA 不符（HEAD=%s，记录=%s，预期=%s）；先核对原更新结果。若为中断，只按原模式、来源及原/目标完整 SHA 加 --resume 续接；不要重写版本记录' % (head, current, original))
    else:
        if (head, current) not in ((original, original), (target, original), (target, target)):
            raise ValueError('续接出现第三值或非法 HEAD/记录组合')
        if target != original and head == target:
            rows = git(root, 'reflog', 'show', '--format=%H%x00%gs', '-n', '2', 'HEAD').splitlines()
            if (len(rows) != 2 or rows[0].split('\0')[0] != target or rows[1].split('\0')[0] != original
                    or marker not in rows[0]):
                raise ValueError('缺少同模式、同来源的原 SHA→目标 SHA Git 切换事实')
        if head == target and current == target and target != original and document['previous_ref'] != original:
            raise ValueError('续接 previous_ref 不属于本次操作')
    prepare_materials(root, original, target)
    print('[AgenticOps][更新][进行] 核验版本兼容性与切换条件', file=sys.stderr)
    before = station_compatibility.manifest_at_ref(root, original)['station_state_epoch']
    target_manifest = station_compatibility.manifest_at_ref(root, target)
    after = target_manifest['station_state_epoch']
    if getattr(args, 'automatic', False):
        if (before != after and target_manifest.get('validation') != 'feature') or target != tip:
            raise ValueError('自动更新目标已变化或目标不支持当前兼容模式；重新检查，不沿用本次选择')
        if not args.writers_stopped:
            raise ValueError('自动更新无法证明未受管会话停写')
        automatic_protocol(root, original)
        automatic_protocol(root, target)
    if args.mode == 'recover-install' and before != after:
        raise ValueError('原版本维护恢复必须同 epoch（当前=%s，目标=%s）；请选择与原安装同 epoch 的维护补丁。跨 epoch 升级请使用 update，按目标兼容模式核实在途操作和停写；旧模式目标才要求退出并 purge' % (before, after))
    station_compatibility.check_upgrade(root, original, target)
    stations = station_compatibility.load_station_registry(root, required=args.mode == 'recover-install' or before != after)
    if getattr(args, 'automatic', False):
        stations = station_compatibility.load_station_registry(root, required=True)
        if json.loads(args.expected_stations or 'null') != stations:
            raise ValueError('检查后登记工位范围变化；重新检查，不扩大自动更新范围')
        from workflow import station_context
        for station in stations:
            binding = station_context.read_binding(station)
            if Path(binding['product_root']).resolve() != root:
                raise ValueError('登记工位绑定漂移，拒绝自动更新')
            station_context.require_epoch(station, root)
    if any(not Path(station).is_absolute() for station in stations):
        raise ValueError('工位登记路径无效，不能确认恢复范围')
    explicit = bool(args.to_ref or args.mode == 'recover-install' or before != after)
    if target != original and explicit and stations and not args.writers_stopped and not args.check:
        raise ValueError('共享安装仍有工位；须核验全部产品调用已停写并声明 --writers-stopped')
    return document, {'status': 'check' if args.check else 'prepared', 'product_root': str(root),
        'mode': args.mode, 'source_branch': branch, 'tracking_branch': document['tracking_branch'],
        'original_ref': original, 'target_ref': target, 'original_epoch': before, 'target_epoch': after,
        'stations': stations, 'head': head, 'writers_stopped_required': explicit and bool(stations) and target != original,
        'reflog_action': marker}


def probe(root):
    """发现版本与切换资格分开；不会执行目标代码或改变安装范围。"""
    packet = {'status': 'invalid_local_state', 'product_root': str(root),
              'checked_at': datetime.now(timezone.utc).isoformat(), 'blockers': [], 'compatible': None}
    try:
        document = product_state.load(root)
        if document['mode'] != 'installed':
            raise ValueError('新任务更新检查只适用于安装目录')
        clean(root)
        original = exact(document['current_ref'])
        if git(root, 'rev-parse', 'HEAD') != original:
            raise ValueError('HEAD 与产品记录不一致，须先恢复原操作')
        if git(root, 'remote', 'get-url', 'origin') != document['repository']:
            raise ValueError('origin 与安装来源记录不一致')
        branch = document['tracking_branch']
        git(root, 'check-ref-format', '--branch', branch)
        stations = station_compatibility.load_station_registry(root, required=True)
        if any(not Path(path).is_absolute() for path in stations):
            raise ValueError('工位登记路径无效')
        packet.update(original_ref=original, source_branch=branch, repository=document['repository'], stations=stations)
    except (ValueError, OSError) as error:
        return dict(packet, reason=str(error))
    try:
        git(root, 'fetch', 'origin', 'refs/heads/%s:refs/remotes/origin/%s' % (branch, branch))
        target = exact(git(root, 'rev-parse', 'refs/remotes/origin/' + branch))
        packet['target_ref'] = target
    except (ValueError, OSError) as error:
        return dict(packet, status='check_unavailable', reason=str(error))
    try:
        ancestor(root, original, target)
        prepare_materials(root, original, target)
        before = station_compatibility.manifest_at_ref(root, original)['station_state_epoch']
        target_manifest = station_compatibility.manifest_at_ref(root, target)
        after = target_manifest['station_state_epoch']
        packet.update(original_epoch=before, target_epoch=after,
                      compatible=before == after or target_manifest.get('validation') == 'feature')
    except (ValueError, OSError) as error:
        return dict(packet, status='target_unknown', reason=str(error))
    if not packet['compatible']:
        packet['blockers'].append('incompatible_epoch')
    if stations:
        packet['blockers'].append('unmanaged_sessions_unverified')
    packet['status'] = 'up_to_date' if original == target else 'update_available'
    return packet


def automatic_protocol(root, reference):
    """受信版本仍须声明入口屏障；旧版首次接入/回退不能推断受保护。"""
    paths = git(root, 'ls-tree', '-r', '--name-only', reference).splitlines()
    if 'bootstrap/product_access.py' not in paths:
        raise ValueError('版本没有产品租约入口；首次采用须手工停写更新')
    for path in paths:
        if path.endswith('.py') and path.startswith(('bootstrap/', 'workflow/', 'gate/', 'projects/')) and '/tests/' not in path:
            source = git(root, 'show', reference + ':' + path)
            if '__main__' in source and path not in ('bootstrap/product_access.py', 'bootstrap/python_runtime.py'):
                if '_AO_PRODUCT_ENTRY' not in source or 'python_entry' not in source:
                    raise ValueError('版本入口尚未接入租约：' + path)
    for path in ('agenticops', 'bootstrap/update.sh', 'bootstrap/rollback.sh', 'bootstrap/setup.sh', 'bootstrap/station-init.sh'):
        if 'AO_PRODUCT_SHELL_BEGIN' not in git(root, 'show', reference + ':' + path):
            raise ValueError('版本 shell 入口尚未接入租约：' + path)


def execute(root, args):
    with product_access.lease(root, 'shared' if args.check else 'exclusive'), lifecycle_lock(root, args.mode):
        document, packet = prepare(root, args)
        # 只为声明了本运行环境能力的目标准备依赖，保留旧目标恢复语义。
        needs_python = bool(git(root, 'ls-tree', packet['target_ref'], '--', 'bootstrap/python_runtime.py'))
        if needs_python:
            python_runtime.select(root)
        if args.check:
            print('[AgenticOps][更新][完成] 预检通过，本次未执行版本切换', file=sys.stderr)
            return packet
        original, target = packet['original_ref'], packet['target_ref']
        if needs_python:
            python_runtime.prepare(root)
        if product_access.inherited(root, 'exclusive') is None:
            raise ValueError('版本切换前产品独占租约丢失')
        print('[AgenticOps][更新][进行] 准备安装范围并切换产品版本', file=sys.stderr)
        refresh_scope(root, target)
        if packet['head'] != target:
            if args.mode == 'recover-install':
                git(root, 'checkout', '--detach', target, action=packet['reflog_action'])
            else:
                # 默认 diffstat 会读取排除目录的 blob；安装切换只需已准备的安装范围。
                # 禁止输出整仓统计，避免边缘显示功能迫使部分克隆下载维护源码。
                git(root, 'merge', '--ff-only', '--no-stat', target, action=packet['reflog_action'])
        if git(root, 'rev-parse', 'HEAD') != target:
            raise ValueError('切换后的实际 HEAD 未核验')
        if target != original and document['current_ref'] != target:
            product_state.save(root, dict(document, current_ref=target, previous_ref=original))
        readback = product_state.load(root)
        if (readback['current_ref'] != target or readback['tracking_branch'] != document['tracking_branch']
                or target != original and readback['previous_ref'] != original):
            raise ValueError('产品版本记录回读不一致')
        # 只提示接线版本漂移；不采用、迁移或写入旧任务。
        if target != original:
            subprocess.run([sys.executable, str(ROOT / 'bootstrap/station_registry.py'), '--product-root', str(root),
                            'pending', '--product-ref', target], check=True, env=product_access.child_environment(root, git_environment()),
                           pass_fds=product_access.child_fds(root))
        packet['status'] = 'updated' if target != original else 'unchanged'
        packet['boundary'] = '仅产品安装切换；任务归档、释放、注销与新版接管须分别核验'
        print('[AgenticOps][更新][完成] %s：分支=%s，%s → %s' %
              ('安装目录已更新' if target != original else '已核验当前版本并补齐安装范围',
               document['tracking_branch'], original, target))
        return packet


def parser():
    p = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    p.add_argument('--product-root', required=True)
    p.add_argument('--mode', choices=('update', 'recover-install'), default='update')
    p.add_argument('--to-ref')
    p.add_argument('--expected-current-ref')
    p.add_argument('--source-branch')
    p.add_argument('--check', action='store_true')
    p.add_argument('--probe', action='store_true', help='配合 --check，分别报告新版、兼容性和切换阻塞')
    p.add_argument('--automatic', action='store_true', help='启动侧兼容目标自动更新；仍需停写事实与独占租约')
    p.add_argument('--expected-stations', help='启动侧绑定检查时的登记工位 JSON 数组')
    p.add_argument('--resume', action='store_true')
    p.add_argument('--writers-stopped', action='store_true', help='已按原生事实核验全部共享产品调用停写；不认证声明者')
    return p


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        if args.probe:
            if not args.check or args.resume or args.to_ref or args.mode != 'update':
                raise ValueError('--probe 只能用于普通 update --check')
            with product_access.lease(args.product_root):
                result = probe(Path(args.product_root).resolve())
        else:
            result = execute(Path(args.product_root).resolve(), args)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        print('[AgenticOps][更新][失败] %s（退出码=2）' % error, file=sys.stderr)
        print('[AgenticOps][更新][处理] 安装目录=%s；模式=%s。保留安装与工位，按上述原因处理；操作指引：docs/usage/update-and-rollback.md' %
              (Path(args.product_root).resolve(), args.mode), file=sys.stderr)
        # 只回读，不把异常退出等同于回退；可能已切换 HEAD 但尚未写完产品记录。
        try:
            document = product_state.load(args.product_root)
            head = git(args.product_root, 'rev-parse', 'HEAD')
            print('[AgenticOps][更新][现场] HEAD=%s；产品记录=%s；跟踪分支=%s' %
                  (head, document['current_ref'], document['tracking_branch']), file=sys.stderr)
            if head != document['current_ref'] or args.resume:
                print('[AgenticOps][更新][处理] 可能存在未完成切换；先核对原操作，使用原模式、来源及原/目标完整 SHA 加 --resume 续接，不要直接重试无参 update。', file=sys.stderr)
            else:
                print('[AgenticOps][更新][处理] HEAD 与产品记录一致；此回读不代表更新成功或已回退。切换前拒绝可在消除原因后重试原命令；切换中断须先核验原操作。', file=sys.stderr)
        except (ValueError, OSError, subprocess.SubprocessError):
            print('[AgenticOps][更新][处理] 无法核验安装版本；保留现场，请维护者核对 HEAD、产品记录与原操作后再决定恢复方式。', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
