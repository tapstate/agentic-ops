#!/usr/bin/env bash
set -euo pipefail

repo_root="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd -P)"
test_root="$(mktemp -d)"
trap 'chmod -R u+w "$test_root" 2>/dev/null || true; rm -rf "$test_root"' EXIT
test_home="$test_root/home"
mkdir -p "$test_home"
export HOME="$test_home"

# 代码验证绑定调用此脚本时的检出；安装 fixture 则允许使用任意受控分支，
# 避免把发布主线当作唯一验证对象。
tested_ref="$(git -C "$repo_root" rev-parse HEAD)"
tested_branch="$(git -C "$repo_root" branch --show-current || true)"
source_branch="develop"
install_branch="${AGENTICOPS_TEST_INSTALL_BRANCH:-acceptance-under-test}"
git check-ref-format --branch "$install_branch" >/dev/null
test "$install_branch" != "$source_branch" || {
  printf '安装 fixture 分支不能与源码维护分支相同：%s\n' "$install_branch" >&2
  exit 2
}

file_digest() {
  if command -v shasum >/dev/null 2>&1; then
    shasum -a 256 "$1" | awk '{print $1}'
  else
    sha256sum "$1" | awk '{print $1}'
  fi
}

source_repo="$test_root/source"
install_root="$test_root/install"
maintainer_root="$test_root/maintainer"
workspace="$test_root/project-workspace"

mkdir -p "$source_repo"
git -C "$source_repo" init -q -b "$install_branch"
git -C "$source_repo" config user.email agentic-ops-test@example.test
git -C "$source_repo" config user.name "AgenticOps Test"
for product_dir in adapters bootstrap contracts gate policies projects skills workflow; do
  cp -R "$repo_root/$product_dir" "$source_repo/$product_dir"
done
cp -R "$repo_root/internal" "$source_repo/internal"
rm -rf "$source_repo/internal/.local" "$source_repo/internal/.venv" \
  "$source_repo/internal/__pycache__" "$source_repo/internal/story_gate/__pycache__"
cp -R "$repo_root/.githooks" "$source_repo/.githooks"
cp "$repo_root/.agentic-ops-source" "$source_repo/.agentic-ops-source"
cp "$repo_root/.gitignore" "$source_repo/.gitignore"
mkdir -p "$source_repo/adapters/agents/test-agent/templates"
printf '%s\n' '#!/usr/bin/env python3' > "$source_repo/adapters/agents/test-agent/hook.py"
cat > "$source_repo/adapters/agents/test-agent/manifest.json" <<'JSON'
{
  "schema_version": 2,
  "name": "test-agent",
  "adapter_version": 1,
  "entrypoint": "adapters/agents/test-agent/hook.py",
  "hook": {"standard_event": "before_operation", "tool_kinds": ["shell"], "timeout_seconds": 15, "failure_mode": "deny", "native": {"event": "PreToolUse", "tool_matchers": {"shell": "Shell"}}},
  "capabilities": {"decisions": ["allow", "deny"], "ask_fallback": "deny_with_guidance"},
  "artifacts": [{"template": "adapters/agents/test-agent/templates/settings.json", "target": ".test-agent/settings.json"}],
  "launch": {"mode": "command", "command": "test-agent-cli", "message": "测试 Agent 已接线。"},
  "skill_target": null
}
JSON
printf '{"hooks":{"__AGENTIC_OPS_HOOK_NATIVE_EVENT__":[{"matcher":"__AGENTIC_OPS_HOOK_NATIVE_TOOL_MATCHER__","hooks":[{"type":"command","command":"python3 __AGENTIC_OPS_HOME__/adapters/agents/test-agent/hook.py","timeout":"__AGENTIC_OPS_HOOK_TIMEOUT_SECONDS__"}]}]}}\n' \
  > "$source_repo/adapters/agents/test-agent/templates/settings.json"
cp "$repo_root/agenticops" "$source_repo/agenticops"
chmod +x "$source_repo/agenticops"
git -C "$source_repo" add .agentic-ops-source .gitignore .githooks agenticops adapters bootstrap \
  contracts gate policies projects skills workflow internal
git -C "$source_repo" commit -qm "initial"
git -C "$source_repo" branch "$source_branch"

git clone -q "$source_repo" "$maintainer_root"
setup_bin="$test_root/setup-bin"
mkdir -p "$setup_bin"
printf '%s\n' \
  '#!/usr/bin/env bash' \
  'set -eu' \
  'test "$1" = sync' \
  'mkdir -p "$UV_PROJECT_ENVIRONMENT"' \
  > "$setup_bin/uv"
chmod +x "$setup_bin/uv"
PATH="$setup_bin:$PATH" "$maintainer_root/agenticops" setup >/dev/null
"$maintainer_root/agenticops" --help | grep -F '维护：源码产品根目录' >/dev/null
"$maintainer_root/agenticops" --help | grep -F '使用：安装产品根目录' >/dev/null
test "$(git -C "$maintainer_root" branch --show-current)" = develop
test -d "$maintainer_root/.local/venv/internal"
test "$(python3 "$maintainer_root/bootstrap/product_state.py" --product-root "$maintainer_root" read --field mode)" = source
test "$(python3 "$maintainer_root/bootstrap/product_state.py" --product-root "$maintainer_root" read --field tracking_branch)" = develop
test "$(python3 "$maintainer_root/bootstrap/product_version.py" --product-root "$maintainer_root")" = \
  "develop-untagged-1-$(git -C "$maintainer_root" rev-parse --short=8 HEAD)"
test -x "$(git -C "$maintainer_root" config --get core.hooksPath)/pre-commit"
test -f "$maintainer_root/.local/maintenance-skill-wiring.json"
for agent_skill_root in .agents/skills .claude/skills; do
  for maintenance_skill in ao-test-takeover ao-ws-init; do
    skill_link="$maintainer_root/$agent_skill_root/$maintenance_skill"
    test -L "$skill_link"
    test "$(python3 -c 'from pathlib import Path; import sys; print(Path(sys.argv[1]).resolve())' "$skill_link")" = \
      "$(python3 -c 'from pathlib import Path; import sys; print(Path(sys.argv[1]).resolve())' \
        "$maintainer_root/skills/$maintenance_skill")"
  done
