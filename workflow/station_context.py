"""工位绑定的只读访问与合同判定；不迁移状态，不编排升级或任务。"""
import json
from pathlib import Path
from workflow import quality_contract

ROOT = Path(__file__).resolve().parents[1]


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("JSON 包含重复键")
        result[key] = value
    return result


def read_object(path, label):
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"), object_pairs_hook=_unique_object)
    except (OSError, UnicodeError, ValueError) as error:
        raise ValueError("%s无法读取：%s（%s）" % (label, path, error)) from error
    if not isinstance(value, dict):
        raise ValueError("%s 无效，顶层必须是对象：%s" % (label, path))
    return value


def schema_version():
    return read_object(ROOT / "contracts/station.schema.json", "工位合同")["properties"]["schema_version"]["const"]


def validate_binding(document):
    try:
        quality_contract.validate(document, "station.schema.json")
    except ValueError as error:
        raise ValueError("工位绑定格式错误：%s" % error) from error
    return document


def read_binding(station):
    station = Path(station).resolve()
    state = station / ".agenticops"
    path = state / "station.json"
    if state.is_symlink() or path.is_symlink():
        raise ValueError("工位状态目录与绑定不能是符号链接")
    return validate_binding(read_object(path, "工位绑定 station.json"))


def find_binding(start):
    current = Path(start).resolve()
    for directory in (current, *current.parents):
        path = directory / ".agenticops/station.json"
        if path.exists() or path.is_symlink():
            return directory, path, read_binding(directory)
    return None, None, None


def validate_manifest(document):
    quality_contract.validate(document, "station-state-compatibility.schema.json")
    return document


def require_epoch(station, product_root, target=None):
    target = validate_manifest(target if target is not None else read_object(
        Path(product_root) / "contracts/station-state-compatibility.json", "工位兼容性清单"))
    init = read_object(Path(station) / ".agenticops/init.json", "工位初始化标记")
    epoch = init.get("station_state_epoch")
    if type(epoch) is not int or epoch != target["station_state_epoch"]:
        raise ValueError("工位状态代际 %s 与当前产品不兼容；请使用原版本保存材料、受控解绑并重建；repair 不执行跨代际采用" % epoch)
    return epoch
