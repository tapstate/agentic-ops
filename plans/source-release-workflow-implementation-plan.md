# AgenticOps 源码发布工作流实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 AgenticOps 从研发期 `main` 直提规则迁移为 `develop` 日常开发、PR 合入 `main`、二段式 Tag 和独立 Hotfix 的正式源码发布流程。

**Architecture:** 使用 `scripts/release.sh` 和 `scripts/hotfix.sh` 作为两个用户入口，公共 Git、GitHub、验证、确认和审计逻辑收敛到 `scripts/lib/`。GitHub 仓库规则和版本化 Hooks 共同阻止 `main` 直提，发布脚本在临时 worktree 完成固定全量验证后创建或复用 PR、启用 Merge commit Auto-merge、等待合并并校验远端事实。

**Tech Stack:** Bash 3.2+、Git、GitHub CLI、GitHub Repository Rulesets API、Go、现有脚本和本地 E2E。

## Global Constraints

- `main` 是 GitHub 默认分支、稳定分支和安装来源；日常开发使用 `develop`。
- 正常发布只允许 `develop → main`，Hotfix 只允许 `<user>/<jira-id>/fix-main → main`。
- 所有合入 `main` 的 PR 使用 Merge commit；不配置必需 GitHub CI 或必需 Review。
- 版本格式保持 `TYPE-vX.Y.COMMIT_NUM-COMMIT`；`COMMIT_NUM` 算法保持现状，允许跳号。
- Git tag 只允许 `vX.Y`；正常发布创建版本线基线，Hotfix 复用已有基线。
- `prepare` 可以生成二进制和 checksum，但不得暂存、提交或推送；`publish` 要求工作区干净。
- 真实远端副作用必须在完整验证后取得最终人工确认；测试只能使用临时仓库和 fake `gh`。
- 不回滚、覆盖或丢弃当前工作区中已有的 Task 6、AO 和文档改动；每次提交只暂存当前任务明确列出的文件或 hunk。
- 正式设计维护在 `docs/`，实施状态和验证命令维护在 `plans/`；不得创建 `docs/superpowers/`。
- 提交使用 Jira `TAP-12371`，中文标题和中文正文；非平凡提交必须包含 body。

---

## 文件结构

### 新增文件

- `.githooks/pre-commit`：阻止在 `main` 直接提交。
- `.githooks/pre-push`：阻止直接向远端 `main` 推送。
- `scripts/lib/release-common.sh`：仓库、版本、同步、验证、PR、等待、确认、审计公共函数。
- `scripts/lib/development-workflow.sh`：Hooks、`develop` 和 GitHub `main` ruleset 的检查与幂等配置。
- `scripts/release.sh`：正常发布 `prepare` / `publish` 用户入口。
- `scripts/hotfix.sh`：Hotfix `create` / `prepare` / `publish` 用户入口。
- `scripts/test-release-workflow.sh`：临时仓库和 fake `gh` 合同测试。

### 修改文件

- `scripts/test-resources.sh`：校验新脚本、Hooks、永久规则和研发期文件删除。
- `AGENTS.md`：正式分支、提交、发布和 Shell 维护脚本例外。
- `README.md`、`docs/README.md`：删除研发期入口，增加正式发布入口。
- `docs/project-rules.md`、`docs/ai-working-rules.md`、`docs/development-style.md`：迁入永久规则。
- `docs/maintainers/getting-started.md`、`docs/review-checklist.md`：维护者命令和永久发布检查清单。
- `docs/architecture/agenticops-current-design.md`、`docs/architecture/project-structure.md`：分支和脚本架构。
- `docs/runtime/versioning.md`：二阶段 Tag、普通发布和 Hotfix 版本语义。
- `docs/user-stories/project-maintainer/pm-003-release-assets.md`、`docs/user-stories/project-maintainer/pm-006-release-governance.md`：发布故事。
- `plans/design-implementation-gap-todo-v1.md`：以源码发布治理替代旧 GitHub Release 缺口。
- `docs/development-phase-rules.md`：删除。

