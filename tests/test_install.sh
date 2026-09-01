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
shared_repository_pool="$test_root/shared-repository-pool"

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
grep -F '已知工作空间待刷新' "$source_update_output" >/dev/null
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
  --install-home "$install_root" --repository "$source_repo" --branch "$install_branch" \
  --repository-pool "$shared_repository_pool"

test -f "$install_root/contracts/gate-request.schema.json"
test -f "$install_root/gate/runner.py"
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
test -f "$install_root/.local/repository-pool.json"
test "$(python3 "$install_root/bootstrap/repository_pool.py" --product-root "$install_root" read --field root)" = \
  "$(python3 -c 'from pathlib import Path; import sys; print(Path(sys.argv[1]).resolve())' "$shared_repository_pool")"
test "$(python3 "$install_root/bootstrap/product_state.py" --product-root "$install_root" read --field tracking_branch)" = "$install_branch"
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
test -f "$workspace/.claude/settings.json"
test -f "$workspace/.codex/hooks.json"
test -f "$workspace/.test-agent/settings.json"
test -L "$workspace/.agents/skills/tapdata-task"
test -L "$workspace/.claude/skills/tapdata-task"
test ! -e "$workspace/.agents/skills/ao-test-takeover"
test ! -e "$workspace/.claude/skills/ao-test-takeover"
test ! -e "$workspace/.agents/skills/ao-ws-init"
test ! -e "$workspace/.claude/skills/ao-ws-init"
"$install_root/agenticops" workspace list | grep -F -- "$workspace" >/dev/null
grep -F '@AGENTS.md' "$workspace/CLAUDE.md" >/dev/null
grep -F 'Product Project：`tapdata`' "$workspace/AGENTS.md" >/dev/null
grep -F 'workflow/task.py status --issue-key <JIRA-KEY>' "$workspace/AGENTS.md" >/dev/null
grep -F '必须先读取当前项目 `.agents/skills/`' "$workspace/AGENTS.md" >/dev/null
grep -F 'memory 只能作为历史线索' "$workspace/AGENTS.md" >/dev/null
grep -F '接管、继续或 reset 成功只是流程恢复点' "$workspace/AGENTS.md" >/dev/null
grep -F '远程候选参考' "$workspace/AGENTS.md" >/dev/null
grep -F '首次收到' "$workspace/AGENTS.md" >/dev/null
grep -F '登记完成后立即执行受控 `task.py repository prepare`' \
  "$install_root/projects/tapdata/skills/tapdata-task/SKILL.md" >/dev/null
if "$workspace/agenticops" --help | grep -F 'agenticops task' >/dev/null; then
  printf '统一入口错误暴露了任务 Runtime\n' >&2
  exit 1
fi
grep -F "$install_root/adapters/agents/claude/hook.py" "$workspace/.claude/settings.json" >/dev/null
grep -F "$install_root/adapters/agents/codex/hook.py" "$workspace/.codex/hooks.json" >/dev/null
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
PY
python3 - "$workspace/.agenticops/workspace.json" "$workspace/.agenticops/init.json" "$install_root" "$shared_repository_pool" <<'PY'
import json
import sys
from pathlib import Path

binding = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
initialization = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
assert binding["schema_version"] == 2
assert binding["product_root"] == str(Path(sys.argv[3]).resolve())
assert len(binding["workspace_id"]) == 32
assert binding["project"] == "tapdata"
assert binding["agents"] == ["claude", "codex", "test-agent"]
assert binding["repository_pool"]["source"] == "product-default"
assert binding["repository_pool"]["root"] == str(Path(sys.argv[4]).resolve())
assert initialization["schema_version"] == 2
artifacts = {item["path"]: item for item in initialization["artifacts"]}
assert {"AGENTS.md", "agenticops", "CLAUDE.md", ".claude/settings.json", ".codex/hooks.json", ".test-agent/settings.json", ".agents/skills/tapdata-task", ".claude/skills/tapdata-task"} <= set(artifacts)
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
    document = json.loads(config_path.read_text(encoding="utf-8"))
    manifest = json.loads(
        (install_root / "adapters" / "agents" / agent / "manifest.json").read_text(encoding="utf-8")
    )
    handler = document["hooks"]["PreToolUse"][0]["hooks"][0]
    assert handler["type"] == "command"
    assert handler["command"].startswith('python3 "')
    assert handler["timeout"] == manifest["hook"]["timeout_seconds"]
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
test -f "$subset_workspace/.codex/hooks.json"
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
override_workspace="$test_root/override-workspace"
override_pool="$test_root/override-pool"
"$install_root/agenticops" init --workspace "$override_workspace" --agent codex \
  --repository-pool "$override_pool" >/dev/null
