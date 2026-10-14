"""Product Root 中央任务档案的路径、预留与安全读取。"""
from __future__ import annotations

import os
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
    """以正式档案目录原子占位；目录存续就是编号不再分配的凭据。"""
    issue_key = task_store.validate_issue_key(issue_key)
    task_store.validate_run_id(issue_key, run_id)
    archive_root = root(base, create=True)
    legacy = archive_root / RESERVATION_DIRECTORY
    if legacy.is_symlink() or (legacy.exists() and not legacy.is_dir()):
        raise ValueError("历史档案预留目录必须为真实目录")
    previous = legacy / (run_id + ".json")
    if previous.exists() or previous.is_symlink():
        raise FileExistsError(run_id)
    target = archive_root / run_id
    target.mkdir(mode=0o700)
    try:
        parent = os.open(str(archive_root), os.O_RDONLY)
        try:
            os.fsync(parent)
        finally:
            os.close(parent)
    except OSError:
        # 创建成功但持久化失败时，仅撤销本次空目录；无法确认撤销则停止。
        try:
            target.rmdir()
        except OSError as error:
            raise ValueError("编号占位持久化失败且撤销结果无法确认，保留目录并核验") from error
        raise
    return target


def consume_reservation(base, run_id):
    """仅接管意图确定未保存时撤销本次占位，不删除任何归档内容。"""
    target = run_directory(base, run_id)
    if target.is_symlink():
        raise ValueError("编号占位目录不能为符号链接")
    target.rmdir()
    parent = os.open(str(target.parent), os.O_RDONLY)
    try:
        os.fsync(parent)
    finally:
        os.close(parent)