## Task 1: 建立脚本测试骨架和本地 main 防护

**Files:**
- Create: `.githooks/pre-commit`
- Create: `.githooks/pre-push`
- Create: `scripts/test-release-workflow.sh`
- Modify: `scripts/test-resources.sh`

**Interfaces:**
- Consumes: Git 标准环境变量、pre-push stdin 的 `<local-ref> <local-sha> <remote-ref> <remote-sha>`。
- Produces: 可执行 Hooks；`bash scripts/test-release-workflow.sh` 测试入口。

- [x] **Step 1: 写 Hook 失败测试**

在 `scripts/test-release-workflow.sh` 中创建临时仓库，复制 `.githooks/`，设置 `core.hooksPath .githooks`，并断言：

```bash
git -C "$repo" switch main >/dev/null
printf 'blocked\n' > "$repo/blocked.txt"
git -C "$repo" add blocked.txt
if git -C "$repo" commit -m "must fail" >"$tmp/commit.out" 2>"$tmp/commit.err"; then
  echo "expected main commit to be blocked" >&2
  exit 1
fi
grep 'direct commit to main is prohibited' "$tmp/commit.err"

git -C "$repo" switch -c develop >/dev/null
git -C "$repo" commit -m "develop commit" >/dev/null
```

为 pre-push 直接执行 Hook，输入 `refs/heads/main`，断言返回非零；输入 `refs/heads/develop`，断言返回零。

- [x] **Step 2: 运行测试并确认 RED**

Run:

```bash
bash scripts/test-release-workflow.sh
```

Expected: FAIL，因为 `.githooks/pre-commit` 和 `.githooks/pre-push` 不存在。

- [x] **Step 3: 实现最小 Hooks**

`pre-commit` 使用：

```bash
#!/usr/bin/env bash
set -euo pipefail

branch="$(git branch --show-current)"
if [ "$branch" = "main" ]; then
  echo "direct commit to main is prohibited; use develop or a fix-main branch" >&2
  exit 1
fi
```

`pre-push` 逐行读取 stdin，只要 `remote_ref` 为 `refs/heads/main` 就失败：

```bash
while read -r local_ref local_sha remote_ref remote_sha; do
  if [ "$remote_ref" = "refs/heads/main" ]; then
    echo "direct push to main is prohibited; use a pull request" >&2
    exit 1
  fi
done
```

- [x] **Step 4: 验证 GREEN 和资源入口**

Run:

```bash
chmod 0755 .githooks/pre-commit .githooks/pre-push scripts/test-release-workflow.sh
bash scripts/test-release-workflow.sh
bash scripts/test-resources.sh
```

Expected: 两个命令均输出 `"ok":true` 且退出码为 0。

- [x] **Step 5: 提交 Hook 和测试骨架**

```bash
git add .githooks/pre-commit .githooks/pre-push scripts/test-release-workflow.sh scripts/test-resources.sh
git commit -m "Test(release): TAP-12371 增加 main 本地防护测试" -m "新增版本化 pre-commit 和 pre-push Hook，阻止 main 直提，并建立发布工作流临时仓库测试入口。"
```

## Task 2: 实现研发流程配置门禁

**Files:**
- Create: `scripts/lib/development-workflow.sh`
- Modify: `scripts/test-release-workflow.sh`

**Interfaces:**
- Produces: `workflow_check_or_configure(mode)`、`workflow_check_hooks()`、`workflow_check_develop()`、`workflow_check_main_ruleset()`。
- Side effects: 经用户确认后设置 `core.hooksPath`、推送初始 `develop`、通过 `gh api` 创建或修复唯一命名的 ruleset。

- [x] **Step 1: 写缺失配置和拒绝配置测试**

测试通过 fake `gh` 记录参数，覆盖：