done
"$maintainer_root/agenticops" doctor --workspace "$maintainer_root" >/dev/null
rm "$maintainer_root/.agents/skills/ao-ws-init"
if "$maintainer_root/agenticops" doctor --workspace "$maintainer_root" >/dev/null 2>&1; then
  printf '维护 Skill 链接漂移未被 doctor 发现\n' >&2
  exit 1
fi
PATH="$setup_bin:$PATH" "$maintainer_root/agenticops" update >/dev/null
test -L "$maintainer_root/.agents/skills/ao-ws-init"
rm "$maintainer_root/.agents/skills/ao-ws-init"
printf 'project owned\n' > "$maintainer_root/.agents/skills/ao-ws-init"
if python3 "$maintainer_root/bootstrap/skill_wiring.py" \
    --product-root "$maintainer_root" --refresh >/dev/null 2>&1; then
  printf '维护 Skill 接线覆盖了已有普通文件\n' >&2
  exit 1
fi
grep -Fx 'project owned' "$maintainer_root/.agents/skills/ao-ws-init" >/dev/null
rm "$maintainer_root/.agents/skills/ao-ws-init"
python3 "$maintainer_root/bootstrap/skill_wiring.py" \
  --product-root "$maintainer_root" --refresh >/dev/null
source_workspace="$test_root/source-workspace"
"$maintainer_root/agenticops" init --workspace "$source_workspace" \
  --project tapdata --agent test-agent >/dev/null
"$maintainer_root/agenticops" doctor --workspace "$source_workspace" >/dev/null
"$source_workspace/agenticops" doctor >/dev/null
printf '{"note":"changed","hooks":{"__AGENTIC_OPS_HOOK_NATIVE_EVENT__":[{"matcher":"__AGENTIC_OPS_HOOK_NATIVE_TOOL_MATCHER__","hooks":[{"type":"command","command":"python3 __AGENTIC_OPS_HOME__/adapters/agents/test-agent/hook.py","timeout":"__AGENTIC_OPS_HOOK_TIMEOUT_SECONDS__"}]}]}}\n' \
  > "$maintainer_root/adapters/agents/test-agent/templates/settings.json"
if "$maintainer_root/agenticops" doctor --workspace "$source_workspace" >/dev/null 2>&1; then
  printf '源码变更后工作空间漂移未被识别\n' >&2
  exit 1
fi
"$maintainer_root/agenticops" repair --workspace "$source_workspace" >/dev/null
grep -F '"changed"' "$source_workspace/.test-agent/settings.json" >/dev/null
git -C "$maintainer_root" checkout -q -- adapters/agents/test-agent/templates/settings.json

git -C "$source_repo" switch -q "$source_branch"
printf 'source update\n' > "$source_repo/SOURCE-NEXT"
mkdir -p "$source_repo/skills/fixture-maintenance"
printf '%s\n' \
  '---' \
  'name: fixture-maintenance' \
  'description: 测试维护 Skill 生命周期接线。' \
  '---' \
  '' \
  '# Fixture maintenance' \
  > "$source_repo/skills/fixture-maintenance/SKILL.md"
git -C "$source_repo" add SOURCE-NEXT skills/fixture-maintenance/SKILL.md
git -C "$source_repo" commit -qm "source next"
source_update_output="$test_root/source-update-output"
PATH="$setup_bin:$PATH" "$source_workspace/agenticops" update > "$source_update_output"
test -f "$maintainer_root/SOURCE-NEXT"
grep -F '工作面=维护' "$source_update_output" >/dev/null
grep -F '请执行 agenticops workspace repair --all' "$source_update_output" >/dev/null
test "$(python3 "$maintainer_root/bootstrap/product_state.py" --product-root "$maintainer_root" read --field current_ref)" = \
  "$(git -C "$maintainer_root" rev-parse HEAD)"
test -L "$maintainer_root/.agents/skills/fixture-maintenance"
test -L "$maintainer_root/.claude/skills/fixture-maintenance"

git -C "$source_repo" rm -qr skills/fixture-maintenance
git -C "$source_repo" commit -qm "remove maintenance skill"
PATH="$setup_bin:$PATH" "$maintainer_root/agenticops" update > "$source_update_output"
test ! -e "$maintainer_root/.agents/skills/fixture-maintenance"
test ! -e "$maintainer_root/.claude/skills/fixture-maintenance"

git -C "$maintainer_root" config user.email agentic-ops-test@example.test
git -C "$maintainer_root" config user.name "AgenticOps Test"
printf 'local ahead\n' > "$maintainer_root/LOCAL-AHEAD"
git -C "$maintainer_root" add LOCAL-AHEAD
local_parent="$(git -C "$maintainer_root" rev-parse HEAD)"
local_tree="$(git -C "$maintainer_root" write-tree)"
local_commit="$(printf 'local ahead\n' | git -C "$maintainer_root" commit-tree "$local_tree" -p "$local_parent")"
git -C "$maintainer_root" update-ref refs/heads/develop "$local_commit" "$local_parent"
PATH="$setup_bin:$PATH" "$maintainer_root/agenticops" update > "$source_update_output"
grep -F '维护分支本地领先 1 个提交；update 不会自动推送' "$source_update_output" >/dev/null
mkdir "$maintainer_root/.local/lifecycle.lock"
printf '%s\n' "$$" > "$maintainer_root/.local/lifecycle.lock/owner"
printf 'test\n' > "$maintainer_root/.local/lifecycle.lock/operation"
if PATH="$setup_bin:$PATH" "$maintainer_root/agenticops" update >/dev/null 2>&1; then
  printf '维护工作面并发生命周期更新未被拒绝\n' >&2
  exit 1
fi
rm -f "$maintainer_root/.local/lifecycle.lock/owner" \
  "$maintainer_root/.local/lifecycle.lock/operation"
