# 标准流程注册处

## 1. 目的

Standard Process Registry 是 AgenticOps 维护标准流程的源头。它定义任务分类、标准流程、流程阶段、阶段处理标准、责任角色、表单输入输出、所有权门禁、日志上报、重试重做和完成清理规则。

它不替代 Jira 工作流。Jira 仍是任务事实源；工作流配置负责把这里定义的标准流程映射到具体 Jira 项目的字段、状态和 `transition`。AIAgent 必须先按任务分类选择标准流程，再按对应阶段的处理标准执行，不允许直接根据 Jira 状态或聊天上下文自由发挥。

## 2. 文档边界

| 文档 | 职责 |
| --- | --- |
| Standard Process Registry | 定义任务分类、标准流程、阶段标准、责任角色、日志和完成清理规则。 |
| Task Form Standard | 定义标准字段、字段语义、生命周期要求和字段缺口处理。 |
| 工作流配置 | 把标准流程、标准字段和阶段映射到具体 Jira 字段、状态、`transition` 和审查节点。 |
| 操作契约 | 定义 `ao-work` 业务操作如何校验、执行、输出和记录事件。 |
| 反馈闭环 | 聚合执行日志、失败码、审查退回、重试重做和流程改进建议。 |

机器可读源头放入 `developer/standards/contracts/processes/`。当前文档用于解释 developer 标准流程注册处的职责边界；maintainer 流程不在此混写。

## 3. 流程选择顺序

所有任务先执行公共接管操作，再完成分类并进入对应具体流程。接管只建立负责人、团队状态和可审计运行轨迹，不替代任务分类。

```text
Jira 卡片
-> 校验项目、负责人、状态映射和 Agent 身份
-> 执行统一接管并回读 Comment/Status
-> 读取标准字段、项目资产和源码事实
-> 判断 task_class
-> 选择 standard_process
-> 校验 Jira 表单、状态和 transition 是否能映射
-> 执行接管门禁
-> 按阶段标准执行
-> 输出阶段表单、事件日志和证据
-> 等待对应专业角色审查
-> 根据表单、审查结论、失败码和门禁决定继续、重试、重做或停止
```

如果无法判断任务分类，AIAgent 必须停止接管并输出 `task_classification_required`，请求研发工程师或流程负责人补充分类依据。

## 4. 初始任务分类

第一阶段先定义研发 Jira 任务的最小分类。分类名称是 AgenticOps 标准，不直接绑定 Jira 卡片类型。

| task_class | 适用范围 | 默认流程 |
| --- | --- | --- |
| `feature_change` | 需求、功能增强、用户故事。 | `development_change_v1` |
| `bug_fix` | 缺陷修复、线上问题修复、回归问题。 | `development_change_v1` |
| `technical_task` | 重构、工程化、依赖升级、脚本或配置改造。 | `development_change_v1` |
| `investigation` | 排查、分析、复现、技术调研。 | `investigation_v1` |
| `process_improvement` | AgenticOps 工作流配置、策略、模板、运行手册或操作改进。 | `agenticops_improvement_v1` |

工作流配置可以把 Jira 卡片类型、标签、组件、自定义字段或描述模板映射为 `task_class`。映射缺失时必须返回 `task_class_mapping_gap`。

## 5. 标准流程结构

每个标准流程必须描述：

- `process_id`：稳定流程编号。
- `task_classes`：适用任务分类。
- `entry_stage`：入口阶段。
- `stages`：阶段列表。
- `stage_standard`：每个阶段的处理标准。
- `required_forms`：阶段输入和输出表单字段。
- `responsible_role`：对阶段结果负责的人或角色。
- `review_gate`：是否需要专业审查，以及审查结论字段。
- `retry_redo`：失败后允许重试还是必须重做。
- `stop_conditions`：必须停止的风险、权限或所有权条件。
- `completion_cleanup`：完成或交接后的清理动作。

示例结构：