python3 - "$override_workspace/.agenticops/workspace.json" "$override_pool" <<'PY'
import json
import sys
from pathlib import Path

binding = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
assert binding["repository_pool"]["root"] == str(Path(sys.argv[2]).resolve())
assert binding["repository_pool"]["source"] == "workspace-override"
PY
rmdir "$override_pool"
if "$install_root/agenticops" doctor --workspace "$override_workspace" >/dev/null 2>&1; then
  printf 'Source Pool 缺失时 doctor 未失败关闭\n' >&2
  exit 1
fi
frozen_workspace="$test_root/frozen-workspace"
"$install_root/agenticops" init --workspace "$frozen_workspace" --agent codex >/dev/null
new_default_pool="$test_root/new-default-pool"
python3 "$install_root/bootstrap/repository_pool.py" --product-root "$install_root" \
  configure --root "$new_default_pool" >/dev/null
"$install_root/agenticops" repair --workspace "$frozen_workspace" >/dev/null
python3 - "$frozen_workspace/.agenticops/workspace.json" "$shared_repository_pool" <<'PY'
import json
import sys
from pathlib import Path

binding = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
assert binding["repository_pool"]["root"] == str(Path(sys.argv[2]).resolve())
PY
python3 "$install_root/bootstrap/repository_pool.py" --product-root "$install_root" \
  configure --root "$shared_repository_pool" >/dev/null

# 旧版 Codex 接线移除后，刷新会在同一父目录生成 hooks.json；该父目录不能在两次
# 操作之间被删除，否则安全 FD 缓存会将其视作替换并拒绝迁移。
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
            "schema_version": 1,
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
"$install_root/agenticops" repair --workspace "$legacy_codex_workspace" >/dev/null
test -f "$legacy_codex_workspace/.codex/hooks.json"
"$install_root/agenticops" doctor --workspace "$legacy_codex_workspace" >/dev/null

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
python3 "$install_root/workflow/task.py" init \
  --issue-key TAP-555 --task-class technical_task --dir "$detached_workspace" >/dev/null
if "$install_root/agenticops" workspace detach --workspace "$detached_workspace" >/dev/null 2>&1; then
  printf '非交互 detach 被错误接受\n' >&2
  exit 1
fi
"$install_root/agenticops" workspace detach --workspace "$detached_workspace" --yes >/dev/null
test ! -e "$detached_workspace/.agenticops/workspace.json"
test ! -e "$detached_workspace/.agenticops/init.json"
test ! -e "$detached_workspace/agenticops"
test ! -e "$detached_workspace/.agents"
test -f "$detached_workspace/.agenticops/tasks/TAP-555/state.json"
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

purge_workspace="$test_root/purge-workspace"
"$install_root/agenticops" init --workspace "$purge_workspace" --agent codex >/dev/null
python3 "$install_root/workflow/task.py" init \
  --issue-key TAP-556 --task-class technical_task --dir "$purge_workspace" >/dev/null
python3 "$install_root/workflow/task.py" init \
  --issue-key TAP-558 --task-class technical_task --dir "$purge_workspace" >/dev/null
if "$install_root/agenticops" workspace purge --all --yes >/dev/null 2>&1; then
  printf '批量 purge 被错误接受\n' >&2
  exit 1