rmdir "$maintainer_root/.local/lifecycle.lock"
"$maintainer_root/agenticops" repair --workspace "$source_workspace" >/dev/null
python3 - "$source_workspace/.agenticops/init.json" "$maintainer_root" <<'PY'
import json
import subprocess
import sys
from pathlib import Path

document = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
head = subprocess.check_output(
    ["git", "-C", sys.argv[2], "rev-parse", "HEAD"], text=True
).strip()
assert document["product_ref"] == head
PY

git -C "$maintainer_root" switch -qc feature/update-boundary
if PATH="$setup_bin:$PATH" "$maintainer_root/agenticops" update >/dev/null 2>&1; then
  printf '维护工作面在非跟踪分支执行了 update\n' >&2
  exit 1
fi
git -C "$maintainer_root" switch -q develop
printf '\n# dirty\n' >> "$maintainer_root/agenticops"
if PATH="$setup_bin:$PATH" "$maintainer_root/agenticops" update >/dev/null 2>&1; then
  printf '维护工作面有未提交修改时执行了 update\n' >&2
  exit 1
fi
git -C "$maintainer_root" checkout -q -- agenticops
git -C "$source_repo" switch -q "$install_branch"

bash "$repo_root/bootstrap/install.sh" \
  --install-home "$install_root" --repository "$source_repo" --branch "$install_branch"

test -f "$install_root/contracts/gate-request.schema.json"
test -f "$install_root/gate/runner.py"
test -f "$install_root/policies/defect-repair-strategies.json"
test -f "$install_root/workflow/repair_strategy.py"
test -x "$install_root/agenticops"
test -f "$maintainer_root/skills/ao-test-takeover/SKILL.md"
test -f "$maintainer_root/skills/ao-ws-init/SKILL.md"
test ! -e "$install_root/skills/ao-test-takeover"
test ! -e "$install_root/skills/ao-ws-init"
test ! -e "$install_root/.local/maintenance-skill-wiring.json"
test ! -e "$install_root/.agents/skills/ao-test-takeover"
test ! -e "$install_root/.claude/skills/ao-test-takeover"
test ! -e "$install_root/internal"
test -f "$install_root/.local/product.json"
test ! -e "$install_root/.local/repository-pool.json"
test "$(python3 "$install_root/bootstrap/product_state.py" --product-root "$install_root" read --field tracking_branch)" = "$install_branch"
test "$(python3 "$install_root/bootstrap/product_version.py" --product-root "$install_root")" = \
  "$install_branch-untagged-1-$(git -C "$install_root" rev-parse --short=8 HEAD)"
if PATH="$setup_bin:$PATH" "$install_root/agenticops" setup >/dev/null 2>&1; then
  printf '安装产品根目录被错误切换为源码维护模式\n' >&2
  exit 1
fi
if "$install_root/agenticops" init --workspace "$install_root" >/dev/null 2>&1; then
  printf '产品根目录被错误初始化为项目工作空间\n' >&2
  exit 1
fi

collision_workspace="$test_root/collision-workspace"
mkdir -p "$collision_workspace"
printf 'project owned\n' > "$collision_workspace/AGENTS.md"
if "$install_root/agenticops" init --workspace "$collision_workspace" >/dev/null 2>&1; then
  printf '工作目录初始化覆盖了项目自有 AGENTS.md\n' >&2
  exit 1
fi
grep -Fx 'project owned' "$collision_workspace/AGENTS.md" >/dev/null
test ! -e "$collision_workspace/.agenticops"

# init 必须在任何写入前拒绝中间目录 symlink；只允许最终 Skill 节点按声明接线。
symlink_outside="$test_root/symlink-outside"
init_symlink_workspace="$test_root/init-symlink-workspace"
mkdir -p "$symlink_outside/init" "$init_symlink_workspace/.agents"
ln -s "$symlink_outside/init" "$init_symlink_workspace/.agents/skills"
if "$install_root/agenticops" init --workspace "$init_symlink_workspace" \
    --agent codex >/dev/null 2>&1; then
  printf 'init 经由 Skill 父目录 symlink 写出了工作空间\n' >&2
  exit 1
fi
test ! -e "$symlink_outside/init/tapdata-task"
test ! -e "$init_symlink_workspace/AGENTS.md"
test ! -e "$init_symlink_workspace/.agenticops"

# repair 必须拒绝已有 Skill 父目录被替换成 symlink，不能在外部重建最终接线。
repair_symlink_workspace="$test_root/repair-symlink-workspace"
mkdir -p "$symlink_outside/repair"
"$install_root/agenticops" init --workspace "$repair_symlink_workspace" \
  --agent codex >/dev/null
repair_skill_target="$(readlink "$repair_symlink_workspace/.agents/skills/tapdata-task")"
for generated_skill in "$repair_symlink_workspace/.agents/skills/"*; do
  rm "$generated_skill"
done
rmdir "$repair_symlink_workspace/.agents/skills"
ln -s "$symlink_outside/repair" "$repair_symlink_workspace/.agents/skills"
ln -s "$repair_skill_target" "$symlink_outside/repair/tapdata-task"
if "$install_root/agenticops" repair --workspace "$repair_symlink_workspace" \
    >/dev/null 2>&1; then
  printf 'repair 接受了 Skill 父目录 symlink\n' >&2
  exit 1
fi
test -L "$symlink_outside/repair/tapdata-task"
test -f "$repair_symlink_workspace/.agenticops/workspace.json"

# detach 预检同样必须逐级检查，不能删除 symlink 父目录外的同名最终接线。
detach_symlink_workspace="$test_root/detach-symlink-workspace"
mkdir -p "$symlink_outside/detach"
"$install_root/agenticops" init --workspace "$detach_symlink_workspace" \
  --agent codex >/dev/null
detach_skill_target="$(readlink "$detach_symlink_workspace/.agents/skills/tapdata-task")"
for generated_skill in "$detach_symlink_workspace/.agents/skills/"*; do
  rm "$generated_skill"
