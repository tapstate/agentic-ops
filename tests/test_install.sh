#!/usr/bin/env bash
set -euo pipefail

repo_root="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd -P)"
test_root="$(mktemp -d)"
trap 'chmod -R u+w "$test_root" 2>/dev/null || true; rm -rf "$test_root"' EXIT

source_repo="$test_root/source"
install_root="$test_root/install"
maintainer_root="$test_root/maintainer"
workspace="$test_root/project-workspace"

mkdir -p "$source_repo"
git -C "$source_repo" init -q -b main
git -C "$source_repo" config user.email agentic-ops-test@example.test
git -C "$source_repo" config user.name "AgenticOps Test"
for product_dir in adapters bootstrap contracts gate policies projects workflow; do
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
  "schema_version": 1,
  "name": "test-agent",
  "adapter_version": 1,
  "entrypoint": "adapters/agents/test-agent/hook.py",
  "capabilities": {"decisions": ["allow", "deny"], "ask_fallback": "deny_with_guidance"},
  "artifacts": [{"template": "adapters/agents/test-agent/templates/settings.json", "target": ".test-agent/settings.json"}],
  "launch": {"mode": "command", "command": "test-agent-cli", "message": "测试 Agent 已接线。"},
  "project_skill_target": null
}
JSON
printf '{"enabled":true}\n' > "$source_repo/adapters/agents/test-agent/templates/settings.json"
cp "$repo_root/agenticops" "$source_repo/agenticops"
chmod +x "$source_repo/agenticops"
git -C "$source_repo" add .agentic-ops-source .gitignore .githooks agenticops adapters bootstrap \
  contracts gate policies projects workflow internal
git -C "$source_repo" commit -qm "initial"
git -C "$source_repo" branch develop

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
test "$(git -C "$maintainer_root" branch --show-current)" = develop
test -d "$maintainer_root/.local/venv/internal"
test "$(python3 "$maintainer_root/bootstrap/product_state.py" --product-root "$maintainer_root" read --field mode)" = source
test "$(python3 "$maintainer_root/bootstrap/product_state.py" --product-root "$maintainer_root" read --field tracking_branch)" = develop
test -x "$(git -C "$maintainer_root" config --get core.hooksPath)/pre-commit"
source_workspace="$test_root/source-workspace"
"$maintainer_root/agenticops" init --workspace "$source_workspace" \
  --project tapdata --agent test-agent >/dev/null
"$maintainer_root/agenticops" doctor --workspace "$source_workspace" >/dev/null
printf '{"enabled":"changed"}\n' \
  > "$maintainer_root/adapters/agents/test-agent/templates/settings.json"
if "$maintainer_root/agenticops" doctor --workspace "$source_workspace" >/dev/null 2>&1; then
  printf '源码变更后工作空间漂移未被识别\n' >&2
  exit 1
fi
"$maintainer_root/agenticops" repair --workspace "$source_workspace" >/dev/null
grep -F '"changed"' "$source_workspace/.test-agent/settings.json" >/dev/null

bash "$repo_root/bootstrap/install.sh" \
  --install-home "$install_root" --repository "$source_repo" --branch main

test -f "$install_root/contracts/gate-request.schema.json"
test -f "$install_root/gate/runner.py"
test -x "$install_root/agenticops"
test ! -e "$install_root/internal"
test -f "$install_root/.local/product.json"
test "$(python3 "$install_root/bootstrap/product_state.py" --product-root "$install_root" read --field tracking_branch)" = main
if PATH="$setup_bin:$PATH" "$install_root/agenticops" setup >/dev/null 2>&1; then
  printf '安装 Product Root 被错误切换为源码维护模式\n' >&2
  exit 1
fi
if "$install_root/agenticops" init --workspace "$install_root" >/dev/null 2>&1; then
  printf 'Product Root 被错误初始化为项目工作空间\n' >&2
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

"$install_root/agenticops" init --workspace "$workspace"

