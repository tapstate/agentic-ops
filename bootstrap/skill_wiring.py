#!/usr/bin/env python3
"""将 Agent 无关的维护 Skill 接线到源码产品根目录的 Agent 原生目录。"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
from pathlib import Path

from agent_registry import discover
from product_state import load as load_product_state
from workspace_paths import WorkspaceDirectory


SCHEMA_VERSION = 1
STATE_PATH = Path(".local") / "maintenance-skill-wiring.json"
SKILL_NAME_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


def _frontmatter_value(value):
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        return value[1:-1]
    return value


def validate_skill(skill_root):
    skill_file = skill_root / "SKILL.md"
    if skill_root.is_symlink() or not skill_root.is_dir():
        raise ValueError("Skill 必须是源码内普通目录：%s" % skill_root)
    if skill_file.is_symlink() or not skill_file.is_file():
        raise ValueError("Skill 缺少普通文件 SKILL.md：%s" % skill_root)
    if (skill_root / "agents").exists():
        raise ValueError("通用 Skill 不得携带 Agent 专用 agents/ 配置：%s" % skill_root)
    try:
        lines = skill_file.read_text(encoding="utf-8").splitlines()
    except OSError as error:
        raise ValueError("Skill 无法读取：%s：%s" % (skill_file, error)) from error
    if not lines or lines[0].strip() != "---":
        raise ValueError("Skill 缺少 YAML frontmatter：%s" % skill_file)
    fields = {}
    for line in lines[1:]:
        if line.strip() == "---":
            break
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        fields[key.strip()] = _frontmatter_value(value)
    else:
        raise ValueError("Skill frontmatter 未闭合：%s" % skill_file)
    name = fields.get("name")
    description = fields.get("description")
    if not isinstance(name, str) or not SKILL_NAME_PATTERN.fullmatch(name):
        raise ValueError("Skill name 无效：%s" % skill_file)
    if name != skill_root.name:
        raise ValueError("Skill name 与目录不一致：%s" % skill_file)
    if not isinstance(description, str) or not description.strip():
        raise ValueError("Skill description 不能为空：%s" % skill_file)


def maintenance_skill_sources(product_root):
    root = Path(product_root).resolve() / "skills"
    if not root.is_dir():
        raise ValueError("源码产品根目录缺少通用 Skill 目录：%s" % root)
    sources = []
    for candidate in sorted(root.iterdir()):
        if candidate.name.startswith("."):
            continue
        validate_skill(candidate)
        sources.append(candidate)
    if not sources:
        raise ValueError("源码产品根目录没有可接线的维护 Skill")
    return sources


def validate_source_product_root(product_root):
    root = Path(product_root).resolve()
    marker = root / ".agentic-ops-source"
    if not marker.is_file() or marker.read_text(encoding="utf-8").splitlines()[:1] != ["source"]:
        raise ValueError("维护 Skill 只允许接线到源码产品根目录：%s" % root)
    state = load_product_state(root)
    if state.get("mode") != "source":
        raise ValueError("维护 Skill 接线要求产品根目录 mode=source：%s" % root)
    return root


def expected_artifacts(product_root):
    artifacts = {}
    owners = {}
    sources = maintenance_skill_sources(product_root)
    for agent_id, manifest in sorted(discover(product_root).items()):
        skill_target = manifest.get("skill_target")
        if not skill_target:
            continue
        for source in sources:
            target = str(Path(skill_target) / source.name)
            destination = product_root / target
            relative_source = os.path.relpath(str(source), str(destination.parent))
            existing = artifacts.get(target)
            if existing is not None and existing["target"] != relative_source:
                raise ValueError(
                    "Agent Skill 接线目标冲突：%s 同时由 %s 和 %s 生成"
                    % (target, owners[target], agent_id)
                )
            artifacts[target] = {"kind": "symlink", "target": relative_source}
            owners[target] = agent_id
    return artifacts


def state_document(artifacts):
    return {
        "schema_version": SCHEMA_VERSION,
        "artifacts": [
            {"path": path, "kind": artifact["kind"], "target": artifact["target"]}
            for path, artifact in sorted(artifacts.items())
        ],
    }


def load_owned(tree):
    if not tree.exists(STATE_PATH):
        return {}
    if not tree.is_file(STATE_PATH) or tree.is_symlink(STATE_PATH):
        raise ValueError("维护 Skill 接线清单不是普通文件：%s" % tree.path(STATE_PATH))
    document = tree.read_json(STATE_PATH, "维护 Skill 接线清单")
    if not isinstance(document, dict) or document.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("维护 Skill 接线清单 schema_version 无效")
    records = document.get("artifacts")
    if not isinstance(records, list):
        raise ValueError("维护 Skill 接线清单 artifacts 无效")
    owned = {}
    for record in records:
        if not isinstance(record, dict) or set(record) != {"path", "kind", "target"}:
            raise ValueError("维护 Skill 接线清单记录结构无效")
        path = record.get("path")
        kind = record.get("kind")
        target = record.get("target")
        if not isinstance(path, str) or not path or path in owned:
            raise ValueError("维护 Skill 接线清单 path 无效")
        if kind != "symlink" or not isinstance(target, str) or not target:
            raise ValueError("维护 Skill 接线清单接线记录无效：%s" % path)
        tree.path(path)
        owned[path] = {"kind": kind, "target": target}
    return owned


def _valid_link(tree, path, artifact):
    return tree.is_symlink(path) and tree.readlink(path) == artifact["target"]


def check_wiring(product_root, artifacts, tree):
    owned = load_owned(tree)
    if owned != artifacts:
        raise ValueError("维护 Skill 接线清单漂移，请执行 agenticops update")
    drift = [path for path, artifact in artifacts.items() if not _valid_link(tree, path, artifact)]
    if drift:
        raise ValueError("维护 Skill 接线漂移：%s" % ", ".join(sorted(drift)))


def _remove_empty_parents(tree, path):
    parent = Path(path).parent
    while parent.parts:
        if not tree.rmdir_cached(parent):
            break
        parent = parent.parent


def refresh_wiring(product_root, artifacts, tree):
    owned = load_owned(tree)
    stale = sorted(set(owned) - set(artifacts))

    for path, artifact in artifacts.items():
        if tree.exists(path) and not _valid_link(tree, path, artifact):
            raise ValueError("维护 Skill 接线位置已被占用或发生漂移：%s" % tree.path(path))
    for path in stale:
        if tree.exists(path) and not _valid_link(tree, path, owned[path]):
            raise ValueError("过期维护 Skill 接线发生漂移，拒绝删除：%s" % tree.path(path))

    for path in stale:
        if tree.exists(path):
            tree.unlink(path)
            _remove_empty_parents(tree, path)
    for path, artifact in artifacts.items():
        if not tree.exists(path):
            tree.symlink(artifact["target"], path)
    tree.write_json_atomic(STATE_PATH, state_document(artifacts))


def update_git_exclude(product_root, artifacts):
    result = subprocess.run(
        ["git", "-C", str(product_root), "rev-parse", "--git-path", "info/exclude"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise ValueError("源码产品根目录无法定位 Git info/exclude")
    exclude = Path(result.stdout.strip())
    if not exclude.is_absolute():
        exclude = product_root / exclude
    existing = set(exclude.read_text(encoding="utf-8").splitlines()) if exclude.is_file() else set()
    missing = [path for path in sorted(artifacts) if path not in existing]
    if missing:
        exclude.parent.mkdir(parents=True, exist_ok=True)
        with exclude.open("a", encoding="utf-8") as stream:
            for path in missing:
                stream.write(path + "\n")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--product-root", required=True)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--refresh", action="store_true")
    mode.add_argument("--check", action="store_true")
    arguments = parser.parse_args()
    try:
        product_root = validate_source_product_root(arguments.product_root)
        artifacts = expected_artifacts(product_root)
        with WorkspaceDirectory(product_root) as tree:
            if arguments.check:
                check_wiring(product_root, artifacts, tree)
            else:
                refresh_wiring(product_root, artifacts, tree)
        if arguments.refresh:
            update_git_exclude(product_root, artifacts)
    except (OSError, ValueError) as error:
        parser.error(str(error))
    if arguments.check:
        print(
            "AgenticOps 维护 Skill 接线检查通过：%s（links=%s）"
            % (product_root, len(artifacts))
        )
    else:
        print(
            "AgenticOps 维护 Skill 接线已刷新：%s（links=%s）"
            % (product_root, len(artifacts))
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
