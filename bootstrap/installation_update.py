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


def git(root, *arguments, action=None):
    env = git_environment()
    if action:
        env['GIT_REFLOG_ACTION'] = action
    result = subprocess.run(['git', '-C', str(root), *arguments], capture_output=True, text=True,
                            env=env, timeout=120)
    if result.returncode:
        raise ValueError('Git 生命周期核验失败：%s：%s' % (arguments[0], result.stderr.strip()))
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


def refresh_scope(root, target):
    paths = ['adapters', 'bootstrap', 'contracts', 'docs', 'gate', 'policies', 'projects', 'workflow']
    shared = git(root, 'ls-tree', target, '--', 'skills/shared')
    if shared:
        if not shared.startswith('040000 tree '):
            raise ValueError('目标共享 Skill 根不是目录')
        paths.append('skills/shared')
    git(root, 'sparse-checkout', 'set', *paths)


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
    git(root, 'fetch', 'origin', 'refs/heads/%s:refs/remotes/origin/%s' % (branch, branch))
    tip = git(root, 'rev-parse', 'refs/remotes/origin/' + branch)
    target = exact(args.to_ref or tip)
    git(root, 'cat-file', '-e', target + '^{commit}')
    ancestor(root, original, target)
    ancestor(root, target, tip)
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
            return packet
        original, target = packet['original_ref'], packet['target_ref']
        refresh_scope(root, target)
        if packet['head'] != target:
            if args.mode == 'recover-install':
                git(root, 'checkout', '--detach', target, action=packet['reflog_action'])
            else:
                git(root, 'merge', '--ff-only', target, action=packet['reflog_action'])
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
        print('AgenticOps 安装目录已更新：branch=%s，%s -> %s' % (document['tracking_branch'], original, target))
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
