#!/usr/bin/env python3
"""只读分析 Maven 报告及多仓 PR 清单；不执行测试、不判定验收。"""
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import sys
import zipfile
import xml.etree.ElementTree as ET

LIMIT = 16 * 1024 * 1024
TOTAL_LIMIT = 128 * 1024 * 1024
FILE_LIMIT = 2000
KEYS = ("tests", "failures", "errors", "skipped")


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def safe_text(value):
    value = re.sub(r"(?i)(password|passwd|token|secret|authorization)(\s*[:=]\s*)[^\s,;]+", r"\1\2[REDACTED]", value or "")
    value = re.sub(r"(://)[^\s/@]+:[^\s/@]+@", r"\1[REDACTED]@", value)
    return value[:4000]


def parse_xml(raw, name):
    # 禁止 DTD/实体，包括 UTF-16/32 编码的声明；仅支持 Maven XML。
    if len(raw) > LIMIT or re.search(br"<!\s*(DOCTYPE|ENTITY)", raw.replace(b"\0", b""), re.I):
        raise ValueError("报告过大或包含 DTD/实体声明")
    root = ET.fromstring(raw)
    suites = [root] if root.tag == "testsuite" else list(root) if root.tag == "testsuites" else []
    if not suites or any(s.tag != "testsuite" for s in suites):
        raise ValueError("不支持的报告结构")
    totals = dict.fromkeys(KEYS, 0)
    cases = []
    identities = set()
    for suite in suites:
        actual = dict.fromkeys(KEYS, 0)
        for case in suite.findall("testcase"):
            identity = (suite.get("name", ""), case.get("classname", ""), case.get("name", ""))
            if identity in identities:
                raise ValueError("用例身份重复，无法确认重试或重复统计")
            identities.add(identity)
            outcomes = [kind for kind in ("failure", "error", "skipped") if case.find(kind) is not None]
            if len(outcomes) > 1:
                raise ValueError("用例结果不互斥")
            state = outcomes[0] if outcomes else "passed"
            actual["tests"] += 1
            if outcomes:
                actual[{"failure": "failures", "error": "errors", "skipped": "skipped"}[state]] += 1
            details = [{"type": x.tag, "message": safe_text(x.get("message", "")),
                        "exception": safe_text(x.get("type", "")), "text": safe_text(x.text)}
                       for x in case if x.tag in ("failure", "error", "skipped", "flakyFailure", "flakyError", "rerunFailure", "rerunError")]
            cases.append({"suite": identity[0], "class": identity[1], "name": identity[2],
                          "status": state, "time": case.get("time"), "details": details})
        if actual != {k: int(suite.attrib[k]) for k in KEYS}:
            raise ValueError("明细与套件计数冲突")
        for key in KEYS:
            totals[key] += actual[key]
    if root.tag == "testsuites":
        for key in KEYS:
            if key in root.attrib and int(root.attrib[key]) != totals[key]:
                raise ValueError("汇总与套件计数冲突")
    totals["passed"] = totals["tests"] - sum(totals[k] for k in KEYS[1:])
    return {"file": name, "sha256": sha(raw), "counts": totals, "cases": cases}


def files(source):
    """ZIP 在内存逐文件读取，不向磁盘解压。"""
    if source.is_symlink():
        raise ValueError("报告来源不能是符号链接")
    if source.is_file() and source.suffix.lower() == ".zip":
        if source.stat().st_size > TOTAL_LIMIT:
            raise ValueError("ZIP 超过大小限制")
        with zipfile.ZipFile(source) as archive:
            members = archive.infolist()
            if len(members) > FILE_LIMIT or sum(x.file_size for x in members) > TOTAL_LIMIT:
                raise ValueError("ZIP 文件数或展开大小超过限制")
            names = set()
            for member in members:
                path = PurePosixPath(member.filename)
                if (path.is_absolute() or ".." in path.parts or "\\" in member.filename
                        or ":" in member.filename or member.filename in names
                        or (member.external_attr >> 16) & 0o170000 == 0o120000):
                    raise ValueError("ZIP 路径不安全或重复")
                names.add(member.filename)
                if member.file_size > LIMIT:
                    raise ValueError("ZIP 单文件超过限制")
                if not member.is_dir() and path.suffix == ".xml":
                    yield member.filename, archive.read(member)
    else:
        paths = sorted(source.rglob("*.xml")) if source.is_dir() else [source]
        if len(paths) > FILE_LIMIT:
            raise ValueError("报告文件数超过限制")
        total = 0
        for path in paths:
            if path.is_symlink() or path.resolve() != path.absolute():
                raise ValueError("报告路径经过符号链接")
            total += path.stat().st_size
            if path.stat().st_size > LIMIT or total > TOTAL_LIMIT:
                raise ValueError("报告超过大小限制")
            yield str(path.relative_to(source)) if source.is_dir() else path.name, path.read_bytes()


