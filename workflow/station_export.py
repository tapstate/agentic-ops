"""向用户指定的工位外私有目录导出单份源码成果，并回读后登记保存决定。"""
from __future__ import annotations

import argparse
import copy
import json
import os
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from workflow import engineering_baseline as baseline, station_artifacts as artifacts, task_store


def receipt_path(base, task, relative, proof, destination):
    identifier = baseline.digest({"source": relative, "snapshot": proof, "target": str(destination)})
    if task.get('archive_ref'):
        return Path(base).resolve() / task['archive_ref']['path'] / 'receipts' / ('export-' + identifier + '.json')
    return task_store.task_directory(base, task['issue_key']) / ('export-' + identifier + '.json')


def verify_receipt(base, task, entry):
    choice = entry['preservation']
    expected_path = receipt_path(base, task, entry['path'], choice['snapshot'], choice['path'])
    active_path = task_store.task_directory(base, task['issue_key']) / expected_path.name
    path = Path(base).resolve() / choice.get('readback_ref', '')
    if path not in (expected_path, active_path) or path.parent.is_symlink() or path.is_symlink() or not path.is_file():
        raise ValueError('源码导出缺少受控导出回执')
    receipt = json.loads(path.read_text())
    choice = entry['preservation']
    expected = {key: choice[key] for key in ('path', 'sha256', 'snapshot', 'readback_ref')}
    if receipt.get('run_id') != task['run_id'] or receipt.get('source') != entry['path'] or receipt.get('result') != expected:
        raise ValueError('源码导出回执与当前决定不一致')


def export(base, issue, run, relative, destination, expected_operation_id=None):
    from workflow import station_resources as resources
    with task_store.task_state_lock(base):
        task = task_store.check_expected_run(base, issue, run)
        closed, recovery = resources.registration_state(base, task, expected_operation_id)
        if closed and not recovery:
            raise ValueError('归档后导出必须绑定当前退出操作编号')
        root = Path(base).resolve()
        target = Path(destination)
        if not target.is_absolute() or target.is_symlink() or root == target or root in target.resolve().parents:
            raise ValueError('导出目的必须是工位外的绝对普通文件路径')
        for parent in target.parents:
            if parent.is_symlink():
                raise ValueError('导出祖先不能是链接')
        info = target.parent.stat()
        if not target.parent.is_dir() or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise ValueError('导出父目录须由当前用户持有且权限为 0700')
        plan = resources.plan(base, task, decisions_override={relative: {"action": "archive"}})
        entry = next((item for item in plan['entries'] if item['path'] == relative), None)
        if entry is None:
            raise ValueError('导出路径不属于当前源码成果')
        proof = {key: entry[key] for key in ('head', 'before', 'before_index', 'index_patch', 'worktree_patch')}
        selected = copy.deepcopy(entry)
        selected['preservation'] = {'action': 'archive'}
        scope = dict(plan, entries=[selected], source={entry['repository']: plan['source'][entry['repository']]})
        bundle = json.loads(artifacts.material(base, task, scope, private_export=True))['repositories'][entry['repository']]
        payload = {'path': relative, 'snapshot': proof, 'bundle': bundle}
        data = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()
        if len(data) > 768 * 1024 * 1024:
            raise ValueError('导出文件超过 768 MiB 上限，需由原生工具保存后人工处置')
        checksum = artifacts.digest(data)
        path = receipt_path(base, task, relative, proof, target)
        intent_path = path.with_suffix('.intent.json')
        intent = {'run_id': run, 'source': relative, 'target': str(target), 'sha256': checksum, 'snapshot': proof, 'archive_digest': task.get('archive_ref', {}).get('digest') if task.get('archive_ref') else None, 'operation_id': expected_operation_id}
        if intent_path.is_symlink() or path.is_symlink():
            raise ValueError('导出回执不能是链接')
        if intent_path.exists():
            if json.loads(intent_path.read_text()) != intent:
                raise ValueError('已有导出意图发生变化，保留原材料并停止')
        else:
            task_store._write_json_atomic(intent_path, intent)
        if not target.exists():
            with tempfile.NamedTemporaryFile(dir=str(target.parent), prefix='.ao-export-', delete=False) as stream:
                temporary = Path(stream.name)
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            try:
                os.link(str(temporary), str(target))  # 独占发布，不覆盖任何已有目的文件。
            except FileExistsError:
                pass
            finally:
                temporary.unlink()
            from workflow.station_directories import sync
            sync(target.parent)
        info = target.lstat()
        import stat
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077 or info.st_size != len(data):
            raise ValueError('导出目的文件身份或权限异常')
        if artifacts.digest(target.read_bytes()) != checksum:
            raise ValueError('导出文件回读失败，拒绝登记保存决定')
        # 写出期间源码有任何变化时，不把刚生成的旧成果当成当前成果。
        if resources.plan(base, task, decisions_override={relative: {'action': 'archive'}})['entries'] != plan['entries']:
            raise ValueError('导出期间源码变化，请保留导出并重新核对')
        choice = {'action': 'export', 'path': str(target), 'sha256': checksum, 'snapshot': proof,
                  'readback_ref': str(path.relative_to(root))}
        receipt = {'run_id': run, 'source': relative, 'archive_digest': task.get('archive_ref', {}).get('digest') if task.get('archive_ref') else None, 'operation_id': expected_operation_id, 'result': {k: v for k, v in choice.items() if k != 'action'}}
        if path.exists() and json.loads(path.read_text()) != receipt:
            raise ValueError('导出回执不可覆盖')
        task_store._write_json_atomic(path, receipt)
        resources.register(base, issue, run, [{'kind': 'source-disposition', 'producer': 'workflow-export',
                                             'path': relative, 'preservation': choice}], expected_operation_id)
        return {'source': relative, 'path': str(target), 'sha256': checksum, 'readback_ref': choice['readback_ref']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dir', required=True)
    parser.add_argument('--issue-key', required=True)
    parser.add_argument('--expected-run-id', required=True)
    parser.add_argument('--path', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--expected-operation-id')
    args = parser.parse_args()
    try:
        print(json.dumps(export(args.dir, args.issue_key, args.expected_run_id, args.path, args.output, args.expected_operation_id), ensure_ascii=False))
        return 0
    except (OSError, ValueError) as error:
        print('错误：%s' % error, file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
