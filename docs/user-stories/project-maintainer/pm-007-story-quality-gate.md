# PM-007 守护两类项目故事质量基线

作为公司员工指导员，
我希望项目维护故事和研发工程师故事成为可执行的质量合同，
以便候选先完成固定验收，再通过可查阅的 commit 或 PR 完成人工审查，而不是让我确认内部哈希。

### 触发方式

```sh
./maintainer/bin/ao-maint story impact --change-source worktree
./maintainer/bin/ao-maint story impact --change-source staged
./maintainer/bin/ao-maint story verify --change-source staged
```

不新增用户命令。`story approve` 由 Agent 在用户确认 commit 报告或 Runtime 回读有效 PR Review 后调用。

### 前置条件

- 当前工作位于 `tapstate/agentic-ops` 源头仓库或独立 worktree，并通过根 AI 入口进入 maintainer 工作面。
- 两类故事已在机器注册表中声明文档、保护路径、验收检查和证据要求。
- `maintainer/standards/git/story-review-policy.yaml` 能唯一确定当前分支的审查通道和目标分支。
- Git diff 可被 Runtime 确定性读取；PR 通道还要求 GitHub PR 和 Review 可被 Runtime 回读。

### 主流程

1. Runtime 读取故事注册表、版本化分支策略和 Git diff，生成内部 `impact_id`。
2. worktree / staged 命中保护路径时输出候选预警和结构化 `review_report`，`confirmation_required=false`；Agent 只收敛候选并运行固定验收，不请求人工确认。
3. 固定验收与候选 Git 内容指纹绑定，可在人工审查前执行；pre-commit 要求映射、安全快照和固定验收通过，但不要求提前批准。
4. `pr_review` 分支继续形成 commit、推送任务分支并创建或更新 PR；Runtime 输出 PR 地址和精确 Head SHA，用户在 PR 上逐项审查确认事项、变更点和风险。
5. `commit_review` 分支形成本地 commit 但保持未推送；Runtime 输出完整 commit SHA，用户在推送前逐项审查确认事项、变更点和风险。
6. PR 通道由 Runtime 回读当前 Head 的有效独立 GitHub Review；commit 通道由 Agent 根据当前对话确认构造内部 commit 审计引用。用户不查看、复制或复述 `impact_id`。
7. 批准记录同时绑定 `impact_id`、commit SHA 或 PR Head、报告摘要和确认事项；Git 内容、提交、PR Head 或报告变化后旧批准失效。
8. pre-push 对 `commit_review` 要求当前待推送 range 的固定验收和 commit 批准；对 `pr_review` 允许推送任务分支以形成 PR，但禁止直接推送目标保护分支。
9. `main`、release、tag、合并、强推和历史改写继续使用独立专用门禁。

AO-43 安装提交的旧 `HEAD` Hook 仍要求 staged 批准先于 commit。该笔变更按旧基线展示一次完整 staged 报告，由公司员工指导员确认报告资源、变更点和风险后，Agent 内部完成旧版 approve / verify，再使用正常 Hook 提交。该迁移不要求用户确认裸 `impact_id`，不使用 `--no-verify`，并在新 Hook 进入 `HEAD` 后永久失效。

### 输出

```json
{
  "ok": true,
  "operation": "story_impact",
  "review_channel": "commit_review",
  "confirmation_stage": "pre_push_commit_review",
  "approval_ready": true,
  "confirmation_required": true,
  "commit_sha": "<git-sha>",
  "review_report": {
    "changed_paths": ["maintainer/runtime/src/ao_maint/story_gate/service.py"],
    "impacted_stories": [
      {
        "story_id": "PM-007",
        "title": "守护两类项目故事质量基线",
        "document": "docs/user-stories/project-maintainer/pm-007-story-quality-gate.md"
      }
    ],
    "confirmation_items": [],
    "change_points": [],
    "risks": []
  },
  "required_human_action": "请审阅本地提交及逐项报告；确认前保持未推送"
}
```

`impact_id` 仍可出现在机器输出中，用于审计和失效判断，但不得出现在面向用户的确认主题或要求用户执行的动作中。

### 失败处理

- 治理路径缺少映射时返回 `maintenance_story_mapping_missing`，列出具体路径并失败关闭。
- 固定验收未运行或失败时返回 `maintenance_story_acceptance_failed`。
- 分支策略缺失、歧义或 GitHub 回读失败时返回 `story_review_policy_unavailable`。
- commit 通道推送前缺少当前提交批准时返回 `story_commit_review_required`。
- 保护或专用通道被普通流程调用时返回 `story_review_channel_protected`。
- 审查事实没有绑定当前 commit 或 PR Head 时返回 `story_authorization_reference_invalid`。
- Git 内容、commit SHA、PR Head 或报告摘要变化后旧确认自动失效。