```yaml
process_id: development_change_v1
task_classes:
  - feature_change
  - bug_fix
  - technical_task
entry_stage: waiting_takeover
stages:
  - id: waiting_takeover
    responsible_role: development_engineer
    input_fields:
      - assignee
      - jira_status
      - workspace_agent_identity
      - explicit_takeover_instruction
    output_fields:
      - agentic_run_id
      - agentic_takeover_at
      - takeover_kind
      - takeover_status
      - human_notice
      - takeover_comment_id
      - takeover_phase
      - takeover_result
      - external_result_certainty
      - recovery_action
      - current_stage
      - next_step
    review_gate: null
  - id: task_intake
    responsible_role: ai_agent
    output_fields:
      - task_class
      - process_id
      - target_repo
      - verification_method
    review_gate: null
  - id: design_review
    responsible_role: development_engineer
    output_fields:
      - design_review_decision
      - execution_authorization
    review_gate: development_engineer_design_review
  - id: implementation
    responsible_role: ai_agent
    output_fields:
      - implementation_summary
      - verification_result
      - residual_risk
    review_gate: null
  - id: completed
    responsible_role: development_engineer
    completion_cleanup:
      write_terminal_comment: true
      close_local_run: true
```

## 6. Jira 适配缺口处理

如果 Jira 没有对应表单、字段、状态或 transition，AIAgent 不允许猜测映射，也不允许绕过流程。

| Gap | 稳定错误码 | 处理方式 |
| --- | --- | --- |
| Jira 无标准字段对应字段或模板 | `missing_form_field` | 停止当前操作，提示新增 Jira 字段、使用描述模板映射，或调整 Task Form Standard。 |
| Jira 字段存在但没有配置映射 | `unmapped_jira_field` | 请求维护工作流配置的 Jira 表单映射。 |
| Jira 状态无法映射到标准阶段 | `lifecycle_mapping_gap` | 请求流程负责人决策状态映射或调整 Jira 工作流。 |
| Jira `transition` 无法映射到标准推进动作 | `transition_mapping_gap` / `jira_transition_mapping_gap` | 请求流程负责人决策标准 `transition` 映射、Jira `transition` 标识映射或新增人工门禁。 |
| Jira 卡片类型或标签无法映射任务分类 | `task_class_mapping_gap` | 请求研发工程师或流程负责人补充任务分类规则。 |
| 审查节点无法映射到人、拉取请求审查、CI 或 Jira 状态 | `review_gate_mapping_gap` | 请求流程负责人决策审查节点和责任角色。 |

缺口输出必须包含：

- `issue_key`
- `workspace`
- `task_class`
- `process_id`
- `current_stage`
- 缺失的标准字段、状态或 `transition`。
- 当前工作流配置中已配置的映射摘要。
- 给开发者或流程负责人的中文决策选项。

## 7. developer 接管门禁

`agent_id` 是识别 AIAgent 的稳定身份编号，`agentic_run_id` 是一次执行记录。developer 不使用 Jira Agentic Custom Field 表达任务锁；Jira `Assignee` 表达负责人，Comment 表达接管与执行轨迹，本地 task state 表达当前运行和恢复状态。

初始接管前必须满足：

- Jira `assignee` 必须等于当前登录用户。
- 当前 Jira 状态和必要 transition 必须能严格映射。
- 当前工作空间 Agent 身份、本地 run 和已有受管 Comment 不得存在已知冲突。
- 研发工程师已经明确表达“接管 <KEY>”。

接管成功后必须写入：

- `agentic_run_id`
- `agent_id`
- `agentic_takeover_at`
- `takeover_kind`
- `takeover_status`
- `human_notice`
- `takeover_comment_id`
- `takeover_phase`
- `takeover_result`
- `external_result_certainty`
- `recovery_action`
- `current_stage`
- `next_step`

接管后连续执行信息分析，补齐 `task_class`、`process_id`、目标仓库、分支和验证方式；这些事实缺失时阻止实现，不阻止建立接管轨迹。普通分析和方案分级不设置独立确认，正常进入设计审查；所有权或风险冲突进入风险决策。

接管评论必须明文区分新接管、接纳存量任务和恢复运行；后两种同时在 `human_notice` 和 Comment 中提示“不是新接管”。如果 Jira `assignee` 不等于当前登录用户，返回 `owner_mismatch`。并发重复接管不是当前阶段的锁能力，出现真实需求后单独设计。

本地接管状态必须把业务阶段与外部写入阶段分离。Comment 或 Status 部分完成、外部结果不确定以及本地最终落盘失败只更新 `sync.json.takeover_operation` 和接管事件，不得用新的业务 stage 表达，也不得返回接管成功。

## 8. 执行过程所有权检查

每个会读取任务、修改代码、写证据、推进状态或请求人工门禁的操作前，都必须重新检查任务所有权。

必须停止的情况：

