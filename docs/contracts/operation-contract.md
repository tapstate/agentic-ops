# 操作契约

## 1. 目的

操作契约是 AgenticOps 的操作契约层，用于屏蔽 Jira / GitHub / Git 的底层事实差异，向 AIAgent 暴露稳定、统一、可验证的任务操作输入输出规范。

AIAgent 面向操作工作，不直接面对 Jira 字段、Jira 状态、Jira `transition` 或 Jira `comment` 模板。

操作契约还必须说明每次操作如何读取或更新 Task Form Standard 中的标准字段。AIAgent 后续判断 `current_stage`、`next_step`、重试、重做和人工审查时，应以操作输出、表单数据和事件记录为准，而不是以聊天上下文为准。

操作契约必须引用 Standard Process Registry 中的任务分类和流程阶段。统一接管操作是具体流程选择前的公共操作，只验证项目、负责人、状态映射、Agent 身份和恢复事实；接管后必须补齐 `task_class` 和 `process_id`，未补齐前不得进入实现。

## 2. 契约原则

- 每个操作必须有稳定输入、输出、失败码和副作用说明。
- 写操作必须声明副作用。
- 需要人工确认的操作必须声明 `human_gate`。
- CLI stdout 必须输出结构化 JSON。
- stderr 只输出人类诊断日志。
- secrets 不得出现在 stdout、stderr 或事件日志中。
- 写入 Jira 的人可见内容必须使用中文，包括标题、描述、评论、工作日志、证据正文、阻塞说明和补卡说明。
- 每个操作必须声明读取或写入哪些标准表单字段。
- 每个任务执行操作必须声明适用的 `task_class`、`process_id` 或阶段范围。
- 每个操作必须声明失败后是否允许重试，或是否要求从某个阶段重做。
- 每个操作都应是成熟固化交互逻辑的原子化入口，不承载尚未稳定的临场流程判断。

## 3. 第一阶段操作

| `Operation` | 用途 |
| --- | --- |
| `doctor` | 输出安装、版本、工作流配置、策略、契约、适配器和工作空间的本地诊断结果。 |
| `contract_validate` | 校验机器可读 操作契约是否满足完整设计基线。 |
| `profile_validate` | 校验工作流配置是否能映射标准字段、任务分类、标准流程、状态和 `transition`。 |
| `profile_update` | 使用经过校验的本地来源工作流配置更新当前工作流配置，并保存可回滚备份。 |
| `profile_rollback` | 从最近一次工作流配置更新备份恢复当前工作流配置。 |
| `policy_validate` | 校验当前策略是否包含关键步骤门禁配置。 |
| `policy_update` | 使用经过校验的本地来源策略更新默认策略，并保存可回滚备份。 |
| `policy_rollback` | 从最近一次策略更新备份恢复默认策略。 |
| `workspace_init` | 在项目 AI 工作空间目录内初始化运行配置，并绑定 Jira 用户、Jira 空间和代码仓库映射。 |
| `agent_init` | 初始化 AIAgent 能力。 |
| `list_tasks` | 列出当前负责人可处理任务。 |
| `inspect_task` | 只读输出 Jira 事实、通用门禁事实和项目资产引用。 |
| `add_task_comment` | 向 Jira 追加分析、计划、决策、证据或阻塞评论。 |
| `update_task_description_sections` | 安全更新 Jira Description 的指定章节并保留其它内容。 |
| `update_task_form` | 按项目 profile 的逻辑字段映射更新 Jira 表单。 |
| `takeover_task` | 自动判断新接管、接纳存量或恢复，并写入可见接管轨迹。 |
| `resume_takeover` | 只读诊断已有 `agentic_run_id`；正式恢复留痕回到统一接管操作。 |
| `read_task_context` | 读取任务上下文摘要。 |
| `write_evidence` | 写入任务阶段证据、阻塞说明和完成审计主体。 |
| `write_pr_evidence` | 读取 GitHub PR、CI 和 Review 事实，并写入任务关联的拉取请求证据。 |
| `release_agent` | 完成或明确交接后写入终态 Comment 并关闭本地任务运行；developer 不清理 Agentic Jira 字段。 |
| `mark_blocked` | 记录阻塞原因和人工动作。 |
| `request_owner_confirmation` | 请求研发工程师确认。 |
| `branch_align` | 按 TapData 项目级分支规范计算或执行多仓分支对齐。 |
| `prepare_pr` | 准备拉取请求，不绕过人工确认。 |
| `fix_pr_comments` | 按拉取请求审查意见修复。 |
| `feedback_collect` | 收集工作空间事件日志。 |
| `feedback_bundle` | 为指定 `agentic_run_id` 生成脱敏诊断包。 |
| `feedback_analyze` | 分析执行失败、阻塞和重复问题。 |
| `feedback_report` | 按需生成执行分析报告，用于发现重复问题和 AgenticOps 改进建议。 |
| `feedback_propose` | 生成改进建议。 |