test -f "$workspace/.agenticops/workspace.json"
test -f "$workspace/.agenticops/init.json"
test -f "$workspace/AGENTS.md"
test -f "$workspace/CLAUDE.md"
test -f "$workspace/.mcp.json"
test -f "$workspace/.claude/settings.json"
test -f "$workspace/.codex/agenticops-hooks.example.json"
test -f "$workspace/.test-agent/settings.json"
test ! -e "$workspace/.claude/skills"
grep -F '@AGENTS.md' "$workspace/CLAUDE.md" >/dev/null
grep -F 'Product Project：`tapdata`' "$workspace/AGENTS.md" >/dev/null
grep -F "$install_root/workflow/task.py" "$workspace/AGENTS.md" >/dev/null
grep -F "$install_root/adapters/agents/claude/hook.py" "$workspace/.claude/settings.json" >/dev/null
grep -F "$install_root/adapters/agents/codex/hook.py" "$workspace/.codex/agenticops-hooks.example.json" >/dev/null
python3 - "$workspace/.agenticops/workspace.json" "$workspace/.agenticops/init.json" "$install_root" <<'PY'
import json
import sys
from pathlib import Path

binding = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
initialization = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
assert binding["schema_version"] == 1
assert binding["product_root"] == str(Path(sys.argv[3]).resolve())
assert binding["project"] == "tapdata"
assert binding["agents"] == ["claude", "codex", "test-agent"]
paths = {item["path"] for item in initialization["artifacts"]}
assert {"AGENTS.md", "CLAUDE.md", ".claude/settings.json", ".codex/agenticops-hooks.example.json", ".test-agent/settings.json"} <= paths
PY
"$install_root/agenticops" doctor --workspace "$workspace" >/dev/null

printf 'drift\n' > "$workspace/AGENTS.md"
if "$install_root/agenticops" doctor --workspace "$workspace" >/dev/null 2>&1; then
  printf '工作目录漂移未被 doctor 发现\n' >&2
  exit 1
fi
"$install_root/agenticops" repair --workspace "$workspace" >/dev/null
"$install_root/agenticops" doctor --workspace "$workspace" >/dev/null

subset_workspace="$test_root/subset-workspace"
"$install_root/agenticops" init --workspace "$subset_workspace" --agent codex >/dev/null
test -f "$subset_workspace/.codex/agenticops-hooks.example.json"
test ! -e "$subset_workspace/CLAUDE.md"
test ! -e "$subset_workspace/.claude/settings.json"
if "$install_root/agenticops" init --workspace "$test_root/unknown-workspace" --agent missing-agent >/dev/null 2>&1; then
  printf '未知 Agent 被错误接受\n' >&2
  exit 1
fi
if "$install_root/agenticops" start --agent test-agent --workspace "$subset_workspace" >/dev/null 2>&1; then
  printf '工作空间启动了未绑定的 Agent\n' >&2
  exit 1
fi

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

printf 'next\n' > "$source_repo/NEXT"
git -C "$source_repo" add NEXT
git -C "$source_repo" commit -qm "next"
AGENTIC_OPS_HOME="$install_root" bash "$install_root/bootstrap/update.sh" >/dev/null
test -f "$install_root/NEXT"
if "$install_root/agenticops" doctor --workspace "$workspace" >/dev/null 2>&1; then
  printf '产品更新后旧工作目录绑定未被识别为待刷新\n' >&2
  exit 1
fi
"$install_root/agenticops" repair --workspace "$workspace" >/dev/null
"$install_root/agenticops" doctor --workspace "$workspace" >/dev/null
test -f "$workspace/.agenticops/tasks/TAP-123/state.json"
test -f "$workspace/.agenticops/tasks/TAP-999/state.json"
AGENTIC_OPS_HOME="$install_root" bash "$install_root/bootstrap/rollback.sh" >/dev/null
test ! -f "$install_root/NEXT"
"$install_root/agenticops" repair --workspace "$workspace" >/dev/null
"$install_root/agenticops" doctor --workspace "$workspace" >/dev/null

printf 'AgenticOps 安装边界验证通过\n'
