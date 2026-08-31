#!/usr/bin/env bash
set -euo pipefail

repo_root="$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd -P)"
cd "$repo_root"

fail() { printf '资源合同验证失败：%s\n' "$1" >&2; exit 1; }
require_file() { test -f "$1" || fail "缺少文件 $1"; }
require_executable() { test -x "$1" || fail "缺少可执行入口 $1"; }
resource_contract="$repo_root/internal/resource-contract.json"
resource_contract_tool="$repo_root/internal/resource_contract.py"

require_file "$resource_contract"
require_file "$resource_contract_tool"
python3 "$resource_contract_tool" --contract "$resource_contract" \
  validate --gitignore "$repo_root/.gitignore" >/dev/null ||
  fail ".gitignore 与统一资源合同不匹配"
PYTHONDONTWRITEBYTECODE=1 python3 "$repo_root/internal/tests/test_resource_contract.py" >/dev/null ||
  fail "统一资源合同匹配回归未通过"
allowed_root_entries="$(python3 "$resource_contract_tool" --contract "$resource_contract" allowed-root)"
tool_root_entries="$(python3 "$resource_contract_tool" --contract "$resource_contract" tool-root)"

# 根目录只保留现役产品层、源码维护设施和明确的本地状态入口。新增顶层内容必须先
# 证明无法归入既有架构层，避免临时脚手架和第二套 Runtime 再次进入产品仓库。
for path in .* *; do
  test -e "$path" || continue
  case "$path" in .|..|.git) continue ;; esac
  printf '%s\n' "$allowed_root_entries" | grep -Fxq -- "$path" ||
    fail "根目录存在未归属现役架构的内容：$path"
done

while IFS= read -r tool_root; do
  test -n "$tool_root" || continue
  tracked_tool_files="$(git ls-files -- "$tool_root")"
  test -z "$tracked_tool_files" ||
    fail "本机工具目录不能被 Git 管理：$tracked_tool_files"
done <<EOF
$tool_root_entries
EOF

for file in \
  .agentic-ops-source AGENTS.md README.md agenticops \
  docs/strategy/project-goals.md docs/architecture/agenticops-v1-architecture.md \
  contracts/gate-request.schema.json contracts/gate-decision.schema.json \
  contracts/adapter-manifest.schema.json contracts/operation-catalog.schema.json \
  contracts/product-state.schema.json contracts/workspace.schema.json \
  contracts/repository-pool.schema.json contracts/repository-catalog.schema.json \
  contracts/workspace-init.schema.json contracts/task-registry.schema.json \
  contracts/task-state.schema.json contracts/operation-catalog.json \
  gate/engine.py gate/runner.py \
  policies/operations.json policies/continuity.json \
  workflow/task.py workflow/task_store.py workflow/project_rules.py \
  workflow/authorization.py workflow/ci.py workflow/evidence.py \
  projects/tapdata/profile.json projects/tapdata/repositories.json projects/tapdata/admission.json \
  projects/tapdata/skills/tapdata-task/SKILL.md \
  adapters/workspace/AGENTS.md adapters/workspace/agenticops adapters/agents/claude/templates/CLAUDE.md \
  adapters/runtime.py adapters/tools/classifier.py adapters/tools/mcp-operations.json \
  adapters/agents/claude/hook.py adapters/agents/claude/manifest.json \
  adapters/agents/codex/hook.py adapters/agents/codex/manifest.json \
  bootstrap/install.sh bootstrap/setup.sh bootstrap/update.sh bootstrap/rollback.sh bootstrap/lifecycle-common.sh \
  bootstrap/workspace-init.sh bootstrap/render.py bootstrap/agent_registry.py \
  bootstrap/product_state.py bootstrap/repository_pool.py bootstrap/workspace_registry.py \
  workflow/repository_worktree.py \
  tests/test_gate.py tests/test_contracts.py tests/test_adapter_boundary.py tests/test_workflow.py tests/test_install.sh \
  internal/acceptance.sh internal/bin/story-gate internal/story_gate/stories.yaml \
  internal/story_gate/review-policy.yaml internal/release/release.sh \
  internal/resource-contract.json internal/resource_contract.py \
  internal/tests/test_resource_contract.py; do
  require_file "$file"
done

for file in \
    agenticops gate/runner.py adapters/agents/claude/hook.py adapters/agents/codex/hook.py \
  workflow/task.py workflow/authorization.py workflow/ci.py workflow/evidence.py \
  bootstrap/install.sh bootstrap/setup.sh bootstrap/update.sh bootstrap/rollback.sh bootstrap/lifecycle-common.sh \
  bootstrap/workspace-init.sh bootstrap/render.py bootstrap/agent_registry.py \
  bootstrap/product_state.py bootstrap/repository_pool.py bootstrap/workspace_registry.py \
  workflow/repository_worktree.py \
  tests/test_install.sh internal/acceptance.sh internal/bin/story-gate internal/release/release.sh \
  internal/release/hotfix.sh internal/tests/test_runtime.sh \
  internal/tests/test_resources.sh internal/tests/test_release.sh \
  .githooks/pre-commit .githooks/pre-push .githooks/reference-transaction; do
  require_executable "$file"
done

test "$(sed -n '1p' .agentic-ops-source)" = "source" ||
  fail ".agentic-ops-source 必须固定为 source"

grep -F '工作面=维护' bootstrap/setup.sh >/dev/null ||
  fail "setup 必须明确初始化维护工作面"
