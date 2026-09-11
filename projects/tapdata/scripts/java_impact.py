#!/usr/bin/env python3
"""根据 Agent 采集的 Maven effective-pom 计算跨仓测试候选范围。"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import sys
import xml.etree.ElementTree as ET

NS = {"m": "http://maven.apache.org/POM/4.0.0"}


def value(element, key, default=""):
    return element.findtext("m:" + key, default, NS).strip()


def coordinate(element):
    parts = [value(element, key) for key in ("groupId", "artifactId", "version")]
    if not all(parts) or any("${" in part for part in parts):
        raise ValueError("Maven 坐标缺失或未解析：%s" % ":".join(parts))
    return parts


def read_model(entry, base):
    path = (base / entry["effective_pom"]).resolve()
    content = path.read_bytes()
    if hashlib.sha256(content).hexdigest() != entry["sha256"]:
        raise ValueError("有效模型内容已变化：%s" % path)
    root = ET.fromstring(content)
    if root.tag != "{%s}project" % NS["m"]:
        raise ValueError("每项须提供单模块 effective-pom，不能使用未拆分的 reactor 输出")
    gav = coordinate(root)
    dependencies = []
    for dep in root.findall("m:dependencies/m:dependency", NS):
        dependencies.append({"gav": coordinate(dep), "kind": "dependency",
                             "scope": value(dep, "scope", "compile")})
    parent = root.find("m:parent", NS)
    if parent is not None:
        dependencies.append({"gav": coordinate(parent), "kind": "parent", "scope": "model"})
    return dict(entry, gav=gav, dependencies=dependencies,
                packaging=value(root, "packaging", "jar"))


def closure(seeds, edges):
    paths = {seed: [seed] for seed in sorted(seeds)}
    queue = list(paths)
    for current in queue:
        for target in sorted(edges.get(current, ())):
            if target not in paths:
                paths[target] = paths[current] + [target]
                queue.append(target)
    return paths


def analyze(document, base):
    if document.get("schema_version") != 1:
        raise ValueError("不支持的输入版本")
    entries = document["modules"]
    if not entries:
        raise ValueError("模块清单不能为空")
    models = {}
    unresolved = list(document.get("unresolved", []))
    for entry in entries:
        key = entry["id"]
        if not isinstance(key, str) or not key or key in models:
            raise ValueError("模块 id 为空或重复")
        for field in ("repository", "source_revision", "pom", "profiles"):
            if field not in entry:
                raise ValueError("模块缺少来源字段：%s" % field)
        pom = PurePosixPath(entry["pom"])
        if pom.is_absolute() or ".." in pom.parts or pom.name != "pom.xml":
            raise ValueError("pom 必须是仓库内相对路径")
        if not isinstance(entry["profiles"], list):
            raise ValueError("profiles 必须明确列出，本次未显式指定时为空数组")
        if not isinstance(entry["source_revision"], str) or len(entry["source_revision"]) != 40 or any(c not in "0123456789abcdef" for c in entry["source_revision"]):
            raise ValueError("source_revision 必须为完整 Git SHA")
        models[key] = read_model(entry, base)
    changed = set(document["changed_modules"])
    if not changed or changed - models.keys():
        raise ValueError("变更模块为空或不在模块清单内")
    by_ga = {}
    for key, model in models.items():
        by_ga.setdefault(tuple(model["gav"][:2]), []).append(key)
    reverse, forward, relations = {}, {}, []
    for consumer, model in models.items():
        for dep in model["dependencies"]:
            targets = by_ga.get(tuple(dep["gav"][:2]), [])
            for producer in targets:
                reverse.setdefault(producer, set()).add(consumer)
                forward.setdefault(consumer, set()).add(producer)
                mismatch = dep["gav"][2] != models[producer]["gav"][2]
                relations.append({"producer": producer, "consumer": consumer,
                                  "kind": dep["kind"], "scope": dep["scope"],
                                  "required_version": dep["gav"][2],
                                  "source_version": models[producer]["gav"][2],
                                  "version_mismatch": mismatch})
    paths = closure(changed, reverse)
    build = closure(paths, forward)
    affected_relations = [r for r in relations if r["producer"] in paths and r["consumer"] in paths]
    for relation in affected_relations:
        if relation["version_mismatch"]:
            unresolved.append("版本差异：%s -> %s；执行前核实变更 Jar 的实际消费" % (relation["producer"], relation["consumer"]))
    return {
        "schema_version": 1,
        "scope": "supplied_effective_models",
        "changed_modules": sorted(changed),
        "test_candidates": [{"id": key, "reason_path": paths[key],
                             "packaging": models[key]["packaging"]} for key in sorted(paths)],
        "build_prerequisites": sorted(set(build) - set(paths)),
        "relations": affected_relations,
        "sources": [{k: m[k] for k in ("id", "repository", "source_revision", "pom", "profiles", "sha256")} for m in models.values()],
        "unresolved": unresolved,
        "limitations": ["只覆盖已提供模型；Agent 须核对仓库、活动 profile、BOM/父 POM 变更、动态加载和未采集模块，必要时扩大范围。",
                        "候选范围不是执行证据；未列入模型的外部依赖不代表无影响，构建成功不代表集成测试通过。"],
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        result = analyze(json.loads(args.input.read_text(encoding="utf-8")), args.input.resolve().parent)
    except (OSError, ValueError, KeyError, TypeError, AttributeError, ET.ParseError) as error:
        print("影响分析未完成：%s" % error, file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
