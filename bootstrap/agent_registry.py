#!/usr/bin/env python3
"""基于 Adapter Manifest 发现和解析 Agent；公共层不维护平台枚举。"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path


AGENT_ID_PATTERN = re.compile(r"^[a-z][a-z0-9-]*$")


def _safe_path(root, relative):
    candidate = (root / relative).resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError as error:
        raise ValueError("Agent Manifest 路径越界：%s" % relative) from error
    return candidate


def discover(product_root):
    root = Path(product_root).resolve()
    agent_root = root / "adapters" / "agents"
    manifests = {}
    if not agent_root.is_dir():
        raise ValueError("产品根目录缺少 Agent Adapter 目录：%s" % agent_root)
    for path in sorted(agent_root.glob("*/manifest.json")):
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise ValueError("Agent Manifest 无法读取：%s：%s" % (path, error)) from error
        agent_id = document.get("name")
        if document.get("schema_version") != 1:
            raise ValueError("Agent Manifest schema_version 无效：%s" % path)
        if not isinstance(agent_id, str) or not AGENT_ID_PATTERN.fullmatch(agent_id):
            raise ValueError("Agent Manifest name 无效：%s" % path)
        if agent_id != path.parent.name:
            raise ValueError("Agent Manifest name 与目录不一致：%s" % path)
        if agent_id in manifests:
            raise ValueError("Agent ID 重复：%s" % agent_id)
        adapter_version = document.get("adapter_version")
        if type(adapter_version) is not int or adapter_version < 1:
            raise ValueError("Agent adapter_version 无效：%s" % agent_id)
        capabilities = document.get("capabilities")
        if not isinstance(capabilities, dict):
            raise ValueError("Agent capabilities 无效：%s" % agent_id)
        decisions = capabilities.get("decisions")
        if (
            not isinstance(decisions, list)
            or not all(isinstance(item, str) for item in decisions)
            or len(set(decisions)) != len(decisions)
            or len(decisions) < 2
            or not set(decisions) <= {"allow", "ask", "deny"}
        ):
            raise ValueError("Agent decisions 无效：%s" % agent_id)
        if capabilities.get("ask_fallback") not in ("native", "deny_with_guidance"):
            raise ValueError("Agent ask_fallback 无效：%s" % agent_id)
        task_directory_argument = capabilities.get("task_directory_argument")
        if task_directory_argument is not None and (
            not isinstance(task_directory_argument, str)
            or not task_directory_argument.startswith("--")
        ):
            raise ValueError("Agent task_directory_argument 无效：%s" % agent_id)
        entrypoint = document.get("entrypoint")
        if not isinstance(entrypoint, str) or not _safe_path(root, entrypoint).is_file():
            raise ValueError("Agent Adapter 入口不存在：%s" % agent_id)
        artifacts = document.get("artifacts")
        if not isinstance(artifacts, list):
            raise ValueError("Agent artifacts 无效：%s" % agent_id)
        for artifact in artifacts:
            template = artifact.get("template") if isinstance(artifact, dict) else None
            target = artifact.get("target") if isinstance(artifact, dict) else None
            if not isinstance(template, str) or not _safe_path(root, template).is_file():
                raise ValueError("Agent 接线模板不存在：%s" % agent_id)
            if not isinstance(target, str) or not target:
                raise ValueError("Agent 接线目标无效：%s" % agent_id)
        launch = document.get("launch")
        if not isinstance(launch, dict):
            raise ValueError("Agent Manifest 缺少 launch：%s" % agent_id)
        mode = launch.get("mode")
        command = launch.get("command")
        if not isinstance(launch.get("message"), str):
            raise ValueError("Agent 启动提示无效：%s" % agent_id)
        if mode == "command" and (not isinstance(command, str) or not command.strip()):
            raise ValueError("Agent 启动命令无效：%s" % agent_id)
        if mode == "manual" and command is not None:
            raise ValueError("手动启动 Agent 不得声明 command：%s" % agent_id)
        if mode not in ("command", "manual"):
            raise ValueError("Agent 启动模式无效：%s" % agent_id)
        project_skill_target = document.get("project_skill_target")
        if project_skill_target is not None:
            if not isinstance(project_skill_target, str) or not project_skill_target:
                raise ValueError("Agent 项目 Skill 目标无效：%s" % agent_id)
            target_path = Path(project_skill_target)
            if target_path.is_absolute() or any(
                part in ("", ".", "..") for part in target_path.parts
            ):
                raise ValueError("Agent 项目 Skill 目标必须是受控相对路径：%s" % agent_id)
        manifests[agent_id] = document
    if not manifests:
        raise ValueError("产品根目录没有可用 Agent Adapter")
    return manifests


def select(product_root, requested=None):
    manifests = discover(product_root)
    values = list(requested or sorted(manifests))
    if not values:
        values = sorted(manifests)
    selected = []
    for value in values:
        if value not in manifests:
            raise ValueError(
                "未知 Agent：%s；可用 Agent：%s" % (value, ", ".join(sorted(manifests)))
            )
        if value in selected:
            raise ValueError("Agent 重复：%s" % value)
        selected.append(value)
    return selected, manifests


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--product-root", required=True)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list")
    resolve = sub.add_parser("resolve-launch")
    resolve.add_argument("agent")
    resolve.add_argument("--workspace")
    task_directory = sub.add_parser("resolve-task-directory-argument")
    task_directory.add_argument("agent")
    task_directory.add_argument("--workspace", required=True)
    args = parser.parse_args()
    try:
        manifests = discover(args.product_root)
        if args.command == "list":
            for agent_id in sorted(manifests):
                print(agent_id)
            return 0
        if args.agent not in manifests:
            raise ValueError(
                "未知 Agent：%s；可用 Agent：%s"
                % (args.agent, ", ".join(sorted(manifests)))
            )
        if args.workspace:
            config_path = Path(args.workspace).resolve() / ".agenticops" / "workspace.json"
            try:
                config = json.loads(config_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as error:
                raise ValueError("工作空间配置无法读取：%s" % error) from error
            if args.agent not in config.get("agents", []):
                raise ValueError("工作空间未绑定 Agent：%s；请重新执行 agenticops init" % args.agent)
        if args.command == "resolve-task-directory-argument":
            config_path = Path(args.workspace).resolve() / ".agenticops" / "workspace.json"
            try:
                config = json.loads(config_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as error:
                raise ValueError("工作空间配置无法读取：%s" % error) from error
            if args.agent not in config.get("agents", []):
                raise ValueError("工作空间未绑定 Agent：%s" % args.agent)
            argument = manifests[args.agent]["capabilities"].get("task_directory_argument")
            if not argument:
                raise ValueError(
                    "Agent %s 未声明动态任务目录能力；不能安全启动外部 worktree" % args.agent
                )
            print(argument)
            return 0
        launch = manifests[args.agent].get("launch", {})
        if launch.get("mode") != "command" or not launch.get("command"):
            raise ValueError(launch.get("message") or "该 Agent 不支持本地命令启动")
        print(launch["command"])
        return 0
    except ValueError as error:
        parser.error(str(error))


if __name__ == "__main__":
    raise SystemExit(main())
