"""Product Root 中央任务档案的路径、预留与安全读取。"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

from workflow import project_rules, task_store


ARCHIVE_DIRECTORY = ".archive"
RESERVATION_DIRECTORY = ".reservations"


def root(base, create=False):
    product_root = project_rules.product_root_from_station(base)
    path = product_root / ARCHIVE_DIRECTORY
    if path.is_symlink() or (path.exists() and not path.is_dir()):
        raise ValueError("Product Root 档案根必须为真实目录")
    if create:
        path.mkdir(mode=0o700, exist_ok=True)
        os.chmod(path, 0o700)
    return path


def run_directory(base, run_id, create_root=False):
    task_store.validate_run_id_from_value(run_id)
    return root(base, create=create_root) / run_id


def from_reference(base, reference):
    if not isinstance(reference, dict) or set(reference) != {"scope", "run_id", "digest"}:
        raise ValueError("档案引用结构无效")
    if reference.get("scope") != "product":
        raise ValueError("档案引用 scope 无效")
    digest = reference.get("digest")
    if not isinstance(digest, str) or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
        raise ValueError("档案引用摘要无效")
    return run_directory(base, reference.get("run_id"))


def reference(run_id, digest):
    task_store.validate_run_id_from_value(run_id)
    return {"scope": "product", "run_id": run_id, "digest": digest}


def receipts(base, reference_value, create=False):
    path = from_reference(base, reference_value) / "receipts"
    if path.is_symlink() or (path.exists() and not path.is_dir()):
        raise ValueError("档案回执目录必须为真实目录")
    if create:
        path.mkdir(mode=0o700, exist_ok=True)
        os.chmod(path, 0o700)
    return path


def reserve(base, issue_key, run_id):
    """原子预留新 run_id，使多工位不能生成同名中央档案。"""
    issue_key = task_store.validate_issue_key(issue_key)
    task_store.validate_run_id(issue_key, run_id)
    archive_root = root(base, create=True)
    reservations = archive_root / RESERVATION_DIRECTORY
    if reservations.is_symlink() or (reservations.exists() and not reservations.is_dir()):
        raise ValueError("档案预留目录必须为真实目录")
    reservations.mkdir(mode=0o700, exist_ok=True)
    os.chmod(reservations, 0o700)
    if (archive_root / run_id).exists() or (archive_root / run_id).is_symlink():
        raise FileExistsError(run_id)
    binding = json.loads((task_store.state_path(base) / "station.json").read_text(encoding="utf-8"))
    station_id = binding.get("station_id")
    if not isinstance(station_id, str) or not station_id:
        raise ValueError("工位绑定缺少 station_id")
    path = reservations / (run_id + ".json")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    descriptor = os.open(str(path), flags, 0o600)
    try:
        value = {
            "schema_version": 1,
            "issue_key": issue_key,
            "run_id": run_id,
            "station_id": station_id,
            "reserved_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        }
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            descriptor = None
            json.dump(value, stream, ensure_ascii=False, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
    finally:
        if descriptor is not None:
            os.close(descriptor)
    parent = os.open(str(reservations), os.O_RDONLY)
    try:
        os.fsync(parent)
    finally:
        os.close(parent)
    return path


def consume_reservation(base, run_id):
    path = root(base) / RESERVATION_DIRECTORY / (run_id + ".json")
    if path.is_symlink():
        raise ValueError("档案预留文件不能是符号链接")
    if path.exists():
        path.unlink()
        parent = os.open(str(path.parent), os.O_RDONLY)
        try:
            os.fsync(parent)
        finally:
            os.close(parent)