### 验收标准

- 注册表只允许 `maintainer` 和 `developer` 两类故事，稳定编号仍使用 `PM-*` 与 `DE-*`。
- 每个故事都有唯一编号、中文标题、人读文档、保护路径、固定验收检查和证据要求。
- worktree / staged 影响输出 `confirmation_required=false`，人工动作不得要求确认或复制 `impact_id`。
- `review_report` 完整列出变更路径、故事标题与文档、故事修订、未映射路径、固定验收、分支通道、审查对象、确认事项、变更点、风险和确认后动作。
- `develop` 进入 `commit_review`；登记的功能、修复和 AO 任务分支进入 `pr_review`；`main` 进入 `protected`；release 分支进入 `special`；缺失或歧义规则失败关闭。
- pre-commit 在固定验收通过后允许形成 commit，不要求人工批准先于 commit。
- commit 通道形成提交后，pre-push 在批准前阻断，批准只绑定完整 commit SHA 和同一 range 报告。
- PR 通道必须输出 URL 与当前 Head；只有 Runtime 回读到当前 Head 的独立批准才可记录，新的 push 使旧 Review 和本地批准失效。
- 确认事项、变更点和风险必须逐项显示；无额外业务风险时仍明确保留本地 Hook 可绕过的残留风险。
- 未暂存篡改 Hook、launcher、Runtime、策略或固定验收入口时，pre-commit 在执行门禁前阻断。
- `maintainer/.local` 故事状态路径出现符号链接、特殊文件或路径逃逸时，Hook 与 release 失败关闭。
- Hook、故事门禁 Runtime、分支策略、注册表或发布脚本等信任根发生净变更时，自动 publish 以 `release_story_gate_trust_root_changed` 停止，改走受保护 `main` 的独立人工审查 PR。
- 执行四项固定完整验证，并覆盖候选预警、commit 审查、PR Review 回读、Head 失效和版本化资产措辞。

### 保护行为

- 项目只维护项目维护故事和研发工程师故事，不建立第三类 AIAgent 故事。
- 故事是仓库内版本化质量合同，Jira 只管理实施计划、进度、确认和验收记录。
- 原任务级连续授权不能替代故事修订审查，也不能替代 commit 或 PR 代码审查事实。
- 用户确认的是 PR 或本地 commit 及完整报告；`impact_id` 只用于 Runtime 内部绑定。
- AI、Skill、Shell、Git Hook 和发布脚本不得绕过 Python Runtime 的影响、验收、分支和审查事实结论。
- 验收检查必须来自 Runtime 固定白名单，注册表不得注入任意 Shell 命令。
- 版本化 `.githooks` 是 trusted launcher 加载的策略源，不能由 candidate 自证。
- 本地 Hook 是防误操作层；硬门禁依赖 `main` Ruleset 的独立批准、最后推送者不能自批、dismiss stale approvals 和 review thread resolution。
- Agent 不自动批准 PR、不自动合并，也不把“已创建 PR”描述为“已人工确认”。

### 审核问题

- 当前审查对象是哪个 PR Head 或本地 commit。
- 当前变更影响哪些故事，是否直接修订故事合同。
- 固定验收是否与当前代码事实绑定并全部通过。
- 确认事项、变更点和风险是否逐项完整。
- 是否存在未映射路径、分支歧义、PR Head 漂移或旧批准复用。

### 验收证据

- Runtime 单测证明 worktree / staged 不请求人工确认且报告字段完整。
- 分支策略测试证明 PR、commit、protected、special 和未知分支的确定性结果。
- pre-push 测试证明 develop 的未确认 commit 被阻断、精确确认后放行。
- PR 测试证明当前 Head Review 可记录、新 Head 使旧批准失效。
- `ao-maint story verify` 在批准前生成与内容指纹绑定的固定验收证据。
- 版本化资产测试证明 AGENTS、Rule、Skill 和 PM-007 不指示用户确认裸 `impact_id`。
- 发布 fixture 证明 candidate 信任根不能自证，受保护 `main` 仍要求独立人工审查。

### 关联设计

- `docs/strategy/project-goals.md`
- `maintainer/standards/stories/project-quality.yaml`
- `maintainer/standards/git/story-review-policy.yaml`
- `maintainer/rules/source-maintenance.md`
- `maintainer/skills/guard-story-quality/SKILL.md`
- `maintainer/runtime/src/ao_maint/story_gate/`
