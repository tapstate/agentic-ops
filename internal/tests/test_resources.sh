#!/usr/bin/env bash
set -euo pipefail

repo_root="$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd -P)"
cd "$repo_root"

fail() { printf '资源合同验证失败：%s\n' "$1" >&2; exit 1; }
require_file() { test -f "$1" || fail "缺少文件 $1"; }
require_executable() { test -x "$1" || fail "缺少可执行入口 $1"; }

# 根目录只保留现役产品层、源码维护设施和明确的本地状态入口。新增顶层内容必须先
# 证明无法归入既有架构层，避免临时脚手架和第二套 Runtime 再次进入产品仓库。
for path in .* *; do
  test -e "$path" || continue
  case "$path" in
    .|..|.git|.agentic-ops-source|.githooks|.gitignore|.local|.python-version|\
    AGENTS.md|README.md|agenticops|adapters|bootstrap|contracts|docs|gate|internal|\
    policies|projects|tests|workflow)
      ;;
    *)
      fail "根目录存在未归属现役架构的内容：$path"
      ;;
  esac
done

for file in \
  .agentic-ops-source AGENTS.md README.md agenticops \
  docs/strategy/project-goals.md docs/architecture/agenticops-v1-architecture.md \
  contracts/gate-request.schema.json contracts/gate-decision.schema.json \
  contracts/adapter-manifest.schema.json contracts/operation-catalog.schema.json \
  contracts/workspace-binding.schema.json contracts/task-registry.schema.json contracts/task-state.schema.json contracts/operation-catalog.json \
  gate/engine.py gate/runner.py \
  policies/operations.json policies/continuity.json \
  workflow/task.py workflow/authorization.py workflow/ci.py workflow/evidence.py \
  projects/tapdata/profile.json projects/tapdata/admission.json \
  projects/tapdata/skills/tapdata-task/SKILL.md \
  adapters/workspace/AGENTS.md adapters/workspace/CLAUDE.md \
  adapters/runtime.py adapters/tools/classifier.py adapters/tools/mcp-operations.json \
  adapters/agents/claude/hook.py adapters/agents/claude/manifest.json \
  adapters/agents/codex/hook.py adapters/agents/codex/manifest.json \
  bootstrap/install.sh bootstrap/update.sh bootstrap/rollback.sh \
  bootstrap/workspace-init.sh bootstrap/render.py \
  tests/test_gate.py tests/test_contracts.py tests/test_adapter_boundary.py tests/test_workflow.py tests/test_install.sh \
  internal/bin/story-gate internal/story_gate/stories.yaml \
  internal/story_gate/review-policy.yaml internal/release/release.sh; do
  require_file "$file"
done

for file in \
    agenticops gate/runner.py adapters/agents/claude/hook.py adapters/agents/codex/hook.py \
  workflow/task.py workflow/authorization.py workflow/ci.py workflow/evidence.py \
  bootstrap/install.sh bootstrap/update.sh bootstrap/rollback.sh bootstrap/workspace-init.sh bootstrap/render.py \
  tests/test_install.sh internal/bin/story-gate internal/release/release.sh \
  internal/release/hotfix.sh internal/tests/test_runtime.sh \
  internal/tests/test_resources.sh internal/tests/test_release.sh \
  .githooks/pre-commit .githooks/pre-push .githooks/reference-transaction; do
  require_executable "$file"
done

test "$(sed -n '1p' .agentic-ops-source)" = "source" ||
  fail ".agentic-ops-source 必须固定为 source"

python3 -m json.tool policies/operations.json >/dev/null
python3 -m json.tool policies/continuity.json >/dev/null
python3 -m json.tool contracts/gate-request.schema.json >/dev/null
python3 -m json.tool contracts/gate-decision.schema.json >/dev/null
python3 -m json.tool contracts/adapter-manifest.schema.json >/dev/null
python3 -m json.tool contracts/operation-catalog.schema.json >/dev/null
python3 -m json.tool contracts/workspace-binding.schema.json >/dev/null
python3 -m json.tool contracts/task-registry.schema.json >/dev/null
python3 -m json.tool contracts/task-state.schema.json >/dev/null
python3 -m json.tool contracts/operation-catalog.json >/dev/null
python3 -m json.tool projects/tapdata/profile.json >/dev/null
python3 -m json.tool projects/tapdata/admission.json >/dev/null
python3 -m json.tool adapters/tools/mcp-operations.json >/dev/null
python3 -m json.tool adapters/tools/mcp.template.json >/dev/null
python3 -m json.tool adapters/agents/claude/manifest.json >/dev/null
python3 -m json.tool adapters/agents/codex/manifest.json >/dev/null

grep -Fq 'sparse-checkout set adapters bootstrap contracts gate policies projects workflow' bootstrap/install.sh ||
  fail "安装脚本没有限制为产品目录"
grep -Fq '__AGENTIC_OPS_HOME__' adapters/workspace/AGENTS.md ||
  fail "工作目录入口缺少安装路径占位符"
grep -Fq 'deny_with_guidance' adapters/agents/codex/manifest.json ||
  fail "Codex Adapter 未声明二态降级"
grep -Fq 'repositories' policies/operations.json || fail "任务授权未绑定多仓库集合"
grep -Fq '@AGENTS.md' adapters/workspace/CLAUDE.md || fail "Claude 入口未复用公共 Agent 规则"
grep -Fq '"project_skill_target": null' adapters/agents/claude/manifest.json ||
  fail "Claude Adapter 仍会复制中央 Project Skill"

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
grep -Fxq '.superpowers/' .gitignore || fail ".superpowers 未忽略"

if rg -n 'ao-work|ao_maint|workplane:[[:space:]]*(maintainer|developer)' \
  contracts gate workflow policies projects adapters bootstrap tests >/dev/null; then
  fail "产品目录仍引用旧 Runtime 或工作面概念"
fi

PYTHONDONTWRITEBYTECODE=1 python3 tests/test_adapter_boundary.py >/dev/null ||
  fail "适配层重量门禁未通过"

printf 'AgenticOps v1 资源合同验证通过\n'