fi
# purge 从重新预检到最终删除必须只持有一次产品级 task-state 锁。已到达锁边界的并发
# init 必须在 purge 事务内阻塞，释放后因 workspace binding 已删除而失败，不能重建状态。
python3 - "$install_root" "$purge_workspace" <<'PY'
import contextlib
import multiprocessing
import sys
import time
from pathlib import Path
from types import SimpleNamespace

install_root = Path(sys.argv[1])
workspace = Path(sys.argv[2])
marker = workspace.parent / "purge-init-lock-entered"
sys.path.insert(0, str(install_root))

from bootstrap import workspace_registry
from workflow import repository_worktree, task as workflow_task, task_store


def competing_init():
    original_lock = task_store.task_run_lock

    @contextlib.contextmanager
    def marked_lock(base, issue_key):
        marker.write_text("entered\n", encoding="utf-8")
        with original_lock(base, issue_key):
            yield

    task_store.task_run_lock = marked_lock
    arguments = SimpleNamespace(
        issue_key="TAP-559", task_class="technical_task", dir=str(workspace), force=False
    )
    with open("/dev/null", "w", encoding="utf-8") as sink:
        with contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
            result = workflow_task.cmd_init(arguments)
            raise SystemExit(result)


context = multiprocessing.get_context("fork")
process = context.Process(target=competing_init)
original_cleanup = repository_worktree._cleanup_task_locked
cleanup_calls = []


def cleanup_with_competitor(current, issue, *, delete_branches=False):
    cleanup_calls.append(issue)
    if not process.is_alive() and process.exitcode is None:
        process.start()
        deadline = time.monotonic() + 5
        while not marker.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert marker.exists(), "并发 init 未到达 task-state 锁边界"
        time.sleep(0.1)
        assert process.is_alive(), "并发 init 未被 workspace purge 的 task-state 锁阻塞"
        assert not task_store.task_path(workspace, "TAP-559").exists()
    return original_cleanup(current, issue, delete_branches=delete_branches)


repository_worktree._cleanup_task_locked = cleanup_with_competitor
try:
    workspace_registry.detach(install_root, workspace, purge=True)
finally:
    repository_worktree._cleanup_task_locked = original_cleanup

process.join(5)
assert not process.is_alive(), "purge 完成后并发 init 未退出"
assert process.exitcode == 2, "binding 删除后并发 init 未失败关闭：%s" % process.exitcode
assert cleanup_calls == ["TAP-556", "TAP-558"], cleanup_calls
assert not (workspace / ".agenticops").exists(), "并发 init 在 purge 后重建了任务状态"
PY
test ! -e "$purge_workspace/.agenticops"

# registry 最终回读完成后若整个 .agenticops 被换成外部 symlink，递归删除必须通过
# 已打开的 workspace/.agenticops FD 发现替换并停止，不能遍历外部 tasks。
purge_race_workspace="$test_root/purge-race-workspace"
purge_race_outside="$test_root/purge-race-outside"
mkdir -p "$purge_race_outside/tasks"
printf 'outside tasks sentinel\n' > "$purge_race_outside/tasks/sentinel"
"$install_root/agenticops" init --workspace "$purge_race_workspace" --agent codex >/dev/null
python3 "$install_root/workflow/task.py" init \
  --issue-key TAP-560 --task-class technical_task --dir "$purge_race_workspace" >/dev/null
python3 - "$install_root" "$purge_race_workspace" "$purge_race_outside" <<'PY'
import sys
from pathlib import Path

install_root = Path(sys.argv[1])
workspace = Path(sys.argv[2])
outside = Path(sys.argv[3])
sys.path.insert(0, str(install_root))

from bootstrap import workspace_registry
from workflow import task_store

original_load_registry = task_store.load_registry
calls = 0


