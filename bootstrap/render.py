#!/usr/bin/env python3
"""为项目工作空间生成中央 Product Root 的薄接线。"""
from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path


BINDING_SCHEMA_VERSION = 1
BINDING_NAME = ".agenticops.json"


def safe_path(root, relative):
    candidate = (root / relative).resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError as error:
        raise ValueError("产物路径越界：%s" % relative) from error
    return candidate


def replacements(install_root, project):
    return {
        "__AGENTIC_OPS_HOME__": str(install_root.resolve()),
        "__AGENTIC_OPS_PROJECT__": project,
    }


def rendered_content(install_root, project, template):
    source = safe_path(install_root, template)
    content = source.read_text(encoding="utf-8")
    for marker, value in replacements(install_root, project).items():
        content = content.replace(marker, value)
    return content


def product_ref(install_root):
    current_ref = install_root / "user" / "current-ref"
    if current_ref.is_file():
        return current_ref.read_text(encoding="utf-8").strip()
    try:
        result = subprocess.run(
            ["git", "-C", str(install_root), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        pass
    return "source"


def load_binding(workspace):
    path = workspace / BINDING_NAME
    if not path.is_file():
        return None
    try:
        binding = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError("工作目录绑定无法读取：%s" % error) from error
    if binding.get("schema_version") != BINDING_SCHEMA_VERSION:
        raise ValueError("不支持的工作目录绑定版本：%s" % binding.get("schema_version"))
    return binding


def agent_names(agent):
    return ["claude", "codex"] if agent == "both" else [agent]


def expected_artifacts(install_root, project, agents):
    artifacts = {
        "AGENTS.md": rendered_content(
            install_root, project, "adapters/workspace/AGENTS.md"
        ),
        "CLAUDE.md": rendered_content(
            install_root, project, "adapters/workspace/CLAUDE.md"
        ),
        ".mcp.json": rendered_content(
            install_root, project, "adapters/tools/mcp.template.json"
        ),
    }
    for agent in agents:
        manifest_path = install_root / "adapters" / "agents" / agent / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        for artifact in manifest["artifacts"]:
            artifacts[artifact["target"]] = rendered_content(
                install_root, project, artifact["template"]
            )
    return artifacts


def legacy_project_skill_paths(install_root, workspace, project):
    skill_root = install_root / "projects" / project / "skills"
    if not skill_root.is_dir():
        return []
    return [
        workspace / ".claude" / "skills" / skill.name / "SKILL.md"
        for skill in sorted(path for path in skill_root.iterdir() if path.is_dir())
    ]


def cleanup_legacy_project_skills(install_root, workspace, project):
    for path in legacy_project_skill_paths(install_root, workspace, project):
        if not path.exists() and not path.is_symlink():
            continue
        try:
            content = path.read_text(encoding="utf-8")
        except OSError as error:
            raise ValueError("旧 Project Skill 无法读取：%s：%s" % (path, error)) from error
        if "product: agenticops" not in content:
            raise ValueError(
                "工作目录存在同名非 AgenticOps Skill，拒绝删除：%s" % path
            )
        path.unlink()
        for directory in (path.parent, path.parent.parent):
            try:
                directory.rmdir()
            except OSError:
                break


def binding_document(install_root, project, agents, artifacts):
    return {
        "schema_version": BINDING_SCHEMA_VERSION,
        "product_root": str(install_root.resolve()),
        "product_ref": product_ref(install_root),
        "project": project,
        "agents": agents,
        "generated_artifacts": sorted(artifacts),
    }


def is_legacy_generated_artifact(target, content, expected):
    if content == expected:
        return True
    markers = {
        "AGENTS.md": ("# AgenticOps 任务工作目录入口",),
        "CLAUDE.md": ("由 AgenticOps 生成", "@AGENTS.md"),
        ".claude/settings.json": ("adapters/agents/claude/hook.py",),
        ".codex/agenticops-hooks.example.json": (
            "adapters/agents/codex/hook.py",
        ),
    }
    required = markers.get(target)
    return bool(required and all(marker in content for marker in required))


def assert_artifact_ownership(workspace, binding, artifacts):
    owned = set((binding or {}).get("generated_artifacts", []))
    for target, expected in artifacts.items():
        path = safe_path(workspace, target)
        if not path.exists() and not path.is_symlink():
            continue
        if target in owned:
            continue
        try:
            content = path.read_text(encoding="utf-8")
        except OSError as error:
            raise ValueError("工作目录同名文件无法读取：%s：%s" % (path, error)) from error
        if not is_legacy_generated_artifact(target, content, expected):
            raise ValueError(
                "工作目录已有非 AgenticOps 文件，拒绝覆盖：%s" % path
            )


def check_workspace(install_root, workspace, binding):
    project = binding.get("project")
    agents = binding.get("agents")
    if not isinstance(project, str) or not project:
        raise ValueError("工作目录绑定缺少 project")
    if not isinstance(agents, list) or not agents or not set(agents) <= {"claude", "codex"}:
        raise ValueError("工作目录绑定 agents 无效")
    artifacts = expected_artifacts(install_root, project, agents)
    expected_binding = binding_document(install_root, project, agents, artifacts)
    if binding != expected_binding:
        raise ValueError("工作目录绑定与当前 Product Root 不一致，请执行 agenticops repair")
    drift = []
    for target, expected in artifacts.items():
        path = safe_path(workspace, target)
        if not path.is_file() or path.read_text(encoding="utf-8") != expected:
            drift.append(target)
    if drift:
        raise ValueError("工作目录薄接线漂移：%s" % ", ".join(sorted(drift)))
    legacy_skills = [
        str(path.relative_to(workspace))
        for path in legacy_project_skill_paths(install_root, workspace, project)
        if path.exists() or path.is_symlink()
    ]
    if legacy_skills:
        raise ValueError(
            "工作目录仍包含旧版复制 Project Skill：%s"
            % ", ".join(legacy_skills)
        )
    return expected_binding


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--install-home", required=True)
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--agent", choices=("claude", "codex", "both"))
    parser.add_argument("--project")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--refresh", action="store_true")
    mode.add_argument("--check", action="store_true")
    arguments = parser.parse_args()

    install_root = Path(arguments.install_home).resolve()
    workspace = Path(arguments.workspace).resolve()
    binding = load_binding(workspace)
    if arguments.refresh or arguments.check:
        if binding is None:
            parser.error("工作目录尚未初始化，请先执行 agenticops init")
        project = binding["project"]
        agents = binding["agents"]
    else:
        project = arguments.project or "tapdata"
        agents = agent_names(arguments.agent or "both")

    project_root = install_root / "projects" / project
    if not project_root.is_dir():
        parser.error("未安装项目适配：%s" % project)
    workspace.mkdir(parents=True, exist_ok=True)

    if arguments.check:
        try:
            checked = check_workspace(install_root, workspace, binding)
        except ValueError as error:
            parser.error(str(error))
        print(
            "AgenticOps 工作目录检查通过：%s（project=%s，agents=%s，ref=%s）"
            % (workspace, project, ",".join(agents), checked["product_ref"])
        )
        return 0

    artifacts = expected_artifacts(install_root, project, agents)
    try:
        assert_artifact_ownership(workspace, binding, artifacts)
        cleanup_legacy_project_skills(install_root, workspace, project)
    except ValueError as error:
        parser.error(str(error))
    for target, content in artifacts.items():
        destination = safe_path(workspace, target)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(content, encoding="utf-8")
    document = binding_document(install_root, project, agents, artifacts)
    (workspace / BINDING_NAME).write_text(
        json.dumps(document, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(
        "AgenticOps 工作目录接线已刷新：%s（project=%s，agents=%s，ref=%s）"
        % (workspace, project, ",".join(agents), document["product_ref"])
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