done
rmdir "$detach_symlink_workspace/.agents/skills"
ln -s "$symlink_outside/detach" "$detach_symlink_workspace/.agents/skills"
ln -s "$detach_skill_target" "$symlink_outside/detach/tapdata-task"
if "$install_root/agenticops" workspace detach \
    --workspace "$detach_symlink_workspace" --yes >/dev/null 2>&1; then
  printf 'detach 接受了 Skill 父目录 symlink\n' >&2
  exit 1
fi
test -L "$symlink_outside/detach/tapdata-task"
test -f "$detach_symlink_workspace/.agenticops/workspace.json"

# 校验完成后父目录才被替换的确定性 TOCTOU 回归：init/repair/detach 的最终副作用
# 必须仍锚定已打开的 workspace 目录 FD，且不得写删外部目录。
race_outside="$test_root/race-outside"
race_init_workspace="$test_root/race-init-workspace"
race_repair_workspace="$test_root/race-repair-workspace"
race_detach_workspace="$test_root/race-detach-workspace"
mkdir -p "$race_outside/init" "$race_outside/repair" "$race_outside/detach" \
  "$race_init_workspace/.agents/skills"
"$install_root/agenticops" init --workspace "$race_repair_workspace" --agent codex >/dev/null
"$install_root/agenticops" init --workspace "$race_detach_workspace" --agent codex >/dev/null
printf 'outside sentinel\n' > "$race_outside/detach/tapdata-task"
python3 - "$install_root" "$race_init_workspace" "$race_repair_workspace" \
  "$race_detach_workspace" "$race_outside" <<'PY'
import os
import sys
from pathlib import Path

install_root = Path(sys.argv[1])
init_workspace = Path(sys.argv[2])
repair_workspace = Path(sys.argv[3])
detach_workspace = Path(sys.argv[4])
outside = Path(sys.argv[5])
sys.path.insert(0, str(install_root))
sys.path.insert(0, str(install_root / "bootstrap"))

import render
from bootstrap import workspace_registry


def render_race(workspace, destination, refresh):
    original = render.remove_stale_artifacts

    def swap_after_preflight(current, owned, targets, tree):
        original(current, owned, targets, tree)
        skills = workspace / ".agents" / "skills"
        held = workspace / ".agents" / "skills-held"
        skills.rename(held)
        skills.symlink_to(destination, target_is_directory=True)

    render.remove_stale_artifacts = swap_after_preflight
    argv = [
        "render.py", "--install-home", str(install_root), "--workspace", str(workspace),
        "--agent", "codex",
    ]
    if refresh:
        argv = [
            "render.py", "--install-home", str(install_root), "--workspace", str(workspace),
            "--refresh",
        ]
    previous = sys.argv
    sys.argv = argv
    try:
        try:
            render.main()
        except SystemExit as error:
            assert error.code == 2
        else:
            raise AssertionError("父目录替换后 render 未失败关闭")
    finally:
        sys.argv = previous
        render.remove_stale_artifacts = original


render_race(init_workspace, outside / "init", False)
render_race(repair_workspace, outside / "repair", True)
assert not (outside / "init" / "tapdata-task").exists()
assert not (outside / "repair" / "tapdata-task").exists()

original_preflight = workspace_registry.detach_preflight

def swap_after_detach_preflight(product_root, workspace, purge=False, tree=None):
    result = original_preflight(product_root, workspace, purge=purge, tree=tree)
    skills = detach_workspace / ".agents" / "skills"
    held = detach_workspace / ".agents" / "skills-held"
    skills.rename(held)
    skills.symlink_to(outside / "detach", target_is_directory=True)
    return result

workspace_registry.detach_preflight = swap_after_detach_preflight
try:
    try:
        workspace_registry.detach(install_root, detach_workspace)
    except ValueError as error:
        assert "已被替换" in str(error)
    else:
        raise AssertionError("父目录替换后 detach 未失败关闭")
finally:
    workspace_registry.detach_preflight = original_preflight

assert (outside / "detach" / "tapdata-task").read_text(encoding="utf-8") == "outside sentinel\n"
PY

"$install_root/agenticops" init --workspace "$workspace"

test -f "$workspace/.agenticops/workspace.json"
test -f "$workspace/.agenticops/init.json"
test -x "$workspace/agenticops"
test -f "$workspace/AGENTS.md"
test -f "$workspace/CLAUDE.md"
test -f "$workspace/.mcp.json"
test ! -e "$workspace/.claude/settings.json"
test ! -e "$workspace/.codex/hooks.json"
test -f "$workspace/.test-agent/settings.json"
test -L "$workspace/.agents/skills/tapdata-task"
test -L "$workspace/.claude/skills/tapdata-task"
test ! -e "$workspace/.agents/skills/ao-test-takeover"
test ! -e "$workspace/.claude/skills/ao-test-takeover"
test ! -e "$workspace/.agents/skills/ao-ws-init"
test ! -e "$workspace/.claude/skills/ao-ws-init"
"$install_root/agenticops" workspace list | grep -F -- "$workspace" >/dev/null
install_root_physical="$(cd "$install_root" && pwd -P)"
grep -F '@AGENTS.md' "$workspace/CLAUDE.md" >/dev/null
grep -F 'Product Project：`tapdata`' "$workspace/AGENTS.md" >/dev/null
grep -F 'python3 '"$install_root_physical"'/workflow/task.py status --issue-key <JIRA-KEY>' "$workspace/AGENTS.md" >/dev/null
grep -F 'python3 '"$install_root_physical"'/workflow/task.py repository context --issue-key <JIRA-KEY>' "$workspace/AGENTS.md" >/dev/null
grep -F '必须先读取当前项目 `.agents/skills/`' "$workspace/AGENTS.md" >/dev/null
grep -F 'memory 只能作为历史线索' "$workspace/AGENTS.md" >/dev/null
grep -F '接管或继续成功只是流程恢复点' "$workspace/AGENTS.md" >/dev/null
grep -F '远程候选参考' "$workspace/AGENTS.md" >/dev/null
grep -F 'Workflow 在本地状态变更处执行流程门禁' "$workspace/AGENTS.md" >/dev/null
grep -F '生成 Q2 方案时应用返回的 `planning_guidance`' "$workspace/AGENTS.md" >/dev/null
grep -F '完整工程基线' "$workspace/AGENTS.md" >/dev/null
grep -F 'current-task.json' "$workspace/AGENTS.md" >/dev/null
if "$workspace/agenticops" --help | grep -F 'agenticops task' >/dev/null; then
  printf '统一入口错误暴露了任务 Runtime\n' >&2
  exit 1