| 条件 | 代码 | 记录 |
| --- | --- | --- |
| Jira `assignee` 已不是当前登录用户 | `assignee_changed` | 记录当前 `assignee`、当前登录用户、操作和停止阶段。 |
| Jira 状态、受管评论与本地运行事实冲突 | `external_task_state_conflict` | 记录 `agentic_run_id`、`issue_key`、当前外部事实和需要人工判断的恢复动作。 |

这些情况不允许自动覆盖外部事实。AIAgent 必须记录事件，输出中文阻塞说明，并等待研发工程师决策。

## 9. 完成收口规则

任务完成或明确交接结束后，必须写入并回读 Jira 中文终止评论，关闭本地运行并保留审计记录。

允许清理的完成条件：

- 标准流程进入 `completed`、`handed_off` 或工作流配置明确声明的终态。
- 完成阶段所需表单已经写入，例如 `agentic_completion_evidence`、`follow_up_items`、`reviewer_decision`。
- 需要的专业审查已经完成，且责任人确认当前结果可交付。
- 收口操作仍通过 `assignee`、运行 ID 和本地状态检查。

收口动作必须记录：

- 执行清理的 `agent_id`。
- `completed_at` 或 `handoff_at`。
- 完成阶段和完成证据引用。
- `terminal_comment_id` 与回读结果。
- 本地运行终态。

异常停止、阻塞、权限冲突或 `assignee` 变更时不得虚构正常完成；这些场景保留本地运行和 Jira 评论轨迹，由研发工程师决定后续动作。

## 10. 阶段处理标准和责任

不同阶段必须使用对应阶段的处理标准。AIAgent 可以执行分析、实现、验证和证据整理，但对阶段结果好坏对错负责的人必须明确。

| 阶段 | AIAgent 工作 | 责任角色 | 合格判断 |
| --- | --- | --- | --- |
| 分类 | 读取标准字段，识别 `task_class`。 | 研发工程师、流程负责人 | 分类能选择正确流程，缺口已阻断并请求决策。 |
| 接管 | 校验负责人、状态和流程入口，写入并回读接管评论。 | 研发工程师 | 任务属于当前登录用户，接管类型和运行标识已明文留痕。 |
| 分析 | 理解范围、风险、依赖和验证方式。 | 研发工程师 | 范围未扩大，风险和阻塞被说明。 |
| 实现 | 修改代码或配置，遵守项目规范。 | AIAgent 执行，研发工程师负责最终判断 | 代码差异解决目标问题，未引入无关变更。 |
| 验证 | 运行约定验证并记录结果。 | AIAgent 执行，研发工程师或 QA 判断 | 验证覆盖验收标准，未验证部分明确。 |
| 审查 | 整理证据、拉取请求信息和待审内容。 | 代码审查人、QA、运维、安全、研发工程师 | 专业角色能判断通过、退回、阻断或要求补充。 |
| 完成 | 写入完成证据与终止评论，关闭本地运行。 | 研发工程师 | 完成条件满足，Jira 与本地终态均可回读。 |

## 11. 日志上报要求

执行任务需要详细记录日志，并由每个 AIAgent 上报结构化事件。事件至少包含：

- `event_id`
- `timestamp`
- `workspace`
- `agent_id`
- `agentic_run_id`
- `issue_key`
- `assignee`
- `takeover_comment_id`
- `operation`
- `task_class`
- `process_id`
- `current_stage`
- `next_step`
- `status`
- `code`
- `message`
- `retryable`
- `redo_from_stage`
- `artifact_refs`
- `terminal_comment_id`

日志写入项目 AI 工作空间，不写入 `~/.agentic-ops`。日志只能保存安全摘要，不得记录 secrets、tokens、private keys、原始敏感日志、完整 Jira 描述或敏感代码片段。

每个阶段完成后，AIAgent 还必须写入面向人的中文证据或工作日志摘要，说明已完成事项、更新表单、验证结果、审查状态和下一步动作。

## 12. 复盘演进

周期性复盘必须聚合：

- 任务分类缺口。
- Jira 字段、状态和 transition 映射缺口。
- Jira 外部事实与本地运行冲突。
- 阶段标准不清导致的人工退回。
- 重试次数、重做来源阶段和连续失败。
- 完成后缺少终止评论或本地运行未收口的任务。
- 可沉淀为运行手册、工作流配置、策略、模板或操作的成熟经验。

AIAgent 可以提出改进建议，但不能未经人工确认自动修改标准流程、工作流配置、Task Form Standard、操作契约或 Jira 工作流。