def replace_after_final_registry_check(base, create=False):
    global calls
    document = original_load_registry(base, create=create)
    calls += 1
    # 第一次是 purge 任务集合，第二次是 cleanup 的 issue 解析，第三次才是
    # registry 锁内的最终回读；返回第三次结果后立即替换整个状态根。
    if calls == 3:
        state = workspace / ".agenticops"
        state.rename(workspace / ".agenticops-held")
        state.symlink_to(outside, target_is_directory=True)
    return document


task_store.load_registry = replace_after_final_registry_check
try:
    try:
        workspace_registry.detach(install_root, workspace, purge=True)
    except ValueError as error:
        assert "父目录已被替换" in str(error)
    else:
        raise AssertionError(".agenticops 最终检查后被替换时 purge 未失败关闭")
finally:
    task_store.load_registry = original_load_registry

assert calls == 3, calls
assert (outside / "tasks" / "sentinel").read_text(encoding="utf-8") == \
    "outside tasks sentinel\n"
assert (workspace / ".agenticops-held" / "tasks" / "TAP-560" / "state.json").is_file()
(workspace / ".agenticops").unlink()
(workspace / ".agenticops-held").rename(workspace / ".agenticops")
workspace_registry.detach(install_root, workspace, purge=True)
assert not (workspace / ".agenticops").exists()
PY

task_purge_workspace="$test_root/task-purge-workspace"
"$install_root/agenticops" init --workspace "$task_purge_workspace" --agent codex >/dev/null
python3 "$install_root/workflow/task.py" init \
  --issue-key TAP-557 --task-class technical_task --dir "$task_purge_workspace" >/dev/null
task_purge_run="$(python3 - "$task_purge_workspace/.agenticops/tasks/TAP-557/state.json" <<'PY'
import json
import sys
from pathlib import Path

print(json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))["run_id"])
PY
)"
python3 "$install_root/workflow/task.py" deactivate \
  --issue-key TAP-557 --dir "$task_purge_workspace" >/dev/null
if python3 "$install_root/workflow/task.py" purge --issue-key TAP-557 \
  --expected-run-id "$task_purge_run" --dir "$task_purge_workspace" >/dev/null 2>&1; then
  printf '任务 purge 缺少 --yes 时被错误接受\n' >&2
  exit 1
fi
if python3 "$install_root/workflow/task.py" purge --issue-key TAP-557 \
  --expected-run-id run-stale --yes --dir "$task_purge_workspace" >/dev/null 2>&1; then
  printf '任务 purge 接受了过期 run_id\n' >&2
  exit 1
fi
python3 "$install_root/workflow/task.py" purge --issue-key TAP-557 \
  --expected-run-id "$task_purge_run" --yes --dir "$task_purge_workspace" >/dev/null
test ! -e "$task_purge_workspace/.agenticops/tasks/TAP-557"
if python3 "$install_root/workflow/task.py" list --dir "$task_purge_workspace" | \
    grep -F 'TAP-557' >/dev/null; then
  printf '已 purge 的任务仍存在于任务注册表\n' >&2
  exit 1
fi
python3 "$install_root/workflow/task.py" --help | grep -F 'purge' >/dev/null

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
if "$workspace/agenticops" start codex TAP-123 --issue-key TAP-999 >/dev/null 2>&1; then
  printf 'start 未拒绝重复 Jira Key\n' >&2
  exit 1
fi
test ! -e "$workspace/AGENTS.md.tmp"

python3 "$install_root/workflow/task.py" init \
  --issue-key TAP-123 --task-class defect_fix --dir "$workspace" >/dev/null
python3 "$install_root/workflow/task.py" init \
  --issue-key TAP-999 --task-class technical_task --dir "$workspace" >/dev/null