```bash
if workflow_check_or_configure check; then
  echo "expected missing workflow configuration" >&2
  exit 1
fi
grep 'workflow_configuration_required' "$failure_output"

if printf 'n\n' | workflow_check_or_configure interactive; then
  echo "expected rejected configuration" >&2
  exit 1
fi
test ! -s "$fake_gh_writes"
```

再测试 `--configure-workflow` 等价的非交互模式会配置并复检成功，第二次执行不产生新增写调用。

- [x] **Step 2: 运行测试并确认 RED**

Run: `bash scripts/test-release-workflow.sh`

Expected: FAIL，提示 `scripts/lib/development-workflow.sh` 不存在。

- [x] **Step 3: 实现配置检查数据模型**

使用固定 ruleset 名：

```text
agentic-ops-main-pull-request-only
```

期望规则必须包含：

```json
{
  "enforcement": "active",
  "target": "branch",
  "conditions": {"ref_name": {"include": ["refs/heads/main"], "exclude": []}},
  "bypass_actors": [],
  "rules": [
    {"type": "deletion"},
    {"type": "non_fast_forward"},
    {"type": "pull_request", "parameters": {
      "allowed_merge_methods": ["merge"],
      "dismiss_stale_reviews_on_push": false,
      "require_code_owner_review": false,
      "require_last_push_approval": false,
      "required_approving_review_count": 0,
      "required_review_thread_resolution": false
    }}
  ]
}
```

配置函数只在检查结果不一致且取得确认后调用 `gh api --method POST/PATCH`。本地 Hooks 使用：

```bash
git config core.hooksPath .githooks
```

远端 `develop` 不存在时，从当前已确认的 `develop` 推送：

```bash
git push -u origin develop
```

仓库设置同时校验并配置：

```json
{"default_branch":"main","allow_auto_merge":true,"allow_merge_commit":true}
```

- [x] **Step 4: 验证配置幂等和无权限失败**

Run: `bash scripts/test-release-workflow.sh`

Expected: PASS，并覆盖 `workflow_configuration_required`、`workflow_configuration_rejected`、`workflow_configuration_permission_denied`。

- [x] **Step 5: 提交配置门禁**

```bash
git add scripts/lib/development-workflow.sh scripts/test-release-workflow.sh
git commit -m "Feat(release): TAP-12371 增加正式研发流程配置门禁" -m "实现 Hooks、develop 和 main PR-only ruleset 的检查、交互确认、非交互显式配置和幂等复检。"
```

## Task 3: 实现发布公共函数和正常发布 prepare

**Files:**
- Create: `scripts/lib/release-common.sh`
- Create: `scripts/release.sh`
- Modify: `scripts/test-release-workflow.sh`

**Interfaces:**
- Produces: `release_fail(code, stage, message, action)`、`release_require_repo()`、`release_require_clean()`、`release_validate_version()`、`release_require_synced_branch()`、`release_build_assets()`、`release_write_audit()`。
- CLI: `scripts/release.sh prepare --version vX.Y [--configure-workflow]`。

- [x] **Step 1: 写 prepare 参数、Tag 和构建测试**

覆盖以下行为：

```bash
scripts/release.sh prepare --version 0.3
# => invalid_release_version

scripts/release.sh prepare --version v0.3
# => 创建 annotated tag v0.3，调用构建函数，不执行 git add/commit/push

scripts/release.sh prepare --version v0.3
# => 同一 tag 是当前 HEAD 的祖先且远端不存在时允许重复执行
```

fake build 函数写入四个平台产物和 checksum；测试断言 prepare 结束后工作区只出现这些生成变更。

- [x] **Step 2: 运行测试并确认 RED**

Run: `bash scripts/test-release-workflow.sh`

Expected: FAIL，因为 `scripts/release.sh` 不存在。

- [x] **Step 3: 实现公共失败输出和 prepare**

