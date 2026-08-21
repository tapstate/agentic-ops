# 项目故事质量门禁

## 1. 目的

AgenticOps 通过项目维护故事和研发工程师故事管理长期质量合同。Runtime 生成与 Git 内容绑定的内部 `impact_id`，但公司员工指导员审查的是可查阅的 commit 或 PR，以及逐项确认事项、变更点和风险。

候选影响预警、固定验收和人工代码审查是三个阶段：候选先完成验收，再形成代码事实，最后进入所属分支通道的人工审查。任务级连续授权不能替代故事修订或代码审查事实。

## 2. 事实源

- 人读故事：`docs/user-stories/project-maintainer/`、`docs/user-stories/development-engineer/`。
- 机器注册表：`maintainer/standards/stories/project-quality.yaml`。
- 分支策略：`maintainer/standards/git/story-review-policy.yaml`。
- 本地确认：`maintainer/.local/story-approvals/<impact_id>.json`。
- 本地验收：`maintainer/.local/story-evidence/<impact_id>.json`。
- commit 与分支：Git。
- PR、Head、Review 与合入：GitHub。
- 任务计划、进度和总结：Jira。

本地记录只用于恢复当前维护会话，不能替代 Git、GitHub 或 Jira 事实。

## 3. 标准流程

```text
worktree / staged impact
-> 输出候选预警和完整 review_report
-> confirmation_required=false
-> 整理精确候选并运行固定验收
-> pre-commit 只检查映射、安全和验收
-> 按版本化分支策略形成 commit 或 PR
-> 审查 commit SHA 或 PR URL + Head
-> Runtime 记录后置人工事实
-> pre-push / PR Ruleset 执行最终门禁
```

候选命令：

```sh
./maintainer/bin/ao-maint story impact --change-source worktree
./maintainer/bin/ao-maint story impact --change-source staged
./maintainer/bin/ao-maint story verify --change-source staged
```

### `commit_review`

`develop` 等允许直接推送、但不属于任务分支的分支先创建本地 commit，不推送。Agent 对待推送 range 运行 `story verify` 和 `story impact`，向用户展示完整 commit SHA、确认事项、变更点和风险。用户确认后，Agent 内部以 `user-confirmation:<AO-KEY>:commit:<commit-sha>` 调用既有 `story approve`；该引用是审计实现，不要求用户阅读或复制。

pre-push 重新计算待推送 range，只有 `impact_id`、报告摘要、固定验收和 commit SHA 全部匹配才允许推送。

### `pr_review`

版本化策略登记的功能、修复和 AO 任务分支在连续授权范围内完成 commit、range 验收、任务分支推送和 PR 创建。Agent 提供 PR 地址与当前 Head SHA，并停在 PR Review。

用户在 GitHub 完成审查后，Agent 调用既有 `story approve`。Runtime 回读仓库、PR number、base/head 分支、当前 Head 和独立 Review，并以 `github-pr-review:<AO-KEY>:<pr-number>:<head-sha>` 保存内部引用。Head 变化后旧 Review 和本地批准均失效。Agent 不自动批准或合并。

### `protected` 与 `special`

`main`、release、tag、合并、强推和历史改写不套用普通通道，继续使用发布、Hotfix 或其它独立人工门禁。分支规则缺失或歧义时失败关闭。

## 4. 结构化审查报告

`story impact` 稳定输出：

- `review_channel`、`confirmation_stage`、`approval_ready`、`confirmation_required`；
- 变更路径；
- 受影响故事的编号、中文标题和文档路径；
- 故事修订与未映射路径；
- 固定验收项；
- 当前分支、目标分支与审查对象；
- PR 地址与 Head，或本地 commit SHA；
- 逐项 `confirmation_items`、`change_points` 和 `risks`；
- 确认后允许的下一动作。

`impact_id` 保留在机器输出和本地文件中，用于严格失效判断；面向用户的确认主题和 `required_human_action` 不得要求确认、复制或复述它。

## 5. Git 与信任边界

- trusted launcher 位于 Git common directory，并从已接受 `HEAD` 加载版本化 pre-commit / pre-push。
- pre-commit 拒绝 `main` 直提、未映射路径、不安全本地状态、未暂存门禁差异和缺失固定验收，但允许在人工审查前形成 commit。
- pre-push 禁止 `main` 直推；`commit_review` 要求精确 commit 批准，`pr_review` 允许任务分支推送以形成 PR。
- `maintainer/.local/story-approvals` 与 `story-evidence` 是快照外输入，Hook 和 release 逐级拒绝符号链接、特殊文件和路径逃逸。
- 本地 Hook 是防误操作层；硬门禁依赖 `main` Ruleset 的独立批准、最后推送者不能自批、dismiss stale approvals 和 review thread resolution。
- release / hotfix publish 只接受刷新后的 `origin/main` Runtime 检查固定 candidate。信任根变化返回 `release_story_gate_trust_root_changed`，改走受保护 `main` 的独立人工审查 PR。

禁止 `--no-verify`、修改 Hook、删除策略或注册表、伪造本地记录、复用旧提交/旧 Review 或用任务级授权替代代码审查。

## 6. AO-43 一次性迁移

AO-43 的父提交仍运行旧版 pre-commit，它要求 staged 批准先于 commit。安装候选必须展示一次完整 staged 报告，由公司员工指导员确认报告中的资源、变更点和风险后，Agent 内部调用旧版 approve / verify，再通过正常 Hook 提交。

这是旧基线约束，不代表新交互已生效；用户不确认裸 `impact_id`，流程不使用 `--no-verify`。AO-43 提交进入 `HEAD` 并重装 trusted Hook 后，该例外永久失效。

## 7. 稳定失败码

- `maintenance_story_mapping_missing`：治理路径缺少故事映射。
- `maintenance_story_acceptance_failed`：当前内容的固定验收未运行或失败。
- `story_review_policy_unavailable`：分支策略、Git 状态或 GitHub 回读不可用。
- `story_commit_review_required`：commit 通道推送前缺少当前提交批准。
- `story_review_channel_protected`：普通流程误入保护或专用通道。
- `story_authorization_reference_invalid`：内部审查引用未绑定当前 commit 或 PR Head。
- `story_impact_changed`：执行批准时 Git 内容已变化。
- `story_gate_local_state_unsafe`、`release_story_gate_local_state_unsafe`：本地故事状态路径不安全。
- `release_story_gate_baseline_upgrade_required`：`origin/main` 尚无可独立执行的门禁基线。
- `release_story_gate_trust_root_changed`：候选修改信任根，禁止自动 publish。
