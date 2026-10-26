"""新任务/会话启动前的只读工位核验和版本探测；不创建 run。"""
if __name__ == '__main__' and not globals().get('_AO_PRODUCT_ENTRY'):
    from pathlib import Path as _AoPath
    _ao_loader = _AoPath(__file__).resolve().parents[1] / "bootstrap/product_access.py"
    _ao_namespace = {"__file__": str(_ao_loader), "_AO_LOADER_SOURCE": _ao_loader.read_bytes()}
    exec(compile(_ao_namespace["_AO_LOADER_SOURCE"], str(_ao_loader), "exec"), _ao_namespace)
    raise SystemExit(_ao_namespace["python_entry"](__file__))

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from bootstrap import installation_update, product_state
from workflow import station_context, station_operation, task_store


def inspect(station, current_ref=None, target_ref=None):
    station = Path(station).resolve()
    binding = station_context.read_binding(station)
    if Path(binding['product_root']).resolve() != ROOT:
        raise ValueError('入口产品根与工位绑定不一致')
    station_context.require_epoch(station, ROOT)
    with task_store.task_state_lock(station, execution=False):
        current = task_store.read_current(station)
        operation = station_operation.read(station)
        if current['current'] is not None or operation and operation['status'] != 'done':
            return {'status': 'resume', 'product_root': str(ROOT)}
    document = product_state.load(ROOT)
    if document['mode'] == 'source':
        return {'status': 'source', 'product_root': str(ROOT)}
    packet = installation_update.probe(ROOT)
    packet['station'] = str(station)
    packet['station_id'] = binding['station_id']
    if packet['status'] != 'invalid_local_state' and str(station) not in packet.get('stations', []):
        return dict(packet, status='invalid_local_state', reason='当前工位未登记，不能确认共享安装范围')
    if current_ref:
        installation_update.exact(current_ref)
        installation_update.exact(target_ref)
        if packet.get('original_ref') != current_ref:
            raise ValueError('继续旧版的选择与实际安装 SHA 不符，请重新核验')
        if packet.get('target_ref') != target_ref:
            raise ValueError('检查目标已变化或无法确认，请重新展示更新结果')
        if packet['status'] != 'invalid_local_state':
            packet['status'] = 'continue_current'
    return packet


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--station', required=True)
    parser.add_argument('--current-ref')
    parser.add_argument('--target-ref')
    args = parser.parse_args()
    print(json.dumps(inspect(args.station, args.current_ref, args.target_ref), ensure_ascii=False))


if __name__ == '__main__':
    main()
