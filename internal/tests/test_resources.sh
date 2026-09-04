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
maintenance_skill_roots=""
if test -f .local/maintenance-skill-wiring.json && \
    python3 bootstrap/skill_wiring.py --product-root "$repo_root" --check >/dev/null 2>&1; then
  maintenance_skill_roots="$(python3 - "$repo_root" <<'PY'
import sys
from pathlib import Path

sys.path.insert(0, str(Path(sys.argv[1]) / "bootstrap"))
from agent_registry import discover

roots = {
    Path(target).parts[0]
    for manifest in discover(sys.argv[1]).values()
    for target in [manifest.get("skill_target")]
    if target
}
print("\n".join(sorted(roots)))
PY
)"
fi

# 根目录只保留现役产品层、源码维护设施和明确的本地状态入口。新增顶层内容必须先
# 证明无法归入既有架构层，避免临时脚手架和第二套 Runtime 再次进入产品仓库。
for path in .* *; do
  test -e "$path" || continue
  case "$path" in .|..|.git) continue ;; esac
  if printf '%s\n' "$allowed_root_entries" | grep -Fxq -- "$path"; then
    continue
  fi
  if printf '%s\n' "$maintenance_skill_roots" | grep -Fxq -- "$path"; then
    test -z "$(git ls-files -- "$path")" ||
      fail "维护 Skill 原生接线目录不得被 Git 管理：$path"
    continue
  fi
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
  docs/skill-maintenance.md \
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
  workflow/quality.py workflow/quality_contract.py workflow/quality_write.py \
  workflow/jira_status.py workflow/pr_ready.py \
  contracts/quality-action.schema.json contracts/quality-state.schema.json \
  projects/tapdata/quality.json docs/usage/quality-checkpoints.md tests/test_quality.py tests/test_jira_status.py \
  projects/tapdata/profile.json projects/tapdata/repositories.json projects/tapdata/admission.json \
  projects/tapdata/skills/tapdata-task/SKILL.md \
  skills/ao-test-takeover/SKILL.md skills/ao-ws-init/SKILL.md \
  adapters/workspace/AGENTS.md adapters/workspace/agenticops adapters/agents/claude/templates/CLAUDE.md \
  adapters/runtime.py adapters/tools/classifier.py adapters/tools/git_push_syntax.py \
  adapters/tools/shell_classifier.py \
  adapters/tools/shell_syntax.py \
  adapters/tools/mcp-operations.json adapters/tools/mcp-requirements.json adapters/tools/mcp.template.json \
  adapters/agents/claude/hook.py adapters/agents/claude/manifest.json \
  adapters/agents/codex/hook.py adapters/agents/codex/manifest.json \
  bootstrap/install.sh bootstrap/setup.sh bootstrap/update.sh bootstrap/rollback.sh bootstrap/lifecycle-common.sh \
  bootstrap/workspace-init.sh bootstrap/render.py bootstrap/workspace_paths.py bootstrap/agent_registry.py \
  bootstrap/skill_wiring.py \
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
  workflow/jira_status.py workflow/pr_ready.py \
  bootstrap/install.sh bootstrap/setup.sh bootstrap/update.sh bootstrap/rollback.sh bootstrap/lifecycle-common.sh \
  bootstrap/workspace-init.sh bootstrap/render.py bootstrap/agent_registry.py \
  bootstrap/skill_wiring.py \
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
python3 -m json.tool adapters/tools/mcp-requirements.json >/dev/null
python3 -m json.tool adapters/tools/mcp.template.json >/dev/null
for manifest in adapters/agents/*/manifest.json; do
  python3 -m json.tool "$manifest" >/dev/null
done
for template in adapters/agents/*/templates/*.json; do
  python3 -m json.tool "$template" >/dev/null
done

grep -Fq 'sparse-checkout set adapters bootstrap contracts gate policies projects workflow' bootstrap/install.sh ||
  fail "安装脚本没有限制为产品目录"
grep -Fq '__AGENTIC_OPS_HOME__' adapters/workspace/AGENTS.md ||
  fail "工作目录入口缺少安装路径占位符"
grep -Fq 'deny_with_guidance' adapters/agents/codex/manifest.json ||
  fail "Codex Adapter 未声明二态降级"
python3 - <<'PY' || fail "Project、MCP 与 Codex 资源版本不一致"
import ast
import json
from pathlib import Path

manifest = json.loads(Path("adapters/agents/codex/manifest.json").read_text(encoding="utf-8"))
tree = ast.parse(Path("adapters/agents/codex/hook.py").read_text(encoding="utf-8"))
versions = [
    node.value.value
    for node in tree.body
    if isinstance(node, ast.Assign)
    and any(isinstance(target, ast.Name) and target.id == "ADAPTER_VERSION" for target in node.targets)
    and isinstance(node.value, ast.Constant)
    and type(node.value.value) is int
]
assert versions == [manifest["adapter_version"]]

mappings = json.loads(Path("adapters/tools/mcp-operations.json").read_text(encoding="utf-8"))
requirements = json.loads(Path("adapters/tools/mcp-requirements.json").read_text(encoding="utf-8"))
template = json.loads(Path("adapters/tools/mcp.template.json").read_text(encoding="utf-8"))
assert "readonly_tools" not in mappings
assert "readonly_prefixes" not in mappings
assert set(mappings["mappings"]) == {"github", "atlassian"}
assert set(requirements["required_servers"]) == {"atlassian"}
assert set(requirements["required_servers"]) < set(mappings["mappings"])
assert set(template["mcpServers"]) == set(requirements["required_servers"])
for name, requirement in requirements["required_servers"].items():
    assert template["mcpServers"][name] == {"type": "http", "url": requirement["url"]}

workspace_entry = Path("adapters/workspace/AGENTS.md").read_text(encoding="utf-8")
assert 'mcp-requirements.json' in workspace_entry
assert '首次需要 Jira 事实时检查 `atlassian`' in workspace_entry
assert '不得伪造工具结果' in workspace_entry
assert 'GitHub MCP、`gh` 和其它 GitHub 工具不由 AgenticOps 绑定' in workspace_entry

profile = json.loads(Path("projects/tapdata/profile.json").read_text(encoding="utf-8"))
assert profile["statuses"]["Analyzed"] == "waiting_takeover"
assert profile["transitions"]["start_progress"] == {
    "name": "Start Investigation",
    "id": "421",
    "from": ["Analyzed"],
    "to": "In Progress",
}
assert profile["workflows_by_issue_type"] == [{
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
grep -Fq '接管、继续或 reset 成功只是流程恢复点' adapters/workspace/AGENTS.md ||
  fail "工作空间入口未声明接管后的连续推进"
grep -Fq '远程候选参考' adapters/workspace/AGENTS.md ||
  fail "工作空间入口未声明远程源码证据边界"
grep -Fq '登记完成后立即执行受控 `task.py repository prepare`' \
  projects/tapdata/skills/tapdata-task/SKILL.md ||
  fail "TapData Skill 未声明受控仓库准备"
grep -Fq 'repositories' policies/operations.json || fail "任务授权未绑定多仓库集合"
grep -Fq '@AGENTS.md' adapters/agents/claude/templates/CLAUDE.md ||
  fail "Claude 入口未复用公共 Agent 规则"
grep -Fq '"skill_target": ".claude/skills"' adapters/agents/claude/manifest.json ||
  fail "Claude Adapter 未声明原生 Skill 发现目录"
grep -Fq '"skill_target": ".agents/skills"' adapters/agents/codex/manifest.json ||
  fail "Codex Adapter 未声明原生 Skill 发现目录"
if rg -n '"project_skill_target"[[:space:]]*:' adapters tests >/dev/null; then
  fail "Agent Skill 接线仍区分项目专用 Manifest 字段"
fi
if find skills projects/*/skills -type f -path '*/agents/*' -print -quit | grep -q .; then
  fail "通用 Skill 源目录仍包含 Agent 专用 agents/ 配置"