test -f "$workspace/.agenticops/tasks/index.json"
test -f "$workspace/.agenticops/tasks/TAP-123/state.json"
test -f "$workspace/.agenticops/tasks/TAP-999/state.json"
python3 "$install_root/workflow/task.py" list --dir "$workspace" | grep -F 'TAP-123：active' >/dev/null
python3 "$install_root/workflow/task.py" list --dir "$workspace" | grep -F 'TAP-999：active' >/dev/null
task_index_digest="$(file_digest "$workspace/.agenticops/tasks/index.json")"
task_123_digest="$(file_digest "$workspace/.agenticops/tasks/TAP-123/state.json")"
task_999_digest="$(file_digest "$workspace/.agenticops/tasks/TAP-999/state.json")"

legacy_workspace="$test_root/legacy-workspace"
"$install_root/agenticops" init --workspace "$legacy_workspace" --agent codex >/dev/null
mkdir -p "$legacy_workspace/.gate"
printf '%s\n' \
  '{"issue_key":"TAP-777","task_class":"technical_task","status":"active"}' \
  > "$legacy_workspace/.gate/task.json"
printf '%s\n' '{"scope":"legacy"}' > "$legacy_workspace/.gate/authorization.json"
printf '%s\n' '{"event":"legacy"}' > "$legacy_workspace/.gate/events.jsonl"
python3 "$install_root/workflow/task.py" list --dir "$legacy_workspace" | grep -F 'TAP-777：active' >/dev/null
test -f "$legacy_workspace/.agenticops/tasks/TAP-777/state.json"
test -f "$legacy_workspace/.agenticops/tasks/TAP-777/authorization.json"
test -f "$legacy_workspace/.agenticops/tasks/TAP-777/events.jsonl"
test ! -e "$legacy_workspace/.gate"

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
test -f "$workspace/.agenticops/tasks/TAP-123/state.json"
test -f "$workspace/.agenticops/tasks/TAP-999/state.json"
test "$(file_digest "$workspace/.agenticops/tasks/index.json")" = "$task_index_digest"
test "$(file_digest "$workspace/.agenticops/tasks/TAP-123/state.json")" = "$task_123_digest"
test "$(file_digest "$workspace/.agenticops/tasks/TAP-999/state.json")" = "$task_999_digest"
"$workspace/agenticops" rollback >/dev/null
test ! -f "$install_root/NEXT"
"$workspace/agenticops" repair >/dev/null
"$install_root/agenticops" doctor --workspace "$workspace" >/dev/null
test "$(file_digest "$workspace/.agenticops/tasks/index.json")" = "$task_index_digest"
test "$(file_digest "$workspace/.agenticops/tasks/TAP-123/state.json")" = "$task_123_digest"
test "$(file_digest "$workspace/.agenticops/tasks/TAP-999/state.json")" = "$task_999_digest"

# 任务启动只追加当前任务已准备的 worktree；purge 同步移除 worktree。
python3 - "$install_root/projects/tapdata/repositories.json" "$source_repo" "$install_branch" <<'PY'
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
document = json.loads(path.read_text(encoding="utf-8"))
entry = document["repositories"]["tapdata/tapdata"]
entry["origin"] = str(Path(sys.argv[2]).resolve())
entry["baseline_branch"] = sys.argv[3]
entry["dev_branch"] = sys.argv[3]
path.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
PY
pool_main="$shared_repository_pool/tapdata/tapdata"
mkdir -p "$(dirname "$pool_main")"
git clone -q "$source_repo" "$pool_main"
python3 "$install_root/workflow/task.py" advance --issue-key TAP-123 \
  --note '安装验收：已完成接管并进入准入阶段' --dir "$workspace" >/dev/null
python3 "$install_root/workflow/task.py" repository add --issue-key TAP-123 \
  --repo tapdata/tapdata --work-branch feature/TAP-123-source-pool \
  --base-branch "$install_branch" --scope 'Source Pool 启动接线' \
  --verification 'bash tests/test_install.sh' --dir "$workspace" >/dev/null
python3 "$install_root/workflow/task.py" repository prepare --issue-key TAP-123 \
  --dir "$workspace" >/dev/null
task_root="$(python3 "$install_root/workflow/repository_worktree.py" roots \
  --issue-key TAP-123 --dir "$workspace")"