fi
python3 - "$workspace/.mcp.json" <<'PY'
import json
import sys
from pathlib import Path

servers = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))["mcpServers"]
assert servers == {
    "atlassian": {"type": "http", "url": "https://mcp.atlassian.com/v1/mcp/authv2"},
}
PY
python3 - "$workspace" "$install_root" <<'PY'
import os
import sys
from pathlib import Path

workspace = Path(sys.argv[1])
skill = Path(sys.argv[2]) / "projects/tapdata/skills/tapdata-task"
for relative in (".agents/skills/tapdata-task", ".claude/skills/tapdata-task"):
    link = workspace / relative
    assert link.is_symlink()
    assert not Path(os.readlink(link)).is_absolute()
    assert link.resolve() == skill.resolve()
    assert (link / "SKILL.md").is_file()
PY
python3 - "$install_root" <<'PY'
import ast
import json
import sys
from pathlib import Path

root = Path(sys.argv[1])
manifest = json.loads((root / "adapters/agents/codex/manifest.json").read_text(encoding="utf-8"))
tree = ast.parse((root / "adapters/agents/codex/hook.py").read_text(encoding="utf-8"))
versions = [
    node.value.value
    for node in tree.body
    if isinstance(node, ast.Assign)
    and any(isinstance(target, ast.Name) and target.id == "ADAPTER_VERSION" for target in node.targets)
    and isinstance(node.value, ast.Constant)
    and type(node.value.value) is int
]
assert versions == [manifest["adapter_version"]]

mappings = json.loads((root / "adapters/tools/mcp-operations.json").read_text(encoding="utf-8"))
assert "readonly_tools" not in mappings
assert "readonly_prefixes" not in mappings
assert set(mappings["mappings"]) == {"github", "atlassian"}

profile = json.loads((root / "projects/tapdata/profile.json").read_text(encoding="utf-8"))
transition = profile["transitions"]["start_progress"]
assert transition == {
    "name": "Start Investigation",
    "id": "421",
    "from": ["Analyzed"],
    "to": "In Progress",
}
assert profile["statuses"]["Analyzed"] == "waiting_takeover"
task_workflow = profile["workflows_by_issue_type"]
assert task_workflow == [{
    "issue_type": {"id": "10008", "name": "任务"},
    "statuses": [
        {"id": "10029", "name": "待办", "stage": "waiting_takeover"},
        {"id": "3", "name": "正在进行", "stage": "implementation"},
    ],
    "transitions": {
        "start_progress": {
            "name": "Work started", "id": "61",
            "from": {"id": "10029", "name": "待办"},
            "to": {"id": "3", "name": "正在进行"},
        }
    },
}]
PY
python3 - "$workspace/.agenticops/workspace.json" "$workspace/.agenticops/init.json" "$install_root" <<'PY'
import json
import sys
from pathlib import Path

binding = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
initialization = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
assert binding["schema_version"] == 3
assert binding["product_root"] == str(Path(sys.argv[3]).resolve())
assert len(binding["workspace_id"]) == 32
assert binding["project"] == "tapdata"
assert binding["agents"] == ["claude", "codex", "test-agent"]
assert "repository_pool" not in binding
assert not (Path(sys.argv[1]).parent / "tasks").exists()
assert (Path(sys.argv[1]).parent / "current-task.json").is_file()
assert initialization["schema_version"] == 2
artifacts = {item["path"]: item for item in initialization["artifacts"]}
assert {"AGENTS.md", "agenticops", ".mcp.json", "CLAUDE.md", ".test-agent/settings.json", ".agents/skills/tapdata-task", ".claude/skills/tapdata-task"} <= set(artifacts)
assert not {".claude/settings.json", ".codex/hooks.json"} & set(artifacts)
assert artifacts[".agents/skills/tapdata-task"]["kind"] == "symlink"
assert artifacts[".claude/skills/tapdata-task"]["kind"] == "symlink"
assert ".agents/skills/ao-test-takeover" not in artifacts
assert ".claude/skills/ao-test-takeover" not in artifacts
assert ".agents/skills/ao-ws-init" not in artifacts
assert ".claude/skills/ao-ws-init" not in artifacts
PY
python3 - "$workspace/.claude/settings.json" "$workspace/.codex/hooks.json" "$install_root" <<'PY'
import json
import sys
from pathlib import Path

install_root = Path(sys.argv[3])
for config_path, agent in ((Path(sys.argv[1]), "claude"), (Path(sys.argv[2]), "codex")):
    assert not config_path.exists()
    manifest = json.loads(
        (install_root / "adapters" / "agents" / agent / "manifest.json").read_text(encoding="utf-8")
    )
    assert not any(artifact["target"] == str(config_path.relative_to(config_path.parent.parent))
                   for artifact in manifest["artifacts"])
PY
python3 - "$workspace/.test-agent/settings.json" <<'PY'
import json
import sys
from pathlib import Path

document = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
handler = document["hooks"]["PreToolUse"][0]["hooks"][0]
assert document["hooks"]["PreToolUse"][0]["matcher"] == "Shell"
assert handler["timeout"] == 15
PY
"$install_root/agenticops" doctor --workspace "$workspace" >/dev/null
"$workspace/agenticops" doctor >/dev/null
test ! -e "$HOME/.codex/skills"
test ! -e "$HOME/.agents/skills"
test ! -e "$HOME/.claude/skills"

rm "$workspace/.agents/skills/tapdata-task"
if "$install_root/agenticops" doctor --workspace "$workspace" >/dev/null 2>&1; then
  printf '工作空间 Skill 链接漂移未被 doctor 发现\n' >&2
  exit 1