developer 工作面的机器可读操作契约位于 `developer/standards/contracts/operations/`。契约保存目标行为边界，不是实现状态事实源；`contract_validate` 本身在当前 Python Runtime 中也是 `capability_gap`，不能因为契约文件存在就声称有对应命令。

当前可调用性以 `developer/standards/capabilities/operations.yaml` 和 `ao-work capability list|show` 为准。能力目录必须覆盖每个契约恰好一次，状态只能是 `implemented` 或 `capability_gap`；只有 `implemented` 且目录声明真实 parser 路径时才可以调用。旧 Go 契约未迁移时继续保留为验收目标，但必须标记能力缺口并给出中文人工动作。

AgenticOps 安装、更新和回滚属于 `developer/bootstrap/` 的 Shell Bootstrap 责任：它们只管理 developer-only sparse managed clone、Git ref、`uv sync --locked`、`ao-work` 入口和本地回滚引用，不是 Jira / GitHub / Git 业务操作契约，也不使用旧二进制 manifest 或 checksum 流程。

## 4. 契约结构

每个 操作契约至少包含：

```yaml
operation: takeover_task
version: 2
purpose: 研发工程师明确要求接管后，由 Runtime 自动完成新接管、接纳存量或恢复。

task_type: task_takeover

allowed_stages:
  - waiting_takeover
  - takeover_started
  - blocked

input:
  issue_key:
    type: string
    required: false
  workspace:
    type: string
    required: true

preconditions:
  - issue_must_be_in_allowed_project
  - current_user_must_match_assignee
  - jira_status_and_transition_must_be_strictly_mapped
  - workspace_agent_identity_must_be_available
  - local_run_and_managed_comment_must_not_conflict

output:
  agentic_run_id:
    type: string
  takeover_status:
    enum:
      - completed
  takeover_kind:
    enum:
      - new_takeover
      - accept_existing_task
      - resume_takeover
  takeover_comment_id:
    type: string
  human_notice:
    type: string
  current_stage:
    enum:
      - takeover_started
  next_step:
    type: object
  retry_policy:
    type: object
    required: false
  redo_from_stage:
    type: string
    required: false

failure:
  code:
    enum:
      - owner_mismatch
      - assignee_changed
      - external_task_state_conflict
      - jira_takeover_comment_readback_mismatch
      - jira_status_mapping_missing
      - jira_transition_mapping_gap
      - missing_permission
      - workflow_transition_not_allowed
  message:
    type: string
  required_human_action:
    type: string

side_effects:
  - writes_managed_takeover_comment
  - may_transition_jira_status
  - may_create_takeover_record
  - must_not_modify_code
  - must_not_create_pr

human_gate:
  required: false
  authorization_basis: 用户明确表达“接管 <KEY>”即授权事实明确的常规接管；冲突和不确定结果进入风险决策。
```

## 5. StepResult v2 与 `next_step`

所有 CLI 结果先返回 `result`，再返回唯一的 `next_step`。`result` 使用 `status`、`summary`、`facts`、`evidence`、`effects` 和 `remaining` 说明已经发生的事实；它不是后续操作的授权来源。

`next_step` 是判别式对象，稳定字段为：

- `kind`：`action`、`decision`、`input`、`wait` 或 `none`。
- `scope`：`local` 仅用于使当前步骤结果可信的读回、幂等恢复和有界重试；`flow` 表示可信结果后的业务推进。边界不清时必须为 `flow`。
- `mode`：`auto`、`timed_auto` 或 `manual`。需要人工授权、高风险写入、范围变化或外部结果不确定时不得为 `auto`。
- `call`：固定操作、参数、工作目录、输入来源和事实绑定。AI 只能按此对象调用 Runtime，不能扩展为未声明的后续操作。

