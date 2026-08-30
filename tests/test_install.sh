#!/usr/bin/env bash
set -euo pipefail

repo_root="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd -P)"
test_root="$(mktemp -d)"
trap 'chmod -R u+w "$test_root" 2>/dev/null || true; rm -rf "$test_root"' EXIT

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
source_workspace="$test_root/source-workspace"
"$maintainer_root/agenticops" init --workspace "$source_workspace" \
  --project tapdata --agent test-agent >/dev/null
"$maintainer_root/agenticops" doctor --workspace "$source_workspace" >/dev/null
"$source_workspace/.agenticops/agenticops" doctor >/dev/null
printf '{"enabled":"changed"}\n' \
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
git -C "$source_repo" add SOURCE-NEXT
git -C "$source_repo" commit -qm "source next"
source_update_output="$test_root/source-update-output"
PATH="$setup_bin:$PATH" "$source_workspace/.agenticops/agenticops" update > "$source_update_output"
test -f "$maintainer_root/SOURCE-NEXT"
grep -F '工作面=维护' "$source_update_output" >/dev/null
test "$(python3 "$maintainer_root/bootstrap/product_state.py" --product-root "$maintainer_root" read --field current_ref)" = \
  "$(git -C "$maintainer_root" rev-parse HEAD)"

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
test -x "$install_root/agenticops"
test ! -e "$install_root/internal"
test -f "$install_root/.local/product.json"
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

"$install_root/agenticops" init --workspace "$workspace"

test -f "$workspace/.agenticops/workspace.json"
test -f "$workspace/.agenticops/init.json"
test -x "$workspace/.agenticops/agenticops"
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
assert {"AGENTS.md", ".agenticops/agenticops", "CLAUDE.md", ".claude/settings.json", ".codex/agenticops-hooks.example.json", ".test-agent/settings.json"} <= paths
PY
"$install_root/agenticops" doctor --workspace "$workspace" >/dev/null
"$workspace/.agenticops/agenticops" doctor >/dev/null

printf 'drift\n' > "$workspace/AGENTS.md"
if "$install_root/agenticops" doctor --workspace "$workspace" >/dev/null 2>&1; then
  printf '工作目录漂移未被 doctor 发现\n' >&2
  exit 1
fi
"$install_root/agenticops" repair --workspace "$workspace" >/dev/null
"$install_root/agenticops" doctor --workspace "$workspace" >/dev/null

printf 'drift\n' > "$workspace/.agenticops/agenticops"
if "$install_root/agenticops" doctor --workspace "$workspace" >/dev/null 2>&1; then
  printf '工作空间入口漂移未被 doctor 发现\n' >&2
  exit 1
fi
"$install_root/agenticops" repair --workspace "$workspace" >/dev/null
test -x "$workspace/.agenticops/agenticops"
"$workspace/.agenticops/agenticops" doctor >/dev/null

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
PATH="$fake_bin:$PATH" \
AGENTIC_OPS_EXPECTED_WORKSPACE="$expected_workspace" \
AGENTIC_OPS_CAPTURE="$capture" \
  "$workspace/.agenticops/agenticops" start --agent codex -- --model workspace-entry >/dev/null
grep -Fx -- '--model workspace-entry' "$capture" >/dev/null
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
"$workspace/.agenticops/agenticops" update > "$installed_update_output"
test -f "$install_root/NEXT"
grep -F '工作面=使用' "$installed_update_output" >/dev/null
if "$install_root/agenticops" doctor --workspace "$workspace" >/dev/null 2>&1; then
  printf '产品更新后旧工作目录绑定未被识别为待刷新\n' >&2
  exit 1
fi
"$workspace/.agenticops/agenticops" repair >/dev/null
"$install_root/agenticops" doctor --workspace "$workspace" >/dev/null
test -f "$workspace/.agenticops/tasks/TAP-123/state.json"
test -f "$workspace/.agenticops/tasks/TAP-999/state.json"
test "$(file_digest "$workspace/.agenticops/tasks/index.json")" = "$task_index_digest"
test "$(file_digest "$workspace/.agenticops/tasks/TAP-123/state.json")" = "$task_123_digest"
test "$(file_digest "$workspace/.agenticops/tasks/TAP-999/state.json")" = "$task_999_digest"
"$workspace/.agenticops/agenticops" rollback >/dev/null
test ! -f "$install_root/NEXT"
"$workspace/.agenticops/agenticops" repair >/dev/null
"$install_root/agenticops" doctor --workspace "$workspace" >/dev/null
test "$(file_digest "$workspace/.agenticops/tasks/index.json")" = "$task_index_digest"
test "$(file_digest "$workspace/.agenticops/tasks/TAP-123/state.json")" = "$task_123_digest"
test "$(file_digest "$workspace/.agenticops/tasks/TAP-999/state.json")" = "$task_999_digest"

printf 'AgenticOps 安装边界验证通过：被测分支=%s，被测提交=%s，安装 fixture 分支=%s\n' \
  "${tested_branch:-detached HEAD}" "$tested_ref" "$install_branch"
