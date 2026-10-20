"""重置后分支/PR 原生处置的追加回执；不执行 GitHub 操作，不占用工位。"""
from __future__ import annotations

# 产品入口：先取得租约，再重新加载本版脚本；导入库模块不启动 CLI。
if __name__ == "__main__" and not globals().get("_AO_PRODUCT_ENTRY"):
    from pathlib import Path as _AoPath
    _ao_loader = _AoPath(__file__).resolve().parents[1] / "bootstrap/product_access.py"
    _ao_namespace = {"__file__": str(_ao_loader), "_AO_LOADER_SOURCE": _ao_loader.read_bytes()}
    exec(compile(_ao_namespace["_AO_LOADER_SOURCE"], str(_ao_loader), "exec"), _ao_namespace)
    raise SystemExit(_ao_namespace["python_entry"](__file__))


import argparse
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from workflow import archive_store, engineering_baseline as baseline, station_archive, task_store, station_operation


def record(base, issue, run, value):
    from workflow import quality_contract
    quality_contract.validate(value, "station-disposition.schema.json")
    task_store.validate_run_id(issue, run)
    identifier = value.get("disposition_id", "")
    if not re.fullmatch(r"disposition-[a-z0-9-]{8,80}", identifier):
        raise ValueError("处置需要稳定 disposition_id")
    with task_store.task_state_lock(base):
        phase = value["phase"]
        current = task_store.read_task(base)
        if current is not None:
            raise ValueError("独立分支处置不得占用或修改当前任务")
        root = archive_store.run_directory(base, run)
        for directory in (archive_store.root(base), root):
            if directory.is_symlink() or not directory.is_dir():
                raise ValueError("处置档案路径无效")
        metadata = root / "record.json"
        if metadata.is_symlink():
            raise ValueError("档案正文不能是链接")
        reference = archive_store.reference(run, baseline.digest(json.loads(metadata.read_text())))
        station_archive.verify(base, reference, {"issue_key": issue, "run_id": run})
        receipts = archive_store.receipts(base, reference, create=True)
        intent_path = receipts / (identifier + "-intent.json")
        if intent_path.is_symlink():
            raise ValueError("处置意图不能是链接")
        if phase == "intent":
            if value.get("resource_type") not in ("git-branch", "pull-request") or value.get("action") not in ("delete", "close"):
                raise ValueError("处置仅支持指定分支删除或 PR 关闭")
            if (value["resource_type"] == "git-branch") != (value["action"] == "delete"):
                raise ValueError("分支与 PR 的处置动作不匹配")
            before = value.get("before", {})
            if not value.get("object_id") or before.get("protected") is not False or not value.get("decision_ref"):
                raise ValueError("处置需精确对象、保护回读和用户决定")
            required = ("repository", "ref", "sha") if value["resource_type"] == "git-branch" else ("repository", "number", "state", "head_sha")
            if any(not before.get(key) for key in required):
                raise ValueError("处置前对象身份/SHA/状态不完整")
            payload = {k: v for k, v in value.items() if k != "confirmed_digest"}
            if value.get("confirmed_digest") != baseline.digest(payload):
                raise ValueError("处置需要独立的精确确认")
            target = intent_path
        elif phase in ("unknown", "readback"):
            if not intent_path.is_file():
                raise ValueError("处置缺少原意图，不能伪造回执")
            if phase == "unknown" and (receipts / (identifier + "-readback.json")).exists():
                raise ValueError("已有最终回读，不能退回 unknown")
            intent = json.loads(intent_path.read_text())
            if value.get("intent_digest") != baseline.digest(intent) or value.get("object_id") != intent["object_id"]:
                raise ValueError("处置回读与原意图不匹配")
            if not value.get("readback_ref") or (phase == "readback" and value.get("status") not in ("deleted", "closed", "unchanged")):
                raise ValueError("处置结果缺少实际回读")
            target = receipts / (identifier + "-" + phase + ".json")
        else:
            raise ValueError("处置 phase 必须为 intent/unknown/readback")
        if target.is_symlink():
            raise ValueError("处置回执不能是链接")
        if target.exists():
            if json.loads(target.read_text()) != value:
                raise ValueError("处置记录不可覆盖，只能回读原操作")
        else:
            task_store._write_json_atomic(target, value)
        return {"path": str(target), "digest": baseline.digest(value), "phase": phase,
                "guidance": "已有意图只回读，不能据此重复发送外部操作"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dir", default=".")
    parser.add_argument("--issue-key", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--input", required=True)
    args = parser.parse_args()
    try:
        print(json.dumps(record(args.dir, args.issue_key, args.run_id, json.loads(Path(args.input).read_text())), ensure_ascii=False))
        return 0
    except (OSError, ValueError) as error:
        print("错误：%s" % error, file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
