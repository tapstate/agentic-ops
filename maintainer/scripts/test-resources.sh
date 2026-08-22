#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
repo_root="$(cd "$script_dir/../.." && pwd -P)"
cd "$repo_root"

fail() {
  printf '资源合同验证失败：%s\n' "$1" >&2
  exit 1
}

require_file() {
  [ -f "$1" ] || fail "缺少文件 $1"
}

require_executable() {
  [ -x "$1" ] || fail "缺少可执行入口 $1"
}

require_file maintainer/AGENTS.md
require_file developer/AGENTS.md
require_file shared/README.md
require_file shared/integration/README.md
require_file shared/integration/task-to-pr-event.schema.json
require_file shared/integration/task-to-pr-manifest.schema.json
require_file shared/integration/task-to-pr-result.schema.json
require_file .agentic-ops-source
require_file maintainer/pyproject.toml
require_file developer/pyproject.toml
require_file maintainer/uv.lock
require_file developer/uv.lock
require_file developer/standards/agent-guides.md
require_file docs/development-engineers/agent-init.md
require_executable maintainer/bin/ao-maint
require_executable developer/bootstrap/ao-work
require_executable developer/bootstrap/install.sh
require_executable developer/bootstrap/update.sh
require_executable developer/bootstrap/rollback.sh
require_executable developer/bootstrap/install-verify-branch.sh
require_executable developer/tests/bootstrap/test_install_boundary.sh
require_executable developer/tests/bootstrap/test_install_verify_boundary.sh
require_executable maintainer/scripts/release.sh
require_executable maintainer/scripts/hotfix.sh
require_executable maintainer/scripts/test-python-runtime.sh
require_executable maintainer/scripts/test-release-workflow.sh
require_executable .githooks/pre-commit
require_executable .githooks/pre-push
require_file maintainer/standards/git/story-review-policy.yaml

grep -q '原子步骤成功不是会话终点' maintainer/rules/source-maintenance.md ||
  fail "maintainer Rule 缺少会话连续推进约束"
grep -q '原子步骤成功不是会话终点' developer/rules/ai-execution.md ||
  fail "developer Rule 缺少会话连续推进约束"
grep -q '^| D-054 | 原子步骤成功不是会话终点 |' docs/decision-log.md ||
  fail "设计决策记录缺少 AO-68 会话推进与停止语义"
shared_entries="$(find shared -mindepth 1 -print | LC_ALL=C sort)"
expected_shared_entries="$(printf '%s\n' \
  shared/README.md \
  shared/integration \
  shared/integration/README.md \
  shared/integration/task-to-pr-event.schema.json \
  shared/integration/task-to-pr-manifest.schema.json \
  shared/integration/task-to-pr-result.schema.json \
  shared/standards \
  shared/standards/jira-comment-template.schema.json)"
if [ "$shared_entries" != "$expected_shared_entries" ]; then
  fail "shared 只能包含根准入说明、integration 准入说明、三个 task-to-pr JSON Schema 和 standards 评论模板"
fi
if find shared -type l -print -quit | grep . >/dev/null; then
  fail "shared 不得包含符号链接"
fi
if find shared -type f -perm -111 -print -quit | grep . >/dev/null; then
  fail "shared 不得包含任何可执行文件"
fi
if find shared -type f \( \
    -name 'AGENTS.md' -o -name 'SKILL.md' -o \
    -name '*.py' -o -name '*.pyc' -o -name '*.sh' \
  \) -print -quit | grep . >/dev/null; then
  fail "shared 不得包含 Python、Shell 或 AI 入口"
fi

grep -q 'git show HEAD:.agentic-ops-source' .githooks/pre-commit ||
  fail "pre-commit 未从提交事实识别 AgenticOps 源头，删除 marker 可能绕过故事门禁"
grep -q 'git cat-file -e ":\$required_path"' .githooks/pre-commit ||
  fail "pre-commit 未验证暂存快照中的故事门禁关键资产"
grep -q 'git write-tree' .githooks/pre-commit ||
  fail "pre-commit 未从 index 创建隔离候选快照"
grep -q 'AGENTIC_OPS_STORY_GATE_STAGE=pre_commit' .githooks/pre-commit ||
  fail "pre-commit 未把候选检查限定为固定验收门禁"
grep -q 'AGENTIC_OPS_STORY_GATE_STAGE=pre_push' .githooks/pre-push ||
  fail "pre-push 未执行分支感知的后置代码审查门禁"
