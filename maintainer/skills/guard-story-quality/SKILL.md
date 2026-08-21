---
name: guard-story-quality
description: Detect and enforce AgenticOps project-maintenance and development-engineer story quality gates. Use before or after source-maintenance changes, before commit or push, or whenever code may alter protected behavior, acceptance conditions, story documents, tests, standards, skills, rules, Runtime, installation, or release flows.
metadata:
  workplane: maintainer
---

# 守护项目故事质量

只在 `maintainer` 工作面使用。先调用维护 Runtime，不自行判断故事影响、分支类型或审查通道。

## 候选预警与验收

修改前或整理候选时运行：

```sh
ao-maint story impact --change-source worktree
ao-maint story impact --change-source staged
```

worktree / staged 命中故事只表示候选预警。此时可以读取 diff、解释影响、收敛修改范围并运行报告列出的固定验收，但不得请求用户确认，更不得询问“是否确认 `<impact_id>`”。`impact_id` 是 Runtime 内部审计键。

精确 staged 候选完成后运行：

```sh
ao-maint story verify --change-source staged
```

固定验收允许且应当在人工审查前完成。pre-commit 校验故事映射、候选快照安全、固定验收证据和信任根，不要求人工批准先于 commit。

遇到以下情况停止并处理报告中的明确资源：

- `maintenance_story_mapping_missing`：列出未映射路径，请公司员工指导员处理故事合同。
- `maintenance_story_acceptance_failed`：修复失败验收，内容变化后重新生成报告和证据。
- `story_review_policy_unavailable`：修复版本化分支策略、Git 工作区或 GitHub 回读能力，不能猜测更宽松通道。
- `story_review_channel_protected`：使用发布、Hotfix 或其它专用流程。

## 按分支通道形成审查事实

Runtime 的 `review_channel` 是唯一判定结果。

### `pr_review`

在工作项连续授权范围内完成 commit、对目标分支 range 运行固定验收、推送任务分支并创建或更新 PR。然后重新运行 range impact，向用户提供：

- PR 地址；
- 当前精确 Head SHA；
- 逐项确认事项；
- 逐项变更点；
- 逐项风险和残留风险。

停在 PR Review，不在聊天中把“确认”冒充 GitHub Review，不自动批准或合并。用户在 PR 上完成审查后，Agent 调用既有 `story approve`，Runtime 必须回读 PR URL、base/head 分支、当前 Head SHA 和有效独立 Review。新 push 改变 Head 后旧 Review 立即失效，重新提供同一 PR 地址和新 Head 等待审查。

### `commit_review`

完成本地 commit，但保持未推送。对待推送 range 运行 `story verify` 和 `story impact`，向用户提供：

- 完整 commit SHA；
- 逐项确认事项；
- 逐项变更点；
- 逐项风险和残留风险。

用户确认当前报告后，Agent 内部构造与 commit SHA 绑定的审计引用并调用既有 `story approve`；用户不需要运行命令、复制引用或阅读 `impact_id`。pre-push 只允许推送与批准、range impact 和验收证据完全一致的提交；commit 或范围变化后必须重新审查。

## 保持信任链

Git common directory 中的 trusted launcher 从已接受 `HEAD` 加载版本化 Hook。pre-commit 在隔离 index 快照中检查映射、固定验收和候选安全；pre-push 按 `maintainer/standards/git/story-review-policy.yaml` 检查提交或 PR 通道。发现未暂存门禁差异、本地状态链接或特殊文件、分支歧义时立即停止。

本地 Hook 只提供防误操作和快速反馈。硬门禁还依赖无 bypass 的 `main` Ruleset 强制至少 1 个独立人工批准、最后推送者不能自批、dismiss stale approvals 和解决全部 review threads。发布只接受刷新后的 `origin/main` 基线；信任根变化改走受保护 `main` 的独立人工审查 PR。

禁止使用 `--no-verify`、修改 Hook、伪造确认文件、旧批准、任意非空字符串或任务级连续授权绕过代码审查。

## AO-43 一次性迁移

AO-43 安装提交的已接受 `HEAD` 仍执行旧版 pre-commit，要求人工批准先于 commit。该笔候选必须按旧基线展示一次完整 staged 报告，由公司员工指导员确认报告中的资源、变更点和风险后，Agent 内部完成旧版 approve / verify，再通过正常 Hook 创建安装提交；不得让用户确认裸 `impact_id`，也不得使用 `--no-verify`。

该兼容步骤只服务 AO-43 安装提交。新 trusted Hook 进入 `HEAD` 后，所有后续变更必须使用本 Skill 的 commit / PR 后置审查通道，不能再次套用迁移例外。
