#!/usr/bin/env bash
set -euo pipefail

repo_root="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd -P)"
test_root="$(mktemp -d)"
trap 'chmod -R u+w "$test_root" 2>/dev/null || true; rm -rf "$test_root"' EXIT

source_repo="$test_root/source"
install_root="$test_root/install"
workspace="$test_root/project-workspace"

mkdir -p "$source_repo"
git -C "$source_repo" init -q -b main
git -C "$source_repo" config user.email agentic-ops-test@example.test
git -C "$source_repo" config user.name "AgenticOps Test"
for product_dir in adapters bootstrap contracts gate policies projects workflow; do
  cp -R "$repo_root/$product_dir" "$source_repo/$product_dir"
done
cp "$repo_root/agenticops" "$source_repo/agenticops"
chmod +x "$source_repo/agenticops"
git -C "$source_repo" add agenticops adapters bootstrap contracts gate policies projects workflow
git -C "$source_repo" commit -qm "initial"

bash "$repo_root/bootstrap/install.sh" \
  --install-home "$install_root" --repository "$source_repo" --branch main

test -f "$install_root/contracts/gate-request.schema.json"
test -f "$install_root/gate/runner.py"
test -x "$install_root/agenticops"
test ! -e "$install_root/internal"
test -f "$install_root/user/current-ref"
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
test ! -e "$collision_workspace/.gate"

"$install_root/agenticops" init --workspace "$workspace" --agent both

test -f "$workspace/.agenticops.json"
test -f "$workspace/AGENTS.md"
test -f "$workspace/CLAUDE.md"
test -f "$workspace/.mcp.json"
test -f "$workspace/.claude/settings.json"
test -f "$workspace/.codex/agenticops-hooks.example.json"
test ! -e "$workspace/.claude/skills"
grep -F '@AGENTS.md' "$workspace/CLAUDE.md" >/dev/null
grep -F 'Product Project：`tapdata`' "$workspace/AGENTS.md" >/dev/null
grep -F "$install_root/workflow/task.py" "$workspace/AGENTS.md" >/dev/null
grep -F "$install_root/adapters/agents/claude/hook.py" "$workspace/.claude/settings.json" >/dev/null
grep -F "$install_root/adapters/agents/codex/hook.py" "$workspace/.codex/agenticops-hooks.example.json" >/dev/null
python3 - "$workspace/.agenticops.json" "$install_root" <<'PY'
import json
import sys
from pathlib import Path

binding = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
assert binding["schema_version"] == 1
assert binding["product_root"] == str(Path(sys.argv[2]).resolve())
assert binding["project"] == "tapdata"
assert binding["agents"] == ["claude", "codex"]
assert ".claude/skills/tapdata-task/SKILL.md" not in binding["generated_artifacts"]
PY
"$install_root/agenticops" doctor --workspace "$workspace" >/dev/null

printf 'drift\n' > "$workspace/AGENTS.md"
mkdir -p "$workspace/.claude/skills/tapdata-task"
printf '%s\n' '---' 'metadata:' '  product: agenticops' '---' \
  > "$workspace/.claude/skills/tapdata-task/SKILL.md"
if "$install_root/agenticops" doctor --workspace "$workspace" >/dev/null 2>&1; then
  printf '工作目录漂移未被 doctor 发现\n' >&2
  exit 1
fi
"$install_root/agenticops" repair --workspace "$workspace" >/dev/null
"$install_root/agenticops" doctor --workspace "$workspace" >/dev/null
test ! -e "$workspace/.claude/skills/tapdata-task/SKILL.md"

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
  "$install_root/agenticops" codex --workspace "$workspace" -- --model fake >/dev/null
grep -Fx -- '--model fake' "$capture" >/dev/null
test ! -e "$workspace/AGENTS.md.tmp"

python3 "$install_root/workflow/task.py" init \
  --issue-key TAP-123 --task-class defect_fix --dir "$workspace" >/dev/null
python3 "$install_root/workflow/task.py" init \
  --issue-key TAP-999 --task-class technical_task --dir "$workspace" >/dev/null
test -f "$workspace/.gate/tasks.json"
test -f "$workspace/.gate/tasks/TAP-123/task.json"
test -f "$workspace/.gate/tasks/TAP-999/task.json"
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
test -f "$workspace/.gate/tasks/TAP-123/task.json"
test -f "$workspace/.gate/tasks/TAP-999/task.json"
AGENTIC_OPS_HOME="$install_root" bash "$install_root/bootstrap/rollback.sh" >/dev/null
test ! -f "$install_root/NEXT"
"$install_root/agenticops" repair --workspace "$workspace" >/dev/null
"$install_root/agenticops" doctor --workspace "$workspace" >/dev/null

printf 'AgenticOps 安装边界验证通过\n'