grep -q 'git diff --quiet HEAD' .githooks/pre-push ||
  fail "pre-push 未拒绝未提交的 Runtime 或分支策略篡改"
grep -q 'maintainer/standards/git/story-review-policy.yaml' .githooks/pre-commit ||
  fail "pre-commit 未要求版本化故事审查分支策略"
grep -q 'head_commit:maintainer/runtime/src/ao_maint/story_gate/service.py' \
  .githooks/pre-commit ||
  fail "pre-commit 未优先从已接受 HEAD 加载故事门禁 Runtime"
grep -q 'AGENTIC_OPS_TRUSTED_HOOK_LAUNCHER_V1' \
  maintainer/scripts/lib/development-workflow.sh ||
  fail "研发流程未安装 Git common directory trusted Hook launcher"
grep -q 'required_approving_review_count": 1' \
  maintainer/scripts/lib/development-workflow.sh ||
  fail "main Ruleset 未要求至少一个独立人工批准"
grep -q 'require_last_push_approval": true' \
  maintainer/scripts/lib/development-workflow.sh ||
  fail "main Ruleset 未阻止最后推送者自批"
grep -q 'dismiss_stale_reviews_on_push": true' \
  maintainer/scripts/lib/development-workflow.sh ||
  fail "main Ruleset 未在新提交后撤销旧批准"
grep -q 'parameters.dismiss_stale_reviews_on_push' \
  maintainer/scripts/lib/development-workflow.sh ||
  fail "main Ruleset 漂移回读未检查新提交撤销旧批准字段"
grep -Fq '.conditions.ref_name.include == ["refs/heads/main"]' \
  maintainer/scripts/lib/development-workflow.sh ||
  fail "main Ruleset 漂移回读未要求 include 精确命中 main"
grep -Fq '.conditions.ref_name.exclude == []' \
  maintainer/scripts/lib/development-workflow.sh ||
  fail "main Ruleset 漂移回读未拒绝排除 main 的配置"
grep -q 'required_review_thread_resolution": true' \
  maintainer/scripts/lib/development-workflow.sh ||
  fail "main Ruleset 未要求解决全部审查线程"
grep -q 'release_story_gate_baseline_upgrade_required' \
  maintainer/scripts/lib/release-common.sh ||
  fail "发布流程未在 origin/main 缺少故事门禁基线时失败关闭"
grep -Fq 'PYTHONPATH="$baseline_snapshot/maintainer/runtime/src"' \
  .githooks/pre-commit ||
  fail "pre-commit 隔离快照未显式加载受信 HEAD Runtime"
if grep -Fq 'ln -s "$python_bin" "$baseline_snapshot/maintainer/.venv/bin/python"' \
  .githooks/pre-commit; then
  fail "pre-commit 不得通过快照内解释器链接破坏虚拟环境定位"
fi
grep -q 'release_story_gate_trust_root_changed' \
  maintainer/scripts/lib/release-common.sh ||
  fail "发布流程未阻止信任根变更走自动 publish"
grep -q 'story_gate_local_state_unsafe' .githooks/pre-commit ||
  fail "pre-commit 未对故事确认/验收路径链接与特殊文件失败关闭"
grep -q 'release_story_gate_local_state_unsafe' \
  maintainer/scripts/lib/release-common.sh ||
  fail "发布流程未对故事确认/验收路径链接与特殊文件失败关闭"

for story_review_asset in \
  AGENTS.md \
  maintainer/AGENTS.md \
  maintainer/rules/source-maintenance.md \
  maintainer/skills/guard-story-quality/SKILL.md \
  docs/user-stories/project-maintainer/pm-007-story-quality-gate.md; do
  grep -q '确认事项' "$story_review_asset" ||
    fail "故事审查资产未逐项要求确认事项：$story_review_asset"
  grep -q '变更点' "$story_review_asset" ||
    fail "故事审查资产未逐项要求变更点：$story_review_asset"
  grep -q '风险' "$story_review_asset" ||
    fail "故事审查资产未逐项要求风险：$story_review_asset"
  if grep -Eq 'user-confirmation:[^ ]*<impact|等待人工确认同一.*impact_id|请公司员工指导员确认影响报告' \
    "$story_review_asset"; then
    fail "故事审查资产仍要求用户确认裸 impact_id：$story_review_asset"
  fi