fi
grep -Fq 'bootstrap/skill_wiring.py' bootstrap/setup.sh ||
  fail "setup 未刷新源码维护面 Skill 接线"
grep -Fq 'bootstrap/skill_wiring.py' bootstrap/update.sh ||
  fail "update 未刷新源码维护面 Skill 接线"
grep -Fq 'WorkspaceDirectory' bootstrap/render.py ||
  fail "Bootstrap 未以 workspace 目录 FD 锚定生成接线"
grep -Fq 'os.O_NOFOLLOW' bootstrap/workspace_paths.py ||
  fail "Bootstrap 未拒绝跟随工作空间产物父目录符号链接"
grep -Fq 'os.symlink(target, leaf, dir_fd=parent_fd)' bootstrap/workspace_paths.py ||
  fail "Bootstrap 未相对已验证父目录 FD 接线中央 Project Skill"
grep -Fq 'src_dir_fd=parent_fd, dst_dir_fd=parent_fd' bootstrap/workspace_paths.py ||
  fail "Bootstrap 原子替换未锚定已验证父目录 FD"
grep -Fq 'os.unlink(leaf, dir_fd=parent_fd)' bootstrap/workspace_paths.py ||
  fail "Bootstrap 删除未锚定已验证父目录 FD"
grep -Fq '_assert_entry_unchanged(relative)' bootstrap/workspace_paths.py ||
  fail "Bootstrap 未复核最终产物在校验后是否被替换"
grep -Fq 'for child in os.listdir(directory_fd)' bootstrap/workspace_paths.py ||
  fail "Bootstrap purge 未基于已打开目录 FD 递归枚举状态树"
grep -Fq 'self._remove_tree_at(directory_fd, child' bootstrap/workspace_paths.py ||
  fail "Bootstrap purge 递归删除未保持子目录 FD 锚定"
if grep -Eq 'shutil\.rmtree|Path\([^)]*\)\.rmdir' bootstrap/workspace_registry.py; then
  fail "Bootstrap workspace purge 仍将绝对路径交给递归删除副作用"
fi

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
