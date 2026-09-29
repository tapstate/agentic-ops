"""仅用于已批准的 epoch 25 空 runtime 恢复；不随产品安装。"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

SUPPORTED = '16b76424c2f20ec737d29d23aec830a4ecdd1eaf'


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def candidate(before, parent, current):
    """只允许父子 inode 均不变、父子同设备且仅设备号变化。"""
    after = json.loads(json.dumps(before))
    if set(after['roots']) != {'runtime'}:
        raise ValueError('仅支持唯一登记的 runtime')
    entry = after['roots']['runtime']
    old_parent, old = entry['parent'], entry['identity']
    if (not old or old_parent['inode'] != parent['inode'] or old['inode'] != current['inode']
            or old_parent['device'] != old['device'] or parent['device'] != current['device']
            or old['device'] == current['device']):
        raise ValueError('仅允许空 runtime 的父子 inode 不变且设备号变化')
    entry['parent'], entry['identity'] = parent, current
    return after


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--product-root', required=True)
    parser.add_argument('--station', required=True)
    parser.add_argument('--request', required=True)
    parser.add_argument('--confirm-digest')
    parser.add_argument('--decision-ref')
    parser.add_argument('--writers-stopped', action='store_true')
    args = parser.parse_args()
    product, base, request = (Path(x).absolute() for x in (args.product_root, args.station, args.request))
    if product != product.resolve() or base != base.resolve() or request != request.resolve():
        raise ValueError('路径不能经过链接')
    if base == request or base in request.parents or product == request or product in request.parents:
        raise ValueError('恢复请求必须在工位及产品根外')
    head = subprocess.check_output(['git', '-C', str(product), 'rev-parse', 'HEAD'], text=True).strip()
    dirty = subprocess.check_output(['git', '-C', str(product), 'status', '--porcelain', '--untracked-files=no'], text=True).strip()
    if head != SUPPORTED or dirty:
        raise ValueError('仅支持未修改的原安装提交 16b7642')
    sys.path.insert(0, str(product))
    from workflow import task_store as store, station_directories as directories, station_operation
    from workflow import project_rules, station_resources
    from bootstrap.station_compatibility import require_station_can_adopt
    if project_rules.product_root_from_station(base).resolve() != product:
        raise ValueError('工位绑定的产品不一致')
    if json.loads((product / 'contracts/station-state-compatibility.json').read_text())['station_state_epoch'] != 25:
        raise ValueError('仅支持 epoch 25')
    require_station_can_adopt(product, base)
    with store.task_state_lock(base):
        task = store.read_task(base)
        operation = station_operation.read(base)
        if not task or task.get('archive_ref') or (operation and operation['status'] != 'done'):
            raise ValueError('须为未归档任务，且没有未完成操作')
        station_resources.verify_stopped(base, task)
        station_resources.verify_known_external(base, task)
        roots = directories.load(base, task)
        path = directories.path_at(base, 'runtime')
        if any(path.iterdir()):
            raise ValueError('runtime 非空，拒绝恢复')
        parent, current = directories.identity(path.parent), directories.identity(path)
        registry = directories.registry_path(base, task)
        before = json.loads(registry.read_text())
        binding = {'product_head': head, 'station': str(base), 'run_id': task['run_id'],
                   'task_digest': digest(task), 'operation_digest': digest(operation)}
        if not args.confirm_digest:
            after = candidate(before, parent, current)
            payload = dict(binding, before=before, after=after)
            payload['digest'] = digest(payload)
            fd = os.open(str(request), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, 'w') as stream:
                json.dump(payload, stream, ensure_ascii=False, indent=2)
                stream.flush()
                os.fsync(stream.fileno())
            print(json.dumps({'digest': payload['digest'], 'run_id': task['run_id'],
                              'old': roots['runtime']['identity'], 'new': current}, ensure_ascii=False))
            return
        if not args.writers_stopped or not args.decision_ref:
            raise ValueError('须明确停写与用户决定来源')
        payload = json.loads(request.read_text())
        expected_digest = payload.pop('digest')
        if expected_digest != args.confirm_digest or digest(payload) != expected_digest:
            raise ValueError('确认摘要不匹配')
        if any(payload.get(k) != v for k, v in binding.items()):
            raise ValueError('任务或操作已变化')
        if candidate(payload['before'], parent, current) != payload['after']:
            raise ValueError('目录身份再次变化')
        if before not in (payload['before'], payload['after']):
            raise ValueError('目录登记已变化')
        receipt = request.with_name(request.name + '.receipt.json')
        record = {'digest': expected_digest, 'decision_ref': args.decision_ref, 'run_id': task['run_id']}
        if receipt.is_symlink() or (receipt.exists() and json.loads(receipt.read_text()) != record):
            raise ValueError('恢复回执冲突')
        # 每次写前复核；相同请求在登记已更新而回执未写时也可收敛。
        if any(path.iterdir()) or directories.identity(path) != current or directories.identity(path.parent) != parent:
            raise ValueError('恢复前目录变化')
        if before != payload['after']:
            store._write_json_atomic(registry, payload['after'])
        directories.validate(base, directories.load(base, task)['runtime'])
        store._write_json_atomic(receipt, record)
        print(json.dumps({'status': 'recovered', 'run_id': task['run_id'], 'receipt': str(receipt)}))


if __name__ == '__main__':
    main()