grep -F 'face="$(lifecycle_work_face "$mode")"' bootstrap/update.sh >/dev/null ||
  fail "update 必须根据产品根目录 mode 区分工作面"

python3 -m json.tool policies/operations.json >/dev/null
python3 -m json.tool policies/continuity.json >/dev/null
python3 -m json.tool contracts/gate-request.schema.json >/dev/null
python3 -m json.tool contracts/gate-decision.schema.json >/dev/null
python3 -m json.tool contracts/adapter-manifest.schema.json >/dev/null
python3 -m json.tool contracts/operation-catalog.schema.json >/dev/null
python3 -m json.tool contracts/product-state.schema.json >/dev/null
python3 -m json.tool contracts/repository-pool.schema.json >/dev/null
python3 -m json.tool contracts/repository-catalog.schema.json >/dev/null
python3 -m json.tool contracts/workspace.schema.json >/dev/null
python3 -m json.tool contracts/workspace-init.schema.json >/dev/null
python3 -m json.tool contracts/task-registry.schema.json >/dev/null
python3 -m json.tool contracts/task-state.schema.json >/dev/null
python3 -m json.tool contracts/operation-catalog.json >/dev/null
python3 -m json.tool projects/tapdata/profile.json >/dev/null
python3 -m json.tool projects/tapdata/repositories.json >/dev/null
python3 -m json.tool projects/tapdata/admission.json >/dev/null
python3 -m json.tool adapters/tools/mcp-operations.json >/dev/null
python3 -m json.tool adapters/tools/mcp.template.json >/dev/null
for manifest in adapters/agents/*/manifest.json; do
  python3 -m json.tool "$manifest" >/dev/null
done

grep -Fq 'sparse-checkout set adapters bootstrap contracts gate policies projects workflow' bootstrap/install.sh ||
  fail "安装脚本没有限制为产品目录"
grep -Fq '__AGENTIC_OPS_HOME__' adapters/workspace/AGENTS.md ||
  fail "工作目录入口缺少安装路径占位符"
grep -Fq 'deny_with_guidance' adapters/agents/codex/manifest.json ||
  fail "Codex Adapter 未声明二态降级"
grep -Fq 'repositories' policies/operations.json || fail "任务授权未绑定多仓库集合"
grep -Fq '@AGENTS.md' adapters/agents/claude/templates/CLAUDE.md ||
  fail "Claude 入口未复用公共 Agent 规则"
grep -Fq '"project_skill_target": ".claude/skills"' adapters/agents/claude/manifest.json ||
  fail "Claude Adapter 未声明原生 Project Skill 发现目录"
grep -Fq '"project_skill_target": ".agents/skills"' adapters/agents/codex/manifest.json ||
  fail "Codex Adapter 未声明原生 Project Skill 发现目录"
grep -Fq 'symlink_to' bootstrap/render.py ||
  fail "Bootstrap 未以受控符号链接接线中央 Project Skill"

test ! -e gate/hook.py || fail "Gate 仍包含平台 Hook 入口"
test ! -e adapters/claude || fail "仍包含旧 Claude Adapter 路径"
test ! -e adapters/codex || fail "仍包含旧 Codex Adapter 路径"
test ! -e adapters/mcp.json || fail "仍包含旧 MCP 产物源"

test ! -f developer/AGENTS.md || fail "现役结构仍包含 developer 工作面入口"
test ! -f maintainer/AGENTS.md || fail "现役结构仍包含 maintainer 工作面入口"
test ! -e packages/agentic-cli || fail "旧 agentic-cli 仍在现役结构"
test ! -e go.mod || fail "旧 Go Runtime 仍在现役结构"
test ! -d install-resources || fail "旧安装制品目录仍在现役结构"
test ! -d docs/superpowers || fail "不得提交 docs/superpowers"
tracked_local_files="$(
  git ls-files .local | while IFS= read -r tracked_local_file; do
    test ! -e "$tracked_local_file" || printf '%s\n' "$tracked_local_file"
  done
)"
test -z "$tracked_local_files" || fail ".local 中存在受 Git 管理的运行态文件：$tracked_local_files"
test ! -e internal/.local || fail "internal/.local 未迁移到产品根目录 .local"
test ! -e internal/.venv || fail "internal/.venv 未迁移到产品根目录 .local"

if rg -n 'ao-work|ao_maint|workplane:[[:space:]]*(maintainer|developer)' \
  contracts gate workflow policies projects adapters bootstrap tests >/dev/null; then
  fail "产品目录仍引用旧 Runtime 或工作面概念"
fi

if rg -n -- '--agent[[:space:]]+(both|claude\|codex)|choices=.*both' \
  agenticops bootstrap contracts docs README.md >/dev/null; then
  fail "公共安装或使用入口仍维护固定 Agent 枚举"
fi

PYTHONDONTWRITEBYTECODE=1 python3 bootstrap/agent_registry.py \
  --product-root "$repo_root" list >/dev/null || fail "Agent Manifest 发现失败"

internal/acceptance.sh --list | grep -Fxq 'full: runtime resources install release' ||
  fail "自动验收脚本缺少固定 full 配置"

PYTHONDONTWRITEBYTECODE=1 python3 tests/test_adapter_boundary.py >/dev/null ||
  fail "适配层重量门禁未通过"

printf 'AgenticOps v1 资源合同验证通过\n'