稳定错误码至少包括：

```text
invalid_release_command
invalid_release_version
wrong_release_branch
dirty_worktree
branch_behind_remote
branch_diverged
release_tag_conflict
release_tag_remote_exists
release_build_failed
```

`prepare` 固定要求 `develop`，调用 `workflow_check_or_configure`，创建：

```bash
git tag -a "$version" -m "AgenticOps $version version baseline"
```

然后调用 `bash scripts/build.sh`。成功输出 JSON 至少包含：

```json
{"ok":true,"operation":"release_prepare","version":"v0.3","agentic_next_action":"review_and_commit_generated_assets"}
```

- [x] **Step 4: 验证 prepare GREEN**

Run:

```bash
bash scripts/test-release-workflow.sh
bash -n scripts/release.sh scripts/lib/release-common.sh scripts/lib/development-workflow.sh
```

Expected: PASS，且 shell 语法检查退出 0。

- [x] **Step 5: 提交正常发布 prepare**

```bash
git add scripts/release.sh scripts/lib/release-common.sh scripts/test-release-workflow.sh
git commit -m "Feat(release): TAP-12371 实现正常发布准备阶段" -m "增加二段式版本校验、本地版本基线 Tag、四平台产物生成和可重复准备流程，保持提交与推送由后续阶段处理。"
```

## Task 4: 实现完整验证和正常发布 publish

**Files:**
- Modify: `scripts/lib/release-common.sh`
- Modify: `scripts/release.sh`
- Modify: `scripts/test-release-workflow.sh`

**Interfaces:**
- Produces: `release_run_full_verification(head)`、`release_confirm_publish()`、`release_find_or_create_pr(head, base)`、`release_enable_auto_merge(pr)`、`release_wait_for_merge(pr)`、`release_verify_remote_contains(commit)`。
- CLI: `scripts/release.sh publish --version vX.Y [--confirm-release] [--configure-workflow]`。

- [x] **Step 1: 写 publish 无副作用失败测试**

覆盖：工作区脏、Tag 缺失、Tag 非祖先、验证失败、拒绝确认。每个场景断言 fake 远端写日志为空：

```bash
test ! -s "$fake_git_pushes"
test ! -s "$fake_gh_writes"
```

再覆盖成功路径：push develop、创建 PR、`gh pr merge --merge --auto`、轮询 `gh pr view --json state,mergeCommit,url,number`、fetch main、祖先关系验证、push tag。

- [x] **Step 2: 运行测试并确认 RED**

Run: `bash scripts/test-release-workflow.sh`

Expected: FAIL，因为 `publish` 尚未注册。

- [x] **Step 3: 实现固定完整验证**

`release_run_full_verification` 使用 `mktemp -d` 和 detached worktree，依次执行：

```bash
go test ./...
bash scripts/test-resources.sh
bash scripts/test-build.sh
bash scripts/test-install.sh
bash tests/e2e/ao-profile-flow.sh
bash tests/e2e/local-fake-flow.sh
bash tests/e2e/local-install-flow.sh
bash tests/e2e/problem-resolution-flow.sh
```

函数结束时删除仅由本次创建的临时 worktree；验证命令列表不得通过 CLI 参数替换或跳过。

- [x] **Step 4: 实现确认、PR、Auto-merge 和审计**

交互确认展示版本、仓库、HEAD、提交列表和验证结果。无 TTY 时只有 `--confirm-release` 才通过。

PR 标题固定为：

```text
Release: v0.3 合并 develop 到 main
```

PR body 包含版本、HEAD、固定验证命令和 UTC 完成时间。审计写入：

```text
.local/release-runs/release-v0.3-<head>.json
```

成功输出 `operation=release_publish`、PR URL、merge commit、tag 和 `agentic_next_action=release_completed`。

- [x] **Step 5: 验证 publish GREEN**

Run:

```bash
bash scripts/test-release-workflow.sh
bash -n scripts/release.sh scripts/lib/release-common.sh
```

Expected: PASS；测试证明验证失败和确认拒绝发生在任何远端写之前。

- [x] **Step 6: 提交正常发布 publish**

```bash
git add scripts/release.sh scripts/lib/release-common.sh scripts/test-release-workflow.sh
git commit -m "Feat(release): TAP-12371 实现 develop 到 main 受控发布" -m "固定执行完整本地验收，取得最终确认后推送 develop、创建或复用 PR、启用 Merge Auto-merge、等待远端合并并安全推送版本基线 Tag。"
```

## Task 5: 实现 Hotfix create 和 prepare

**Files:**
- Create: `scripts/hotfix.sh`
- Modify: `scripts/lib/release-common.sh`
- Modify: `scripts/test-release-workflow.sh`

**Interfaces:**
- CLI: `scripts/hotfix.sh create --jira-id <KEY> [--user <name>]`。
- CLI: `scripts/hotfix.sh prepare [--configure-workflow]`。
- Produces: `release_parse_hotfix_branch()`、`release_find_iteration_tag()`。

- [ ] **Step 1: 写 Hotfix 分支和版本基线测试**

覆盖：

```text
AO-123 -> harsen/AO-123/fix-main
TAP-12371 -> harsen/TAP-12371/fix-main
ao-123 -> invalid_jira_id
已存在同名本地或远端分支 -> hotfix_branch_exists
非 fix-main 分支执行 prepare -> invalid_hotfix_branch
main 历史没有 vX.Y -> iteration_tag_missing
```

断言 `create` 从 fetch 后的 `origin/main` 创建分支，不从本地陈旧 `main` 创建。

- [ ] **Step 2: 运行测试并确认 RED**

Run: `bash scripts/test-release-workflow.sh`

Expected: FAIL，因为 `scripts/hotfix.sh` 不存在。

- [ ] **Step 3: 实现 create 和 prepare**

Jira ID 使用：

```text
^[A-Z][A-Z0-9]+-[1-9][0-9]*$
```

用户名优先取 `--user`，否则取 `git config user.name` 并转换为安全的小写分支片段；转换后为空或包含非法字符时返回 `invalid_git_user`。

`prepare` 解析最近的已合并二段式 tag，调用 `bash scripts/build.sh`，但不创建 tag、不提交、不推送。

- [ ] **Step 4: 验证 Hotfix prepare GREEN**

Run:

```bash
bash scripts/test-release-workflow.sh
bash -n scripts/hotfix.sh
```

Expected: PASS。

- [ ] **Step 5: 提交 Hotfix create 和 prepare**

```bash
git add scripts/hotfix.sh scripts/lib/release-common.sh scripts/test-release-workflow.sh
git commit -m "Feat(hotfix): TAP-12371 实现 main 紧急修复准备流程" -m "新增标准 fix-main 分支创建、Jira 编号和用户名校验，以及复用既有二段式版本基线的构建准备流程。"
```

## Task 6: 实现 Hotfix publish

**Files:**
- Modify: `scripts/hotfix.sh`
- Modify: `scripts/lib/release-common.sh`
- Modify: `scripts/test-release-workflow.sh`

**Interfaces:**
- CLI: `scripts/hotfix.sh publish [--confirm-release] [--configure-workflow]`。
- Reuses: Task 4 的完整验证、最终确认、PR、Auto-merge、等待和远端包含关系函数。

- [ ] **Step 1: 写 Hotfix publish 测试**

成功场景断言：

```text
push <user>/<jira-id>/fix-main
PR head=<user>/<jira-id>/fix-main base=main
merge method=merge auto=true
等待 state=MERGED
验证 origin/main 包含修复 HEAD
没有 git push refs/tags/*
输出 sync_hotfix_to_develop 提示
```

失败场景覆盖分支落后/分叉、验证失败、确认拒绝、PR 创建失败和等待超时。