`decision` 必须带 `question` 与非空 `choices`；每个选项包含 `id`、`label`、`description`、`impact`、`risk`，且只有一个 `recommended=true`。提交选择只记录决策，不直接执行下游业务动作。

`timed_auto` 必须带受 Runtime 持久化并原子解析的 `timed`：`deadline`、`default_choice`、`cancel_if`、`fact_bind` 和 `policy`。UI 只能展示、提交取消或读取最终状态，不能以本地计时推断用户已经确认。

顺序执行闭环只能暴露当前唯一 `next_step`。完整流程必须通过独立只读 `WorkflowQuery` 获取；查询结果不可执行，也不构成授权。

## 6. 错误模型

错误必须稳定、可聚合、可反馈分析。

建议错误字段：

```json
{
  "ok": false,
  "operation": "takeover_task",
  "code": "missing_target_repo",
  "message": "Jira 卡片缺少目标仓库信息",
  "required_human_action": "请补充 target_repo 或 工作空间代码仓库 映射"
}
```

错误码命名使用 lowercase snake_case。

失败输出必须包含：

- `ok=false`
- `operation`
- `code`
- `message`
- `required_human_action`
- `task_type`
- `current_stage`
- `next_step`
- `retryable`
- `redo_from_stage`

## 7. 副作用规则

操作必须明确副作用：

- 是否写 Jira。
- 是否写拉取请求。
- 是否写本地事件日志。
- 事件日志必须能记录 `agentic_cli_version`、`version_state`、`asset_version`、`code`、`gate` 和 `gate_status`。
- 事件日志必须能记录 `agent_id`、`agentic_run_id`、`takeover_kind`、`takeover_comment_id`、`task_class`、`process_id` 和终态收口结果。
- 写入 Jira 的标题、描述、评论、工作日志、证据正文、阻塞说明和补卡说明必须使用中文。
- 是否修改代码。
- 是否创建 `commit`。
- 是否推送。
- 是否创建拉取请求。

任何涉及向 `master`、`main`、`develop`、`release/*` 或其它保护分支推送、合并、发布、Git Tag、强推或历史改写的操作必须要求人工确认。工作项级连续执行授权可以覆盖任务分支的 `git commit`、推送和目标为 `develop` 的 `gh pr create` / `gh pr edit`，但完成后必须停在拉取请求审查节点。

## 8. 工作项级连续执行授权

研发工程师确认版本化设计或修复计划时，可以同时授予工作项级连续执行授权。该授权绑定 `issue_key`、`agentic_run_id`、`agent_id`、已回读的 `takeover_comment_id`、目标仓库、工作分支、目标分支、计划版本、修改范围和验证方式，并通过 Jira 决策评论或项目配置的等价任务事实源提供稳定引用。

操作消费该授权时必须遵守：

- 高风险操作仍保留 `human_gate.required: true` 或等价策略 gate；授权复用表示人工确认已经存在，不表示取消门禁。
- `git_commit`、`git_push`、`write_jira_comment`、`create_pr` 和 `update_pr` 可以消费同一份仍有效的 `task_execution` 授权，不重复请求确认。
- 每次操作仍必须检查所有权、策略、输入、幂等条件和事实源结果；授权不能跳过这些前置门禁。
- 没有稳定授权引用的旧任务继续逐项确认，不得根据聊天摘要静默放宽。
- 所有权或绑定事实变化、范围或风险扩大、必要验证受阻、连续失败或外部写入结果不明确时，授权立即失效。
- 合并、发布、Git Tag、直接修改受保护分支、强推、历史改写和范围变化不能消费 `task_execution` 授权，必须取得新的人工确认。

`prepare_pr` 继续只生成结构化拉取请求计划和固定 Head SHA，不推送、不创建拉取请求。AIAgent 只有在回读有效授权、确认任务分支不属于保护分支且 PR 目标为 `develop` 后，才可以通过受控 Git 和 GitHub 工具执行授权覆盖的远端写入，并在拉取请求创建后统一停在审查节点。