done

grep -q '^ao-maint = "ao_maint\.cli:main"$' maintainer/pyproject.toml ||
  fail "maintainer Python 入口不是 ao-maint"
grep -q '^ao-work = "ao_work\.work_cli:main"$' developer/pyproject.toml ||
  fail "developer Python 入口不是 ao-work"
if rg -n '^agentic-cli[[:space:]]*=' maintainer/pyproject.toml developer/pyproject.toml; then
  fail "不得注册旧 agentic-cli 兼容入口"
fi
if [ -e bin/agentic-cli ]; then
  fail "根 bin 中仍存在可执行的旧 agentic-cli 本地入口；请删除本地残留后验证"
fi
if [ -e .venv/bin/agentic-cli ]; then
  fail "根 .venv 中仍存在可执行的旧 agentic-cli 本地入口；请删除本地残留后验证"
fi
if [ -e agent-guides.md ] || [ -e agent-init.md ]; then
  fail "developer 专属指引不得留在 maintainer 根工作面"
fi
if rg -n -- '--mode([^l]|$)|--workplane|--role' maintainer/runtime/src developer/runtime/src; then
  fail "CLI 不得通过参数切换工作面"
fi

for skill in maintainer/skills/*/SKILL.md; do
  grep -q '^metadata:$' "$skill" && grep -q '^  workplane: maintainer$' "$skill" ||
    fail "维护 Skill 未声明唯一 maintainer 工作面：$skill"
  if grep -q '^allowed_modes:' "$skill"; then
    fail "维护 Skill 仍使用多模式元数据：$skill"
  fi
done
for skill in developer/skills/*/SKILL.md; do
  grep -q '^metadata:$' "$skill" && grep -q '^  workplane: developer$' "$skill" ||
    fail "研发 Skill 未声明唯一 developer 工作面：$skill"
  if grep -q '^allowed_modes:' "$skill"; then
    fail "研发 Skill 仍使用多模式元数据：$skill"
  fi
done

require_file maintainer/standards/stories/project-quality.yaml
grep -q '^  - maintainer$' maintainer/standards/stories/project-quality.yaml ||
  fail "故事注册表缺少 maintainer 类别"
grep -q '^  - developer$' maintainer/standards/stories/project-quality.yaml ||
  fail "故事注册表缺少 developer 类别"
if rg -n 'project_maintenance|development_engineer' maintainer/standards/stories/project-quality.yaml; then
  fail "故事注册表仍使用旧类别"
fi
require_file developer/standards/company/core-hard-rules.md
require_file developer/standards/connections/tapdata-cloud.yaml
require_file developer/standards/contracts/operations/workspace-init.yaml
require_file developer/standards/contracts/operations/jira-authorization.yaml
require_file developer/standards/contracts/operations/jira-comment.yaml
require_file developer/standards/contracts/operations/jira-description.yaml
require_file developer/standards/contracts/operations/jira-worklog.yaml
require_file developer/standards/projects/tapdata/profile.yaml
require_file developer/standards/runbooks/jira-write-recovery.md
require_file maintainer/standards/experiments/ao/profile.yaml

if rg -n 'Offline Fake 全链路|真实 Jira 全链路适配' \
  maintainer/runtime maintainer/standards maintainer/skills docs/examples/end-to-end-demo.md \
  docs/runtime/local-test-sample-configuration-design.md; then
  fail "离线合同回归不得冒充真实任务到 PR 全链路"
fi

if rg -n '\]\((\.\./)+(docs|maintainer)/' developer --glob '*.md'; then
  fail "developer 安装资产链接到 sparse checkout 不包含的根 docs 或 maintainer 资产"
fi

# 边界说明可以明确禁止跨工作面，但 developer 安装资产不得正向引导 AI
# 读取 sparse checkout 中不存在的源仓 docs 或 maintainer 路径。
if rg -n '(读取|加载|执行|遵守|参照|参考|由[^。]{0,60}约束)[^。]*(maintainer/|docs/[A-Za-z0-9._/-]+)' \
  developer --glob '*.md' |
  rg -v '(不得|禁止|不应|不能|不允许|不适用|不属于|无需|不需要)'; then
  fail "developer 安装资产正向引导读取根 docs 或 maintainer 资产"