- [ ] **Step 2: 运行测试并确认 RED**

Run: `bash scripts/test-release-workflow.sh`

Expected: FAIL，因为 Hotfix `publish` 尚未注册。

- [ ] **Step 3: 实现 publish 和 Hotfix 审计**

审计路径固定为：

```text
.local/release-runs/hotfix-<jira-id>-<head>.json
```

成功输出 `operation=hotfix_publish`、Jira ID、PR URL、merge commit、版本基线和 `agentic_next_action=sync_hotfix_to_develop`。

- [ ] **Step 4: 验证 Hotfix publish GREEN**

Run:

```bash
bash scripts/test-release-workflow.sh
bash -n scripts/hotfix.sh scripts/lib/release-common.sh
```

Expected: PASS，且 fake push 日志中没有 tag。

- [ ] **Step 5: 提交 Hotfix publish**

```bash
git add scripts/hotfix.sh scripts/lib/release-common.sh scripts/test-release-workflow.sh
git commit -m "Feat(hotfix): TAP-12371 实现紧急修复发布流程" -m "复用完整验证和人工确认门禁，将 fix-main 分支通过 Merge Auto-merge 合入 main，并在完成后明确提示人工同步 develop。"
```

## Task 7: 删除研发期限制并迁入永久规则

**Files:**
- Delete: `docs/development-phase-rules.md`
- Modify: `AGENTS.md`
- Modify: `README.md`
- Modify: `docs/README.md`
- Modify: `docs/project-rules.md`
- Modify: `docs/ai-working-rules.md`
- Modify: `docs/development-style.md`
- Modify: `docs/maintainers/getting-started.md`
- Modify: `docs/review-checklist.md`
- Modify: `docs/architecture/agenticops-current-design.md`
- Modify: `docs/architecture/project-structure.md`
- Modify: `docs/runtime/versioning.md`
- Modify: `docs/user-stories/project-maintainer/pm-003-release-assets.md`
- Modify: `docs/user-stories/project-maintainer/pm-006-release-governance.md`
- Modify: `plans/design-implementation-gap-todo-v1.md`
- Modify: `scripts/test-resources.sh`

**Interfaces:**
- Consumes: `docs/architecture/source-release-workflow-design.md`。
- Produces: 不依赖研发期文件的永久规则、维护者入口和发布故事。

- [ ] **Step 1: 写资源一致性失败检查**

在 `scripts/test-resources.sh` 增加：

```bash
test ! -e docs/development-phase-rules.md
test -x .githooks/pre-commit
test -x .githooks/pre-push
test -x scripts/release.sh
test -x scripts/hotfix.sh
test -x scripts/test-release-workflow.sh
```

- [ ] **Step 2: 运行资源测试并确认 RED**

Run: `bash scripts/test-resources.sh`

Expected: FAIL，因为研发期文件仍存在。

- [ ] **Step 3: 迁移永久规则并删除研发期文件**

按设计文档第 11 节迁移。必须保留：

```text
go test ./...
bash scripts/test-resources.sh
bash scripts/test-build.sh
bash scripts/test-install.sh
全部 tests/e2e/*.sh
```

必须删除研发期一律禁止真实 Jira/GitHub 写入、Git push、PR、merge 和发布的表述，改为永久策略门禁与明确人工确认。

- [ ] **Step 4: 校正文档链接和当前计划**

Run:

```bash
rg -n 'development-phase-rules\.md|第一个版本发布正式上线前|当前阶段只允许本地模拟' \
  AGENTS.md README.md docs/README.md docs/project-rules.md docs/ai-working-rules.md \
  docs/development-style.md docs/maintainers/getting-started.md docs/review-checklist.md
```

Expected: 不再出现研发期文件引用；GitHub Release 只在明确区分“不是本次源码发布”或历史纠正时出现。

- [ ] **Step 5: 验证文档和资源 GREEN**

