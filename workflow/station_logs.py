"""保留 Project 指定日志/报告的脱敏正文；范围授权与最终内容快照分开。"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import stat

from workflow import project_rules, station_directories, task_store

MAX_FILE_BYTES = 16 * 1024 * 1024
MAX_TOTAL_BYTES = 64 * 1024 * 1024


def _read_tree(directory, root):
    """FD 遍历先核验边界再读正文，不读取链接/挂载外内容。"""
    descriptor = os.open(str(directory), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    device = os.fstat(descriptor).st_dev
    def walk(fd, relative):
        for name in sorted(os.listdir(fd)):
            info = os.stat(name, dir_fd=fd, follow_symlinks=False)
            if name == ".git" or info.st_dev != device or stat.S_ISLNK(info.st_mode):
                raise ValueError("关键日志/报告包含链接、挂载或嵌套 Git")
            if stat.S_ISDIR(info.st_mode):
                child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                try:
                    opened = os.fstat(child)
                    if (opened.st_dev, opened.st_ino) != (info.st_dev, info.st_ino):
                        raise ValueError("日志目录被替换")
                    yield from walk(child, relative + "/" + name)
                finally:
                    os.close(child)
            else:
                if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_FILE_BYTES:
                    raise ValueError("关键日志/报告对象不支持或过大，请先导出并提供脱敏摘要")
                child = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
                with os.fdopen(child, "rb") as stream:
                    opened = os.fstat(stream.fileno())
                    if (opened.st_dev, opened.st_ino) != (info.st_dev, info.st_ino):
                        raise ValueError("日志文件被替换")
                    data = stream.read(MAX_FILE_BYTES + 1)
                    if len(data) > MAX_FILE_BYTES:
                        raise ValueError("关键日志/报告过大")
                    yield relative + "/" + name, data
    try:
        yield from walk(descriptor, directory.relative_to(root).as_posix())
    finally:
        os.close(descriptor)


def material(base, task, plan):
    rules = project_rules.load_admission(station=base)
    root = Path(base).resolve()
    result = {"schema_version": 1, "run_id": task["run_id"], "files": {}}
    size = 0
    for relative in plan["archive_runtime"]:
        directory = station_directories.path_at(base, "runtime/" + relative)
        if not directory.exists():
            continue
        for name, data in _read_tree(directory, root):
            size += len(data)
            if size > MAX_TOTAL_BYTES:
                raise ValueError("关键日志/报告总量过大，请先导出并提供脱敏摘要")
            try:
                text = data.decode("utf-8")
            except UnicodeDecodeError as error:
                raise ValueError("关键日志/报告不是 UTF-8 文本，请先安全导出并提供脱敏摘要") from error
            hits = {line for line, _, _ in project_rules.scan_sensitive(rules, text)}
            lines = text.splitlines(keepends=True)
            redacted = "".join("[redacted]\n" if index in hits else line for index, line in enumerate(lines, 1))
            if project_rules.scan_sensitive(rules, redacted):
                raise ValueError("日志/报告脱敏校验失败")
            result["files"][name] = {"source_sha256": hashlib.sha256(data).hexdigest(), "source_size": len(data),
                                     "redacted_lines": sorted(hits), "text": redacted}
    return json.dumps(result, sort_keys=True, ensure_ascii=False).encode()


def verify_current(base, task, plan):
    target = Path(base).resolve() / task["archive_ref"]["path"] / "runtime-evidence.json"
    if target.is_symlink():
        raise ValueError("运行证据档案不能是链接")
    saved = json.loads(target.read_text())["files"]
    current = json.loads(material(base, task, plan))["files"]
    # 部分删除恢复可以少文件，不能删除档案未覆盖的新日志或变化正文。
    if any(name not in saved or item != saved[name] for name, item in current.items()):
        # 后续回读和停止动作可能产生新日志；先追加核验后的脱敏证据，再回收。
        # 正式正文不变，目录授权没有扩大。
        data = {"run_id": task["run_id"], "files": current}
        from workflow.engineering_baseline import digest
        receipt = target.parent / "receipts" / ("runtime-evidence-" + digest(data) + ".json")
        if receipt.parent.is_symlink() or receipt.is_symlink():
            raise ValueError("日志追加证据不能是链接")
        if not receipt.exists():
            task_store._write_json_atomic(receipt, data)
        if json.loads(receipt.read_text()) != data:
            raise ValueError("日志追加证据回读失败，拒绝删除")