fi
grep -q '任一禁止动作已经发生.*最终状态只能输出 `failed`' \
  developer/standards/README.md ||
  fail "developer 资产入口未把已经发生的禁止动作固定为 failed"
if grep -q '任一禁止动作被观察到时只能输出 `blocked`' \
  developer/standards/README.md; then
  fail "developer 资产入口仍把已经发生的禁止动作错误归类为 blocked"
fi

grep -q '^workplane: developer$' developer/standards/contracts/operations/workspace-init.yaml ||
  fail "workspace-init 合同未绑定 developer 工作面"
for contract in \
  developer/standards/contracts/operations/jira-authorization.yaml \
  developer/standards/contracts/operations/jira-comment.yaml \
  developer/standards/contracts/operations/jira-description.yaml \
  developer/standards/contracts/operations/jira-worklog.yaml; do
  grep -q '^workplane: developer$' "$contract" ||
    fail "研发操作合同未绑定 developer 工作面：$contract"
done

if [ -e packages/agentic-cli ] || [ -e go.mod ] || [ -e go.sum ]; then
  fail "旧 Go Runtime 仍在现役工作树中"
fi
if [ -d install-resources ]; then
  fail "旧 install-resources 分发目录仍在现役工作树中"
fi
for legacy in \
  scripts/build.sh \
  scripts/test-build.sh \
  scripts/install.sh \
  scripts/update-checksums.sh \
  scripts/test-install.sh \
  scripts/generate-command-catalog.sh \
  scripts/version.sh \
  tests/e2e/ao-profile-flow.sh \
  tests/e2e/local-fake-flow.sh \
  tests/e2e/local-install-flow.sh \
  tests/e2e/problem-resolution-flow.sh; do
  [ ! -e "$legacy" ] || fail "旧 Go 构建或 E2E 入口仍存在：$legacy"
done

if find developer -type f \( -name 'ao-maint' -o -path '*/ao_maint/*' \) -print -quit | grep . >/dev/null; then
  fail "developer 工作面包含 maintainer Runtime 或入口"
fi
if find maintainer -type f \( -name 'ao-work' -o -path '*/ao_work/*' \) -print -quit | grep . >/dev/null; then
  fail "maintainer 工作面包含 developer Runtime 或入口"
fi
if rg -n 'from[[:space:]]+ao_work|import[[:space:]]+ao_work' maintainer/runtime maintainer/scripts; then
  fail "maintainer 代码不得导入 developer Runtime"
fi
if rg -n 'from[[:space:]]+ao_maint|import[[:space:]]+ao_maint' developer/runtime developer/bootstrap; then
  fail "developer 代码不得导入 maintainer Runtime"
fi

test ! -e docs/development-phase-rules.md
test ! -d docs/superpowers
grep '^\.superpowers/$' .gitignore >/dev/null

grep -q 'verification_only_install_forbidden' developer/bootstrap/lib/common.sh ||
  fail "生产维护命令未拒绝 verification-only 验证安装目录"
grep -q 'verification_branch_unreachable' developer/runtime/src/ao_work/installation/__init__.py ||
  fail "Runtime 未实现验证安装的远端可达性门禁"
grep -q 'verification-only' developer/bootstrap/install-verify-branch.sh ||
  fail "验证安装入口未写入 verification-only 标记"
grep -Fq 'developer/bootstrap/lib/common.sh?ref=$bootstrap_source_branch' \
  developer/bootstrap/install-verify-branch.sh ||
  fail "验证安装入口不支持按来源分支远程加载公共库"
grep -Fq 'bootstrap="$(gh api' docs/development-engineers/getting-started.md ||
  fail "developer 安装文档未在执行前检查 gh api 下载结果"
grep -Fq 'set -e' docs/development-engineers/getting-started.md ||
  fail "developer 安装文档未在隔离 Shell 中传播下载或安装失败"
grep -Fq 'contents/developer/bootstrap/install-verify-branch.sh?ref=develop' \
  docs/development-engineers/agent-init.md ||
  fail "研发员初始化文档缺少 develop 远程验证安装入口"
if rg -n 'contents/scripts/install\.sh|AGENTIC_OPS_REPO_URL=.*bash' \
  docs/development-engineers docs/project-rules.md \
  docs/user-stories/development-engineer; then
  fail "现役 developer 文档仍发布旧安装路径或身份覆盖调用"
fi

printf '{"ok":true,"operation":"test_resources","workplanes":["maintainer","developer"]}\n'