Run:

```bash
bash scripts/test-resources.sh
git diff --check
```

Expected: PASS。

- [ ] **Step 6: 提交正式规则迁移**

```bash
git add AGENTS.md README.md docs/README.md docs/project-rules.md docs/ai-working-rules.md \
  docs/development-style.md docs/maintainers/getting-started.md docs/review-checklist.md \
  docs/architecture/agenticops-current-design.md docs/architecture/project-structure.md \
  docs/runtime/versioning.md docs/user-stories/project-maintainer/pm-003-release-assets.md \
  docs/user-stories/project-maintainer/pm-006-release-governance.md \
  plans/design-implementation-gap-todo-v1.md scripts/test-resources.sh
git add -u docs/development-phase-rules.md
git commit -m "Docs(workflow): TAP-12371 启用正式源码发布规则" -m "删除首版上线前临时限制，将 develop、main、Hotfix、完整验证、人工确认、Tag 和发布审计迁入永久项目规则与维护者故事。"
```

## Task 8: 完整回归、计划收口和长期记忆

**Files:**
- Modify: `plans/source-release-workflow-implementation-plan.md`
- Modify outside repo after conflict check: `/Users/lhs/wiki/30-projects/agentic-ops.md`
- Modify outside repo when threshold applies: `/Users/lhs/wiki/00-inbox/wiki-optimization-observations.md`

**Interfaces:**
- Produces: 全部计划勾选、验证证据、长期分支与发布决策记忆。

- [ ] **Step 1: 运行发布脚本合同测试和 shell 语法检查**

Run:

```bash
bash scripts/test-release-workflow.sh
bash -n .githooks/pre-commit .githooks/pre-push scripts/release.sh scripts/hotfix.sh scripts/lib/release-common.sh scripts/lib/development-workflow.sh
```

Expected: PASS。

- [ ] **Step 2: 运行完整 Go、资源、构建、安装和 E2E 回归**

Run:

```bash
go test ./...
bash scripts/test-resources.sh
bash scripts/test-build.sh
bash scripts/test-install.sh
bash tests/e2e/ao-profile-flow.sh
bash tests/e2e/local-fake-flow.sh
bash tests/e2e/local-install-flow.sh
bash tests/e2e/problem-resolution-flow.sh
```

Expected: 全部退出 0，输出各自 `"ok":true` 结果。

- [ ] **Step 3: 验证需求和仓库状态**

Run:

```bash
git diff --check
rg -n 'development-phase-rules\.md|第一个版本发布正式上线前|当前阶段只允许本地模拟' \
  AGENTS.md README.md docs/README.md docs/project-rules.md docs/ai-working-rules.md \
  docs/development-style.md docs/maintainers/getting-started.md docs/review-checklist.md
git status --short
```

Expected: diff check 退出 0；`rg` 无当前规则命中；状态只包含已知、待提交的计划勾选或既有范围改动。

- [ ] **Step 4: 更新计划勾选和长期记忆**

把本计划已完成步骤改为 `[x]`。在写入 wiki 前检查现有 AgenticOps 项目页、分支规则和冲突处理规则；用新决策替换“研发期 main 直提”记忆，并记录读取链路超过 5 个文件的优化观察，不保存凭证或原始日志。

- [ ] **Step 5: 提交计划收口**

```bash
git add plans/source-release-workflow-implementation-plan.md
git commit -m "Docs(plan): TAP-12371 完成源码发布流程实施计划" -m "记录正式分支、正常发布、Hotfix、规则迁移和完整回归的实施结果与验证入口。"
```

- [ ] **Step 6: 推送前最终核对**

Run:

```bash
git log --oneline --decorate origin/main..develop
git status --short --branch
```

Expected: 只包含已审阅的聚焦提交；工作区没有遗漏的未知改动。根据用户已给出的推送授权推送 `develop`，不直接推送 `main`。