fi
"$install_root/agenticops" repair --workspace "$workspace" >/dev/null
test -L "$workspace/.agents/skills/tapdata-task"

printf 'drift\n' > "$workspace/AGENTS.md"
if "$install_root/agenticops" doctor --workspace "$workspace" >/dev/null 2>&1; then
  printf '工作目录漂移未被 doctor 发现\n' >&2
  exit 1
fi
"$install_root/agenticops" repair --workspace "$workspace" >/dev/null
"$install_root/agenticops" doctor --workspace "$workspace" >/dev/null
"$install_root/agenticops" workspace clean --workspace "$workspace" --generated-only >/dev/null
"$install_root/agenticops" doctor --workspace "$workspace" >/dev/null

printf 'drift\n' > "$workspace/agenticops"
if "$install_root/agenticops" doctor --workspace "$workspace" >/dev/null 2>&1; then
  printf '工作空间入口漂移未被 doctor 发现\n' >&2
  exit 1
fi
"$install_root/agenticops" repair --workspace "$workspace" >/dev/null
test -x "$workspace/agenticops"
"$workspace/agenticops" doctor >/dev/null

entry_migration_workspace="$test_root/entry-migration-workspace"
"$install_root/agenticops" init --workspace "$entry_migration_workspace" --agent codex >/dev/null
python3 - "$entry_migration_workspace" <<'PY'
import json
import sys
from pathlib import Path

workspace = Path(sys.argv[1])
old_entry = workspace / ".agenticops" / "agenticops"
new_entry = workspace / "agenticops"
new_entry.rename(old_entry)
init_path = workspace / ".agenticops" / "init.json"
document = json.loads(init_path.read_text(encoding="utf-8"))
for artifact in document["artifacts"]:
    if artifact["path"] == "agenticops":
        artifact["path"] = ".agenticops/agenticops"
        break
else:
    raise AssertionError("init.json 缺少工作空间入口")
init_path.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
PY
"$install_root/agenticops" repair --workspace "$entry_migration_workspace" >/dev/null
test ! -e "$entry_migration_workspace/.agenticops/agenticops"
test -x "$entry_migration_workspace/agenticops"
"$entry_migration_workspace/agenticops" doctor >/dev/null

subset_workspace="$test_root/subset-workspace"
"$install_root/agenticops" init --workspace "$subset_workspace" --agent codex >/dev/null
test ! -e "$subset_workspace/.codex/hooks.json"
test -L "$subset_workspace/.agents/skills/tapdata-task"
test ! -e "$subset_workspace/CLAUDE.md"
test ! -e "$subset_workspace/.claude/settings.json"
test ! -e "$subset_workspace/.claude/skills"
collision_workspace="$test_root/root-entry-collision-workspace"
mkdir -p "$collision_workspace"
printf 'user owned\n' > "$collision_workspace/agenticops"
if "$install_root/agenticops" init --workspace "$collision_workspace" --agent codex >/dev/null 2>&1; then
  printf '工作空间根入口覆盖了已有用户文件\n' >&2
  exit 1
fi
grep -Fx 'user owned' "$collision_workspace/agenticops" >/dev/null
if "$install_root/agenticops" init --workspace "$test_root/unknown-workspace" --agent missing-agent >/dev/null 2>&1; then
  printf '未知 Agent 被错误接受\n' >&2
  exit 1
fi
# 旧版托管 Codex Hook 必须显式迁移，普通 repair 不得静默撤除控制。
legacy_codex_workspace="$test_root/legacy-codex-workspace"
mkdir -p "$legacy_codex_workspace/.agenticops" "$legacy_codex_workspace/.codex"
printf 'legacy codex hook\n' > "$legacy_codex_workspace/.codex/agenticops-hooks.example.json"
legacy_codex_hash="$(file_digest "$legacy_codex_workspace/.codex/agenticops-hooks.example.json")"
python3 - "$legacy_codex_workspace" "$install_root" "$legacy_codex_hash" <<'PY'
import json
import sys
from pathlib import Path

workspace = Path(sys.argv[1])
install_root = Path(sys.argv[2]).resolve()
digest = sys.argv[3]
(workspace / ".agenticops" / "workspace.json").write_text(
    json.dumps(
        {
            "schema_version": 3,
            "workspace_id": "a" * 32,
            "product_root": str(install_root),
            "project": "tapdata",
            "agents": ["codex"],
        },
        ensure_ascii=False,
        indent=2,
    )
    + "\n",
    encoding="utf-8",
)
(workspace / ".agenticops" / "init.json").write_text(
    json.dumps(
        {
            "schema_version": 1,
            "product_ref": "legacy",
            "workspace_state_epoch": json.loads((install_root / "contracts/workspace-state-compatibility.json").read_text())["workspace_state_epoch"],
            "artifacts": [
                {
                    "path": ".codex/agenticops-hooks.example.json",
                    "sha256": digest,
                }
            ],
        },
        ensure_ascii=False,
        indent=2,
    )
    + "\n",
    encoding="utf-8",
)
PY
if "$install_root/agenticops" repair --workspace "$legacy_codex_workspace" >/dev/null 2>&1; then
  printf '普通 repair 不得静默撤除旧 Hook\n' >&2
  exit 1
fi
test "$(file_digest "$legacy_codex_workspace/.codex/agenticops-hooks.example.json")" = "$legacy_codex_hash"
# 同时覆盖现役旧 Hook、部分漂移及启动路径；迁移预检失败不得先删除另一项。
python3 - "$legacy_codex_workspace" <<'PY'
import hashlib
import json
import sys
from pathlib import Path
workspace = Path(sys.argv[1])
hook = workspace / ".codex/hooks.json"
hook.write_text('{"hooks": {}}\n')
manifest = workspace / ".agenticops/init.json"
document = json.loads(manifest.read_text())
document["artifacts"].append({"path": ".codex/hooks.json", "kind": "file",
                              "sha256": hashlib.sha256(hook.read_bytes()).hexdigest()})
