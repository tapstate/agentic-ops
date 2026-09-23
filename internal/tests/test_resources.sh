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
  contracts/product-state.schema.json contracts/station.schema.json \
  contracts/station-state-compatibility.json contracts/station-state-compatibility.schema.json \
  contracts/repository-catalog.schema.json \
  contracts/station-init.schema.json \
  contracts/task-state.schema.json contracts/operation-catalog.json \
  gate/engine.py gate/runner.py \
  policies/operations.json policies/continuity.json policies/defect-repair-strategies.json \
  workflow/task.py workflow/task_store.py workflow/project_rules.py workflow/repair_strategy.py tests/test_station_compatibility.py tests/test_repair_strategy.py \
  workflow/authorization.py workflow/ci.py workflow/evidence.py \
  workflow/quality.py workflow/quality_contract.py workflow/quality_write.py \
  workflow/jira_status.py workflow/jira_watermark.py workflow/jira_tests.py workflow/pr_ready.py \
  contracts/quality-action.schema.json contracts/quality-state.schema.json \
  projects/tapdata/quality.json docs/usage/quality-checkpoints.md tests/test_quality.py tests/test_jira_status.py tests/test_jira_watermark.py \
  projects/tapdata/profile.json projects/tapdata/repositories.json projects/tapdata/admission.json \
  projects/tapdata/skills/tapdata-task/SKILL.md \
  projects/tapdata/skills/tapdata-wiki/SKILL.md projects/tapdata/skills/tapdata-ci-test/SKILL.md bootstrap/shared-repositories.json bootstrap/shared_repositories.py \
  skills/ao-test-takeover/SKILL.md skills/ao-ws-init/SKILL.md \
  skills/ao-review-change/SKILL.md skills/ao-review-change/scripts/review-context.py \
  adapters/station/AGENTS.md adapters/station/agenticops adapters/agents/claude/templates/CLAUDE.md \
  adapters/tools/mcp-requirements.json adapters/tools/mcp.template.json \
  adapters/agents/claude/manifest.json \
  adapters/agents/codex/manifest.json \
  bootstrap/install.sh bootstrap/setup.sh bootstrap/update.sh bootstrap/rollback.sh bootstrap/lifecycle-common.sh \
  bootstrap/station-init.sh bootstrap/render.py bootstrap/station_paths.py bootstrap/agent_registry.py \
  bootstrap/skill_wiring.py \
  bootstrap/product_state.py bootstrap/product_version.py bootstrap/station_registry.py bootstrap/station_compatibility.py \
  workflow/station.py workflow/station_source.py workflow/source_pool.py workflow/station_resources.py workflow/station_archive.py workflow/station_operation.py \
  tests/test_gate.py tests/test_contracts.py tests/test_adapter_boundary.py tests/test_workflow.py tests/test_task_identity.py tests/test_install.sh \
  internal/acceptance.sh internal/bin/story-gate internal/story_gate/stories.yaml \
  internal/story_gate/review-policy.yaml internal/release/release.sh \
  internal/resource-contract.json internal/resource_contract.py \
  internal/tests/test_resource_contract.py; do
  require_file "$file"
done

for file in \
    agenticops gate/runner.py \
  workflow/task.py workflow/authorization.py workflow/ci.py workflow/evidence.py \
  workflow/jira_status.py workflow/jira_watermark.py workflow/pr_ready.py \
  bootstrap/install.sh bootstrap/setup.sh bootstrap/update.sh bootstrap/rollback.sh bootstrap/lifecycle-common.sh \
  bootstrap/station-init.sh bootstrap/render.py bootstrap/agent_registry.py \
  bootstrap/skill_wiring.py \
  bootstrap/product_state.py bootstrap/product_version.py bootstrap/station_registry.py bootstrap/station_compatibility.py \
  tests/test_install.sh internal/acceptance.sh internal/bin/story-gate internal/release/release.sh \
  internal/release/hotfix.sh internal/tests/test_runtime.sh \
  internal/tests/test_resources.sh internal/tests/test_release.sh \
  .githooks/pre-commit .githooks/pre-push .githooks/reference-transaction; do
  require_executable "$file"
done

test "$(sed -n '1p' .agentic-ops-source)" = "source" ||
  fail ".agentic-ops-source 必须固定为 source"

grep -F '初始化完成：branch=%s，ref=%s，源码池=%s' bootstrap/setup.sh >/dev/null ||
  fail "setup 必须明确初始化产品源码目录"
grep -F 'face="$(lifecycle_work_face "$mode")"' bootstrap/update.sh >/dev/null ||
  fail "update 必须根据产品根目录 mode 区分生命周期路径"

