# 操作契约

## 1. 目的

操作契约是 AgenticOps 的操作契约层，用于屏蔽 Jira / GitHub / Git 的底层事实差异，向 AIAgent 暴露稳定、统一、可验证的任务操作输入输出规范。

AIAgent 面向操作工作，不直接面对 Jira 字段、Jira 状态、Jira `transition` 或 Jira `comment` 模板。

操作契约还必须说明每次操作如何读取或更新 Task Form Standard 中的标准字段。AIAgent 后续判断 `current_stage`、`agentic_next_action`、重试、重做和人工审查时，应以操作输出、表单数据和事件记录为准，而不是以聊天上下文为准。

操作契约必须引用 Standard Process Registry 中的任务分类和流程阶段。AIAgent 执行任务前必须先得到 `task_class` 和 `process_id`，再进入对应流程阶段。

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
| `install` | 安装 AgenticOps 到 `~/.agentic-ops`。 |
| `doctor` | 输出安装、版本、工作流配置、策略、契约、适配器和工作空间的本地诊断结果。 |
| `assets_install` | 校验源资产 manifest 与当前 CLI 的 `exact_pair` 后安装版本化运行资产。 |
| `update_check` | 基于本地或远程 release manifest 判断 CLI / 资产兼容状态，返回更新级别和受影响操作。 |
| `update_apply` | 校验 manifest 与 checksum，版本化暂存产物，保存上一状态并原子切换激活二进制。 |
| `update_rollback` | 只使用本地上一状态和 checksum 恢复 CLI、资产指针与 current metadata。 |
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
| `takeover_task` | 接管一个新的 Jira 卡片。 |
| `resume_takeover` | 恢复已有 `agentic_run_id` 的接管任务。 |
| `read_task_context` | 读取任务上下文摘要。 |
| `write_evidence` | 写入任务阶段证据、阻塞说明和完成审计主体。 |
| `write_pr_evidence` | 读取 GitHub PR、CI 和 Review 事实，并写入任务关联的拉取请求证据。 |
| `release_agent` | 完成或明确交接后释放当前 AIAgent 绑定，并记录 `agentic_id_cleared=true`。 |
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

当前 `install-resources/basic/contracts/operations/` 维护已落地或直接需要的机器可读 YAML，并通过 `agentic-cli contract validate` 校验。未进入当前可运行闭环的操作先保留在本文档中作为后续契约范围，不视为已实现 CLI 命令。

## 4. 契约结构

每个 操作契约至少包含：

```yaml
operation: takeover_task
version: 1
purpose: 研发工程师授权 AIAgent 接管一个已进入迭代的任务。

task_type: task_takeover

allowed_stages:
  - waiting_takeover
  - takeover_gate

input:
  issue_key:
    type: string
    required: true
  workspace:
    type: string
    required: true

preconditions:
  - current_user_must_match_owner
  - agentic_id_must_be_empty_or_match_agent_id
  - task_class_must_be_mapped_to_standard_process
  - issue_must_be_in_allowed_project
  - jira_status_must_map_to_entry_stage

output:
  agentic_run_id:
    type: string
  current_stage:
    enum:
      - takeover_started
      - blocked
      - waiting_owner_confirmation
  target_repo:
    type: string
  agentic_next_action:
    enum:
      - proceed
      - ask_owner
      - blocked
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
      - assignee_mismatch
      - agent_ownership_conflict
      - task_class_mapping_gap
      - standard_process_mapping_gap
      - unknown_jira_status
      - invalid_takeover_stage
      - missing_permission
      - workflow_transition_not_allowed
  message:
    type: string
  required_human_action:
    type: string

side_effects:
  - may_write_jira_ownership
  - may_create_takeover_record
  - must_not_modify_code
  - must_not_create_pr

human_gate:
  required: false
```

## 5. 错误模型

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
- `agentic_next_action`
- `retryable`
- `redo_from_stage`

## 6. 副作用规则

操作必须明确副作用：

- 是否写 Jira。
- 是否写拉取请求。
- 是否写本地事件日志。
- 事件日志必须能记录 `agentic_cli_version`、`version_state`、`asset_version`、`code`、`gate` 和 `gate_status`。
- 事件日志必须能记录 `agent_id`、`agentic_id`、`task_class`、`process_id` 和 `agentic_id_cleared`。
- 写入 Jira 的标题、描述、评论、工作日志、证据正文、阻塞说明和补卡说明必须使用中文。
- 是否修改代码。
- 是否创建 `commit`。
- 是否推送。
- 是否创建拉取请求。

任何涉及 `git commit`、`git push`、`gh pr create`、`gh pr edit`、合并或发布的操作必须要求人工确认。

## 7. 工作项级连续执行授权

研发工程师确认版本化设计或修复计划时，可以同时授予工作项级连续执行授权。该授权绑定 `issue_key`、`agentic_run_id`、`agent_id`、`agentic_id`、目标仓库、工作分支、目标分支、计划版本、修改范围和验证方式，并通过 Jira 决策评论或项目配置的等价任务事实源提供稳定引用。

操作消费该授权时必须遵守：

- 高风险操作仍保留 `human_gate.required: true` 或等价策略 gate；授权复用表示人工确认已经存在，不表示取消门禁。
- `git_commit`、`git_push`、`write_jira_comment`、`create_pr` 和 `update_pr` 可以消费同一份仍有效的 `task_execution` 授权，不重复请求确认。
- 每次操作仍必须检查所有权、策略、输入、幂等条件和事实源结果；授权不能跳过这些前置门禁。
- 没有稳定授权引用的旧任务继续逐项确认，不得根据聊天摘要静默放宽。
- 所有权或绑定事实变化、范围或风险扩大、必要验证受阻、连续失败或外部写入结果不明确时，授权立即失效。
- 合并、发布、Git Tag、直接修改受保护分支、强推、历史改写和范围变化不能消费 `task_execution` 授权，必须取得新的人工确认。

`prepare_pr` 继续只生成结构化拉取请求计划和固定 Head SHA，不推送、不创建拉取请求。AIAgent 只有在回读有效授权后，才可以通过受控 Git 和 GitHub 工具执行授权覆盖的远端写入，并在拉取请求创建后统一停在审查节点。