task_execution_root="$(python3 "$install_root/workflow/repository_worktree.py" execution-root \
  --issue-key TAP-123 --dir "$workspace")"
resolved_workspace="$(python3 -c 'from pathlib import Path; import sys; print(Path(sys.argv[1]).resolve())' "$workspace")"
case "$task_root" in
  "$resolved_workspace"/.agenticops/worktrees/TAP-123/*/tapdata/tapdata) ;;
  *) printf '任务 worktree 路径不符合工作空间布局：%s\n' "$task_root" >&2; exit 1 ;;
esac
PATH="$fake_bin:$PATH" \
AGENTIC_OPS_EXPECTED_WORKSPACE="$task_execution_root" \
AGENTIC_OPS_CAPTURE="$capture" \
  "$workspace/agenticops" start codex TAP-123 -- --model task-bound >/dev/null
grep -Fx -- "--add-dir $task_root --model task-bound" "$capture" >/dev/null
test ! -e "$task_execution_root/agenticops"
# 已准备部分仓库后仍可登记后续仓库；此时 workspace purge 只应校验并回收
# 已准备的 worktree，不能把执行上下文“全仓已准备”的条件误用于清理预检。
python3 "$install_root/workflow/task.py" repository add --issue-key TAP-123 \
  --repo tapdata/tapdata-enterprise --work-branch feature/TAP-123-enterprise \
  --base-branch "$install_branch" --scope '企业模块后续改动' \
  --verification 'bash tests/test_install.sh' --dir "$workspace" >/dev/null
"$install_root/agenticops" workspace purge --workspace "$workspace" --yes >/dev/null
test ! -e "$task_root"
test ! -e "$workspace/.agenticops"

# auto-clone 必须通过受控 repository prepare 供给 Source Pool 并固化本地 Git 基线。
auto_clone_pool="$test_root/auto-clone-pool"
auto_clone_workspace="$test_root/auto-clone-workspace"
python3 "$install_root/bootstrap/repository_pool.py" --product-root "$install_root" \
  configure --root "$auto_clone_pool" --provisioning auto-clone >/dev/null
"$install_root/agenticops" init --workspace "$auto_clone_workspace" --agent codex >/dev/null
python3 "$install_root/workflow/task.py" init \
  --issue-key TAP-124 --task-class technical_task --dir "$auto_clone_workspace" >/dev/null
python3 "$install_root/workflow/task.py" advance --issue-key TAP-124 \
  --note '安装验收：进入准入阶段后自动准备仓库' --dir "$auto_clone_workspace" >/dev/null
python3 "$install_root/workflow/task.py" repository add --issue-key TAP-124 \
  --repo tapdata/tapdata --work-branch feature/TAP-124-auto-clone \
  --base-branch "$install_branch" --scope 'auto-clone 受控准备' \
  --verification 'bash tests/test_install.sh' --dir "$auto_clone_workspace" >/dev/null
python3 "$install_root/workflow/task.py" repository prepare --issue-key TAP-124 \
  --dir "$auto_clone_workspace" >/dev/null
test -d "$auto_clone_pool/tapdata/tapdata/.git"
python3 - "$auto_clone_workspace/.agenticops/tasks/TAP-124/state.json" "$source_repo" <<'PY'
import json
import sys
from pathlib import Path

task = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
repository = task["repositories"][0]
assert repository["base_sha"]
assert repository["authorized_endpoint"] == str(Path(sys.argv[2]).resolve())
assert repository["worktree"]["status"] == "prepared"
assert Path(repository["worktree"]["path"]).is_dir()
PY
"$install_root/agenticops" workspace purge --workspace "$auto_clone_workspace" --yes >/dev/null
test ! -e "$auto_clone_workspace/.agenticops"

printf 'AgenticOps 安装边界验证通过：被测分支=%s，被测提交=%s，安装 fixture 分支=%s\n' \
  "${tested_branch:-detached HEAD}" "$tested_ref" "$install_branch"