python3 -m json.tool policies/operations.json >/dev/null
python3 -m json.tool policies/continuity.json >/dev/null
python3 -m json.tool policies/defect-repair-strategies.json >/dev/null
python3 -m json.tool contracts/gate-request.schema.json >/dev/null
python3 -m json.tool contracts/gate-decision.schema.json >/dev/null
python3 -m json.tool contracts/adapter-manifest.schema.json >/dev/null
python3 -m json.tool contracts/operation-catalog.schema.json >/dev/null
python3 -m json.tool contracts/product-state.schema.json >/dev/null
python3 -m json.tool contracts/repository-catalog.schema.json >/dev/null
python3 -m json.tool contracts/station.schema.json >/dev/null
python3 -m json.tool contracts/station-init.schema.json >/dev/null
python3 -m json.tool contracts/task-state.schema.json >/dev/null
python3 -m json.tool contracts/operation-catalog.json >/dev/null
python3 -m json.tool projects/tapdata/profile.json >/dev/null
python3 -m json.tool projects/tapdata/repositories.json >/dev/null
python3 -m json.tool projects/tapdata/admission.json >/dev/null
python3 -m json.tool adapters/tools/mcp-requirements.json >/dev/null
python3 -m json.tool adapters/tools/mcp.template.json >/dev/null
for manifest in adapters/agents/*/manifest.json; do
  python3 -m json.tool "$manifest" >/dev/null
done

grep -Fq 'sparse-checkout set adapters bootstrap contracts gate policies projects workflow' bootstrap/install.sh ||
  fail "安装脚本没有限制为产品目录"
grep -Fq '__AGENTIC_OPS_HOME__' adapters/station/AGENTS.md ||
  fail "工作目录入口缺少安装路径占位符"
python3 - <<'PY' || fail "Project、MCP 与 Codex 资源版本不一致"
import json
from pathlib import Path

from bootstrap.agent_registry import discover

manifests = discover(Path("."))
assert all(manifest["schema_version"] == 3 for manifest in manifests.values())
assert not list(Path("adapters").rglob("*.py"))

requirements = json.loads(Path("adapters/tools/mcp-requirements.json").read_text(encoding="utf-8"))
template = json.loads(Path("adapters/tools/mcp.template.json").read_text(encoding="utf-8"))
assert set(requirements["required_servers"]) == {"atlassian"}
assert set(template["mcpServers"]) == set(requirements["required_servers"])
for name, requirement in requirements["required_servers"].items():
    assert template["mcpServers"][name] == {"type": "http", "url": requirement["url"]}

station_entry = Path("adapters/station/AGENTS.md").read_text(encoding="utf-8")
assert 'mcp-requirements.json' in station_entry
assert '首次需要 Jira 事实时检查 `atlassian`' in station_entry
assert '不得伪造结果' in station_entry
assert 'GitHub MCP、gh 或其它工具由 Agent 按已有授权选择' in station_entry

profile = json.loads(Path("projects/tapdata/profile.json").read_text(encoding="utf-8"))
assert "statuses" not in profile and "transitions" not in profile
takeover = profile["jira"]["status_sync"]["attempts"]["takeover"]
assert takeover["transition_id"] == "421"
assert takeover["from"] == ["Analyzed"] and takeover["to"] == "In Progress"
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
grep -Fq '接管或继续成功只是流程恢复点' adapters/station/AGENTS.md ||
  fail "工位入口未声明接管后的连续推进"
grep -Fq '远程候选参考' adapters/station/AGENTS.md ||
  fail "工位入口未声明远程源码证据边界"
grep -Fq 'task.py takeover --issue-key' \
  projects/tapdata/skills/tapdata-task/SKILL.md ||
  fail "TapData Skill 未声明完整工程接管"
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
  fail "setup 未刷新产品源码 Skill 接线"
grep -Fq 'bootstrap/skill_wiring.py' bootstrap/update.sh ||
  fail "update 未刷新产品源码 Skill 接线"
grep -Fq 'StationDirectory' bootstrap/render.py ||
  fail "Bootstrap 未以 station 目录 FD 锚定生成接线"
grep -Fq 'os.O_NOFOLLOW' bootstrap/station_paths.py ||
  fail "Bootstrap 未拒绝跟随工位产物父目录符号链接"
grep -Fq 'os.symlink(target, leaf, dir_fd=parent_fd)' bootstrap/station_paths.py ||
  fail "Bootstrap 未相对已验证父目录 FD 接线中央 Project Skill"
grep -Fq 'src_dir_fd=parent_fd, dst_dir_fd=parent_fd' bootstrap/station_paths.py ||
  fail "Bootstrap 原子替换未锚定已验证父目录 FD"
grep -Fq 'os.unlink(leaf, dir_fd=parent_fd)' bootstrap/station_paths.py ||
  fail "Bootstrap 删除未锚定已验证父目录 FD"
grep -Fq '_assert_entry_unchanged(relative)' bootstrap/station_paths.py ||
  fail "Bootstrap 未复核最终产物在校验后是否被替换"
grep -Fq 'for child in os.listdir(directory_fd)' bootstrap/station_paths.py ||
  fail "Bootstrap purge 未基于已打开目录 FD 递归枚举状态树"
grep -Fq 'self._remove_tree_at(directory_fd, child' bootstrap/station_paths.py ||
  fail "Bootstrap purge 递归删除未保持子目录 FD 锚定"
if grep -Eq 'shutil\.rmtree|Path\([^)]*\)\.rmdir' bootstrap/station_registry.py; then
  fail "Bootstrap station purge 仍将绝对路径交给递归删除副作用"
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