manifest.write_text(json.dumps(document))
(workspace / ".agenticops/preserved-state.json").write_text('{"run_id":"run-preserved"}\n')
hook.write_text('user changed hook\n')
PY
if "$install_root/agenticops" repair --workspace "$legacy_codex_workspace" --accept-checkpoint-migration >/dev/null 2>&1; then
  printf '显式迁移不得覆盖用户修改的 Hook\n' >&2
  exit 1
fi
test "$(file_digest "$legacy_codex_workspace/.codex/agenticops-hooks.example.json")" = "$legacy_codex_hash"
grep -Fx 'user changed hook' "$legacy_codex_workspace/.codex/hooks.json" >/dev/null
if "$install_root/agenticops" start --agent codex --workspace "$legacy_codex_workspace" >/dev/null 2>&1; then
  printf 'start 不得隐式迁移旧 Hook\n' >&2
  exit 1
fi
test -f "$legacy_codex_workspace/.codex/hooks.json"
printf '{"hooks": {}}\n' > "$legacy_codex_workspace/.codex/hooks.json"
"$install_root/agenticops" repair --workspace "$legacy_codex_workspace" --accept-checkpoint-migration >/dev/null
test ! -e "$legacy_codex_workspace/.codex/hooks.json"
test ! -e "$legacy_codex_workspace/.codex/agenticops-hooks.example.json"
"$install_root/agenticops" doctor --workspace "$legacy_codex_workspace" >/dev/null
python3 - "$legacy_codex_workspace" <<'PY'
import json
import sys
from pathlib import Path
workspace = Path(sys.argv[1])
assert json.loads((workspace / ".agenticops/preserved-state.json").read_text())["run_id"] == "run-preserved"
migration = json.loads((workspace / ".agenticops/init.json").read_text())["checkpoint_migration"]
assert migration["from_product_ref"] == "legacy"
assert migration["accepted_at"]
assert set(migration["retired_artifacts"]) == {".codex/hooks.json", ".codex/agenticops-hooks.example.json"}
PY

if "$install_root/agenticops" start --agent test-agent --workspace "$subset_workspace" >/dev/null 2>&1; then
  printf '工作空间启动了未绑定的 Agent\n' >&2
  exit 1
fi
workspace_help="$test_root/workspace-help"
if "$install_root/agenticops" workspace detach > "$workspace_help" 2>&1; then
  printf '缺少工作空间目标的 detach 被错误接受\n' >&2
  exit 1
fi
grep -F -- '--workspace WORKSPACE | --all' "$workspace_help" >/dev/null

detached_workspace="$test_root/detached-workspace"
"$install_root/agenticops" init --workspace "$detached_workspace" --agent codex >/dev/null
if "$install_root/agenticops" workspace detach --workspace "$detached_workspace" >/dev/null 2>&1; then
  printf '非交互 detach 被错误接受\n' >&2
  exit 1
fi
"$install_root/agenticops" workspace detach --workspace "$detached_workspace" --yes >/dev/null
test ! -e "$detached_workspace/.agenticops/workspace.json"
test ! -e "$detached_workspace/.agenticops/init.json"
test ! -e "$detached_workspace/agenticops"
test ! -e "$detached_workspace/.agents"
test ! -e "$detached_workspace/.agenticops"
if "$install_root/agenticops" workspace list | grep -F -- "$detached_workspace" >/dev/null; then
  printf '解绑工作空间仍保留在提示索引\n' >&2
  exit 1
fi

# 无法唯一归属 active 任务的 Gate 判定写入工作空间级 events.jsonl；purge 必须
# 将这个受控审计文件与任务状态一并删除，而非把它误判为未知文件。
unbound_events_workspace="$test_root/unbound-events-workspace"
"$install_root/agenticops" init --workspace "$unbound_events_workspace" --agent codex >/dev/null
python3 - "$install_root" "$unbound_events_workspace" <<'PY'
import sys
from pathlib import Path

install_root = Path(sys.argv[1])
workspace = Path(sys.argv[2])
sys.path.insert(0, str(install_root))

from gate import runner

result = runner.evaluate_request(
    {
        "protocol_version": 1,
        "event": "before_operation",
        "source": {
            "agent": "test-agent",
            "adapter": "test-adapter",
            "adapter_version": 1,
            "tool_kind": "shell",
            "tool_name": "test",
        },
        "cwd": str(workspace),
        "operations": ["unknown_external_write"],
        "target": {},
        "note": "测试无任务归属审计事件",
    }
)
assert result["decision"] == "ask", result
events = workspace / ".agenticops" / "events.jsonl"
assert events.is_file(), events
PY
"$install_root/agenticops" workspace purge \
  --workspace "$unbound_events_workspace" --yes >/dev/null
test ! -e "$unbound_events_workspace/.agenticops"
if "$install_root/agenticops" workspace purge \
    --worksp "$workspace" --yes >/dev/null 2>&1; then
  printf 'workspace purge 未拒绝 workspace 缩写\n' >&2
  exit 1
fi
test -d "$workspace/.agenticops"

# 仅受控的 events.jsonl 可被 purge；其它未知状态及 events.jsonl 的非常规文件
# 形态仍必须失败关闭，且不得触及工作空间外的目标。
unknown_state_workspace="$test_root/unknown-state-workspace"
"$install_root/agenticops" init --workspace "$unknown_state_workspace" --agent codex >/dev/null
printf 'unknown\n' > "$unknown_state_workspace/.agenticops/unknown-state"
if "$install_root/agenticops" workspace purge \
    --workspace "$unknown_state_workspace" --yes >/dev/null 2>&1; then
  printf '未知工作空间状态被错误清理\n' >&2
  exit 1
fi
test -f "$unknown_state_workspace/.agenticops/unknown-state"

event_outside="$test_root/event-outside"
printf 'outside sentinel\n' > "$event_outside"
event_symlink_workspace="$test_root/event-symlink-workspace"
"$install_root/agenticops" init --workspace "$event_symlink_workspace" --agent codex >/dev/null
ln -s "$event_outside" "$event_symlink_workspace/.agenticops/events.jsonl"
if "$install_root/agenticops" workspace purge \
    --workspace "$event_symlink_workspace" --yes >/dev/null 2>&1; then
  printf '符号链接 Gate 审计事件被错误清理\n' >&2
  exit 1
