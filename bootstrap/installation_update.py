#!/usr/bin/env python3
"""安装版本定点切换与有限续接；只读取产品配置、兼容清单和工位索引。"""
import argparse
from contextlib import contextmanager
import json
import os
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from bootstrap import product_state, station_compatibility
from workflow.git_environment import git_environment

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
        raise ValueError('安装目录存在未提交修改，拒绝更新')
    for row in git(root, 'ls-files', '-v').splitlines():
        if row and row[0].islower():
            raise ValueError('安装目录使用 assume-unchanged 隐藏受管文件，拒绝更新')


def ancestor(root, earlier, later):
    result = subprocess.run(['git', '-C', str(root), 'merge-base', '--is-ancestor', earlier, later],
                            capture_output=True, env=git_environment(), timeout=30)
    if result.returncode:
        raise ValueError('目标不可向前到达、越出来源分支或历史不足，拒绝切换')


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
    # 中间提交的兼容清单也可能是独有 blob，不能只补齐两端后声称连续兼容。
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
    history = git(root, 'rev-list', '--first-parent', target, '^' + original).splitlines()
    for reference in history + [original]:
        row = git(root, 'ls-tree', reference, '--', station_compatibility.MANIFEST_PATH)
        if row:
            metadata, _ = row.split('\t', 1)
            _, kind, object_ref = metadata.split()
            if kind == 'blob':
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
        raise ValueError('origin 与产品记录不一致')
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
            raise ValueError('安装 HEAD/产品记录与原 SHA 不符；不自动恢复')
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
    after = station_compatibility.manifest_at_ref(root, target)['station_state_epoch']
    if args.mode == 'recover-install' and before != after:
        raise ValueError('原版本维护恢复必须同 epoch')
    if before == after:
        mainline = git(root, 'rev-list', '--first-parent', target).splitlines()
        if original not in mainline:
            raise ValueError('原安装不在恢复主线上，不能证明连续兼容')
        for reference in mainline[:mainline.index(original) + 1]:
            if station_compatibility.manifest_at_ref(root, reference)['station_state_epoch'] != before:
                raise ValueError('恢复路径经过不同 epoch，不能只核验端点')
    station_compatibility.check_upgrade(root, original, target)
    stations = station_compatibility.load_station_registry(root, required=args.mode == 'recover-install' or before != after)
    if any(not Path(station).is_absolute() for station in stations):
        raise ValueError('工位登记路径无效，不能确认恢复范围')
    explicit = bool(args.to_ref or args.mode == 'recover-install')
    if target != original and explicit and stations and not args.writers_stopped and not args.check:
        raise ValueError('共享安装仍有工位；须核验全部产品调用已停写并声明 --writers-stopped')
    return document, {'status': 'check' if args.check else 'prepared', 'product_root': str(root),
        'mode': args.mode, 'source_branch': branch, 'tracking_branch': document['tracking_branch'],
        'original_ref': original, 'target_ref': target, 'original_epoch': before, 'target_epoch': after,
        'stations': stations, 'head': head, 'writers_stopped_required': explicit and bool(stations) and target != original,
        'reflog_action': marker}


def execute(root, args):
    with lifecycle_lock(root, args.mode):
        document, packet = prepare(root, args)
        if args.check:
            print('[AgenticOps][更新][完成] 预检通过，本次未执行版本切换', file=sys.stderr)
            return packet
        original, target = packet['original_ref'], packet['target_ref']
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
                            'pending', '--product-ref', target], check=True, env=git_environment())
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
    p.add_argument('--resume', action='store_true')
    p.add_argument('--writers-stopped', action='store_true', help='已按原生事实核验全部共享产品调用停写；不认证声明者')
    return p


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        result = execute(Path(args.product_root).resolve(), args)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        print('AgenticOps：%s' % error, file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