def analyze(source):
    reports, problems, summaries = [], [], []
    hashes = set()
    case_ids = set()
    source_info = {"path": str(source)}
    try:
        path = Path(source)
        if path.is_file() and not path.is_symlink() and path.stat().st_size <= TOTAL_LIMIT:
            with path.open("rb") as stream:
                digest = hashlib.sha256()
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(chunk)
            source_info["sha256"] = digest.hexdigest()
        for name, raw in files(Path(source)):
            if PurePosixPath(name).name == "failsafe-summary.xml":
                summaries.append((name, raw))
                continue
            if not PurePosixPath(name).name.startswith("TEST-"):
                continue
            try:
                parsed = parse_xml(raw, name)
                if parsed["sha256"] in hashes:
                    raise ValueError("重复报告内容，未重复计数")
                identities = {(str(PurePosixPath(name).parent), c["suite"], c["class"], c["name"]) for c in parsed["cases"]}
                if identities & case_ids:
                    raise ValueError("同目录报告存在重叠用例，统计冲突")
                hashes.add(parsed["sha256"])
                case_ids.update(identities)
                reports.append(parsed)
            except (ValueError, KeyError, ET.ParseError) as error:
                problems.append({"file": name, "reason": str(error)})
    except (OSError, ValueError, zipfile.BadZipFile, RuntimeError) as error:
        problems.append({"reason": str(error)})
    # Failsafe summary 只和同目录明细交叉核对，不叠加计数。
    for name, raw in summaries:
        try:
            if len(raw) > LIMIT or re.search(br"<!\s*(DOCTYPE|ENTITY)", raw.replace(b"\0", b""), re.I):
                raise ValueError("汇总包含不支持的声明")
            root = ET.fromstring(raw)
            peers = [r for r in reports if PurePosixPath(r["file"]).parent == PurePosixPath(name).parent]
            if root.tag != "failsafe-summary" or not peers:
                raise ValueError("汇总缺少对应明细或格式不支持")
            for key, field in (("tests", "completed"), ("failures", "failures"), ("errors", "errors"), ("skipped", "skipped")):
                if int(root.findtext(field)) != sum(r["counts"][key] for r in peers):
                    raise ValueError("Failsafe 汇总与明细冲突")
        except (ValueError, TypeError, ET.ParseError) as error:
            problems.append({"file": name, "reason": str(error)})
    if not reports:
        problems.append({"reason": "未取得可解析的 TEST-*.xml 明细"})
    return {"source": source_info, "parse_status": "complete" if reports and not problems else "partial" if reports else "unavailable",
            "reports": reports, "summary_files": [{"file": n, "sha256": sha(r)} for n, r in summaries],
            "problems": problems,
            "boundary": "计数按报告文件展示，未跨运行去重；解析完成不代表执行或验收通过。异常文本仅做常见凭据脱敏，回写前仍须审查。"}


def task_report(document):
    if not isinstance(document, dict) or document.get("schema_version") != 1 or not isinstance(document.get("objects"), list) or not document["objects"]:
        raise ValueError("需要 schema_version=1 和非空 objects 报告清单")
    results, ids = [], set()
    for entry in document["objects"]:
        if not isinstance(entry, dict):
            raise ValueError("报告对象必须为 JSON 对象")
        identity = entry.get("id")
        if not isinstance(identity, str) or not identity or identity in ids or not isinstance(entry.get("repository"), str) or not entry["repository"].strip():
            raise ValueError("报告对象需有唯一 id 和 repository")
        if entry.get("path") is not None and not isinstance(entry["path"], str):
            raise ValueError("path 必须为文件路径字符串")
        ids.add(identity)
        result = {"id": identity, "repository": entry["repository"], "context": entry.get("context", {}),
                  "selection": entry.get("selection", "unresolved"),
                  "provenance": entry.get("provenance", {"status": "unknown"}),
                  "execution": entry.get("execution", {"status": "unknown"}),
                  "dependency_observation": entry.get("dependency_observation", "实际消费版本未核实；仅披露，不新增门禁")}
        if entry.get("path"):
            result["analysis"] = analyze(entry["path"])
        else:
            result["analysis"] = {"parse_status": "unavailable", "reports": [],
                                  "problems": [{"reason": entry.get("reason", "尚未取得报告")}]}
        results.append(result)
    fingerprint = sha(json.dumps(document, sort_keys=True, ensure_ascii=False).encode())
    return {"schema_version": 1, "input_sha256": fingerprint, "snapshot": document.get("snapshot", {}),
            "processing_complete": True, "objects": results,
            "boundary": "仅表示清单内对象均已处理或记录缺失原因；清单完整性、运行选择及归属由原生回读证据核对，不产生任务 PASS。跨仓组合未证明不新增门禁。"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--source", type=Path)
    group.add_argument("--input", type=Path)
    args = parser.parse_args()
    try:
        result = analyze(args.source) if args.source else task_report(json.loads(args.input.read_text()))
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0  # 处理完成，不是测试或验收通过。
    except (OSError, ValueError, TypeError, KeyError) as error:
        print("报告输入无效：%s" % error, file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