fi
grep -Fx 'outside sentinel' "$event_outside" >/dev/null

event_directory_workspace="$test_root/event-directory-workspace"
"$install_root/agenticops" init --workspace "$event_directory_workspace" --agent codex >/dev/null
mkdir "$event_directory_workspace/.agenticops/events.jsonl"
if "$install_root/agenticops" workspace purge \
    --workspace "$event_directory_workspace" --yes >/dev/null 2>&1; then
  printf '目录 Gate 审计事件被错误清理\n' >&2
  exit 1
fi
test -d "$event_directory_workspace/.agenticops/events.jsonl"

# 新版空闲工位清理与重建、材料保留、并发和路径替换回归。
python3 "$repo_root/tests/test_station_bootstrap.py" --product-root "$install_root"

missing_workspace="$test_root/missing-workspace"
"$install_root/agenticops" init --workspace "$missing_workspace" --agent codex >/dev/null
rm -rf "$missing_workspace"
"$install_root/agenticops" workspace prune --all --yes | grep -F '已注销 1 个无法跟踪的工作空间。' >/dev/null

fake_bin="$test_root/fake-bin"
capture="$test_root/codex-capture"
expected_workspace="$(CDPATH= cd -- "$workspace" && pwd -P)"
mkdir -p "$fake_bin"
printf '%s\n' \
  '#!/usr/bin/env bash' \
  'set -eu' \
  'test "$(pwd -P)" = "$AGENTIC_OPS_EXPECTED_WORKSPACE"' \
  'if [ "${1:-}" = mcp ] && [ "${2:-}" = get ]; then' \
  '  case "$3" in' \
  "    atlassian) printf '%s\\n' '{\"enabled\":true,\"transport\":{\"type\":\"streamable_http\",\"url\":\"https://mcp.atlassian.com/v1/mcp/authv2\"}}' ;;" \
  "    github) printf '%s\\n' '{\"enabled\":true,\"transport\":{\"type\":\"streamable_http\",\"url\":\"https://api.githubcopilot.com/mcp/\"}}' ;;" \
  '  esac' \
  '  exit 0' \
  'fi' \
  'printf "%s\n" "$*" > "$AGENTIC_OPS_CAPTURE"' \
  > "$fake_bin/codex"
chmod +x "$fake_bin/codex"
PATH="$fake_bin:$PATH" \
AGENTIC_OPS_EXPECTED_WORKSPACE="$expected_workspace" \
AGENTIC_OPS_CAPTURE="$capture" \
  "$install_root/agenticops" start --agent codex --workspace "$workspace" -- --model fake >/dev/null
grep -Fx -- '--model fake' "$capture" >/dev/null
PATH="$fake_bin:$PATH" \
AGENTIC_OPS_EXPECTED_WORKSPACE="$expected_workspace" \
AGENTIC_OPS_CAPTURE="$capture" \
  "$workspace/agenticops" start codex -- --model workspace-entry >/dev/null
grep -Fx -- '--model workspace-entry' "$capture" >/dev/null
if "$workspace/agenticops" start codex --agent codex >/dev/null 2>&1; then
  printf 'start 未拒绝重复 Agent ID\n' >&2
  exit 1
fi
if "$workspace/agenticops" start codex TAP-123 >/dev/null 2>&1; then
  printf 'start 未拒绝已移除的 Jira Key 位置参数\n' >&2
  exit 1
fi
test ! -e "$workspace/AGENTS.md.tmp"

task_index_digest="$(file_digest "$workspace/.agenticops/current-task.json")"

printf 'next\n' > "$source_repo/NEXT"
git -C "$source_repo" add NEXT
git -C "$source_repo" commit -qm "next"
installed_update_output="$test_root/installed-update-output"
"$workspace/agenticops" update > "$installed_update_output"
test -f "$install_root/NEXT"
grep -F '工作面=使用' "$installed_update_output" >/dev/null
if "$install_root/agenticops" doctor --workspace "$workspace" >/dev/null 2>&1; then
  printf '产品更新后旧工作目录绑定未被识别为待刷新\n' >&2
  exit 1
fi
"$workspace/agenticops" repair >/dev/null
"$install_root/agenticops" doctor --workspace "$workspace" >/dev/null
test "$(file_digest "$workspace/.agenticops/current-task.json")" = "$task_index_digest"
"$workspace/agenticops" rollback >/dev/null
test ! -f "$install_root/NEXT"
"$workspace/agenticops" repair >/dev/null
"$install_root/agenticops" doctor --workspace "$workspace" >/dev/null
test "$(file_digest "$workspace/.agenticops/current-task.json")" = "$task_index_digest"

python3 - "$source_repo/contracts/workspace-state-compatibility.json" <<'PY'
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
document = json.loads(path.read_text(encoding="utf-8"))
document["workspace_state_epoch"] += 1
document["supported_workspace_state_epochs"] = [document["workspace_state_epoch"]]
path.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
PY
git -C "$source_repo" add contracts/workspace-state-compatibility.json
git -C "$source_repo" commit -qm "incompatible workspace state"
installed_head_before_blocked_update="$(git -C "$install_root" rev-parse HEAD)"
if "$workspace/agenticops" update > "$test_root/incompatible-update-output" 2>&1; then
  printf '存在未清理任务时跨工作空间状态代际升级未被拒绝\n' >&2
  exit 1
fi
grep -F '目标版本包含不兼容的工作空间状态变更' "$test_root/incompatible-update-output" >/dev/null
grep -F '状态代际' "$test_root/incompatible-update-output" >/dev/null
test "$(git -C "$install_root" rev-parse HEAD)" = "$installed_head_before_blocked_update"

printf 'AgenticOps 安装边界验证通过：被测分支=%s，被测提交=%s，安装 fixture 分支=%s\n' \
  "${tested_branch:-detached HEAD}" "$tested_ref" "$install_branch"
