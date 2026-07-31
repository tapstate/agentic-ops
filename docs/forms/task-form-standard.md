# AI 操作任务表单标准

## 1. 目的

任务表单标准（Task Form Standard）是 AgenticOps 维护 AI 操作任务属性和节点结果的标准层。它定义一张任务从创建、进入迭代、AI 接管、本地开发、拉取请求审查到完成过程中需要维护的标准字段、生命周期要求、校验规则和缺口处理方式。

Jira 是任务事实源，但不是 AgenticOps 表单标准的源头。AIAgent 必须面向 AgenticOps 标准字段工作；不同 Jira 项目、工作流、页面或自定义字段的差异通过工作流配置中的 Jira Form Mapping 适配。

表单数据不是普通附件。一个节点输出对应表单，代表该节点的标准动作已经执行过；后续操作必须基于这些表单数据、事件记录、审查结论和失败码判断下一步、重试、重做或停止。

## 2. 设计目标

- 让 AI 操作任务所需字段有统一源头，避免散落在操作、工作流配置、Jira 评论模板和手册中。
- 让不同 Jira 工作流先适配 AgenticOps 标准，不符合标准的地方进入 gap 记录和人工决策。
- 支持任务从卡片创建到完成的全周期字段维护。
- 支持不同生命周期阶段使用不同字段集合和不同必填规则。
- 支持不同专业角色在对应节点写入审查结论。
- 支持 AIAgent 基于表单数据判断 `agentic_next_action`，而不是只依赖聊天上下文。
- 支持任务失败后的重试和重做判断。
- 支持 AIAgent 在字段缺失、映射缺失或工作流不匹配时稳定阻断，而不是猜测。
- 支持后续把标准字段、生命周期要求和 Jira 映射转换为机器可读契约。

## 3. 标准分层

AgenticOps 表单体系分为三层。

| 层级 | 职责 | 源头 |
| --- | --- | --- |
| Task Form Standard | 定义 AgenticOps 标准字段、字段语义、值类型、负责人和敏感性。 | `docs/forms/`，后续 `contracts/forms/` |
| Lifecycle Form Requirements | 定义每个生命周期阶段需要哪些字段、何时必填、缺失时如何处理。 | `docs/forms/`，后续 `contracts/forms/` |
| Jira Form Mapping | 把标准字段映射到具体 Jira 项目的字段、描述模板、评论模板、状态或 `transition`。 | `install-resources/basic/projects/<project>/profile.yaml` |

操作契约只引用标准字段，不直接引用 Jira 字段。工作流配置负责把标准字段映射到具体系统事实。

## 4. 初始标准字段

第一阶段先覆盖研发 Jira 任务从创建到完成的最小闭环。

| 字段 | 用途 | 负责人 | 必需阶段 |
| --- | --- | --- | --- |
| `issue_key` | Jira 卡片编号。 | Jira | 创建后 |
| `issue_type` | 任务类型，例如需求、缺陷、技术任务。 | 需求负责人、Jira | 创建后 |
| `business_goal` | 任务要解决的业务或研发目标。 | 需求负责人 | 卡片创建 |
| `scope_boundary` | 明确包含和不包含的范围。 | 需求负责人、研发工程师 | 进入迭代 |
| `acceptance_criteria` | 验收标准。 | 需求负责人、研发工程师 | 进入迭代 |
| `owner` | 当前研发工程师。 | Jira / 迭代管理员 | 进入迭代 |
| `iteration` | 所属迭代或计划窗口。 | 迭代管理员 | 进入迭代 |
| `priority` | 任务优先级。 | 需求负责人、迭代管理员 | 进入迭代 |
| `risk_level` | 风险等级。 | 研发工程师 | 进入迭代 |
| `target_repo` | AI 需要读取和修改的目标仓库。 | 研发工程师、工作流配置 | AI 接管 |
| `target_branch` | 目标基线分支。 | 研发工程师、工作流配置 | AI 接管 |
| `verification_method` | 最小验证方式，例如命令、手动验收或 CI。 | 研发工程师 | AI 接管 |
| `environment_context` | 需要的环境、账号、测试数据或约束摘要。 | 研发工程师 | AI 接管 |
| `dependencies` | 外部依赖、前置任务或阻塞条件。 | 需求负责人、研发工程师 | AI 接管 |
| `agentic_run_id` | 一次 AI 执行记录的唯一编号；同一任务可以有多个历史 `agentic_run_id`。 | AgenticOps | 接管后 |
| `agent_id` | 当前 AIAgent 的稳定身份编号；同一 `agent_id` 可以产生多个 `agentic_run_id`。 | AgenticOps | AIAgent 初始化 |
| `agentic_id` | 当前任务绑定的 `agent_id`，用于所有权门禁和并发冲突检测；不是新的身份字段。 | AgenticOps、Jira 映射 | AI 接管 |
| `agentic_takeover_at` | AIAgent 成功接管任务的时间。 | AgenticOps | AI 接管 |
| `agentic_heartbeat_at` | 当前锁持有者最近一次成功心跳或持久化操作时间；不得使用 Jira 系统 `updated` 代替。 | AgenticOps、Jira 映射 | 接管后持续更新 |
| `task_type` | AgenticOps 任务类型。 | AgenticOps | 接管后 |
| `task_class` | 标准任务分类，用于选择对应标准流程。 | AgenticOps、工作流配置 | 接管前 |
| `process_id` | 标准流程编号。 | AgenticOps | 接管后 |
| `current_stage` | 当前执行阶段。 | AgenticOps | 接管后 |
| `agentic_next_action` | 下一步动作。 | AgenticOps | 接管后 |
| `implementation_summary` | 本地实现摘要。 | AIAgent | 开发完成 |
| `verification_result` | 实际验证结果。 | AIAgent | 开发完成 |
| `residual_risk` | 剩余风险和未验证部分。 | AIAgent | 开发完成 |
| `pr_link` | 拉取请求链接。 | 研发工程师、AIAgent（门禁后） | 拉取请求阶段 |
| `ci_status` | CI 状态摘要。 | GitHub / CI | 拉取请求阶段 |
| `review_status` | 审查意见处理状态。 | AIAgent、研发工程师 | 拉取请求阶段 |
| `reviewer_decision` | 专业审查结论，例如通过、退回、要求补充、阻断。 | 代码审查人、QA、运维、安全、研发工程师 | 审查节点 |
| `reviewer_required_action` | 审查后要求 AIAgent 或负责人执行的动作。 | 代码审查人、QA、运维、安全、研发工程师 | 审查节点 |
| `retry_policy` | 当前失败是否允许重试、最大次数或重试前置条件。 | AgenticOps、工作流配置 | 失败后 |
| `redo_from_stage` | 信息变更或审查退回时需要重做的起始阶段。 | AgenticOps、代码审查人、研发工程师 | 重做时 |
| `agentic_completion_evidence` | 最终完成证据。 | AIAgent、研发工程师 | 完成 |
| `follow_up_items` | 后续问题或新任务建议。 | AIAgent、研发工程师 | 完成 |
| `completed_at` | 标准流程完成或交接结束时间。 | AgenticOps、研发工程师 | 完成 |
| `agentic_id_cleared` | 完成或交接后是否已清理任务上的 `agentic_id`。 | AgenticOps | 完成 |

字段值不得包含 secrets、tokens、private keys、原始敏感日志或完整敏感代码片段。写入 Jira 的人可见内容必须使用中文。

### 标识字段边界

`agentic_run_id`、`agent_id` 和 `agentic_id` 不重复，分别处在执行记录、代理身份和任务绑定三层：

| 字段 | 表示 | 生命周期 | 关系 |
| --- | --- | --- | --- |
| `agent_id` | 一个 AIAgent 身份。 | AIAgent 初始化后长期稳定。 | 一个 `agent_id` 可以产生多个 `agentic_run_id`。 |
| `agentic_run_id` | 一次任务执行记录。 | 接管或恢复执行时创建或加载，完成后保留用于审计。 | 一个 Jira 卡片可以有多个历史 `agentic_run_id`。 |
| `agentic_id` | Jira 任务当前绑定的 `agent_id`。 | 接管成功后写入，完成或明确交接后清理。 | 同一时刻最多允许一个有效 `agentic_id`。 |

## 5. 生命周期要求

每个阶段只要求当前阶段需要的信息。AIAgent 不应因为后续阶段字段为空就阻断早期流程，但进入某阶段前必须满足该阶段门禁。

| 阶段 | 必需字段 | 门禁失败 |
| --- | --- | --- |
| 卡片创建 | `business_goal`、`issue_type` | 缺失时卡片不能作为 AI 可接管任务。 |
| 进入迭代 | `scope_boundary`、`acceptance_criteria`、`owner`、`iteration`、`priority`、`risk_level` | 缺失时不能进入 AI 接管候选列表。 |
| AI 接管 | `target_repo`、`target_branch`、`verification_method`、`environment_context`、`task_class`、`agent_id` | AIAgent 在调用 `takeover_task` 前按项目准入资产检查；不足时先分析、补卡并重新检查。CLI 只执行通用接管安全门禁。 |
| 本地开发 | `agentic_run_id`、`agent_id`、`agentic_id`、`agentic_takeover_at`、`agentic_heartbeat_at`、`task_type`、`task_class`、`process_id`、`current_stage`、`agentic_next_action` | 缺失时恢复接管或重新初始化执行记录。 |
| 开发完成 | `implementation_summary`、`verification_result`、`residual_risk` | 缺失时不能请求推送或创建拉取请求的确认。 |
| 拉取请求审查 | `pr_link`、`ci_status`、`review_status`、`reviewer_decision` | 缺失时不能进入完成证据。 |
| 完成 | `agentic_completion_evidence`、`follow_up_items`、`completed_at`、`agentic_id_cleared` | 缺失时不能关闭 AI 执行记录；完成或交接结束后必须清理任务上的 `agentic_id`。 |

## 6. 表单驱动推进规则

每个生命周期阶段完成后，都必须能回答：

- 该阶段的标准动作是否已经执行。
- 哪些表单字段证明动作已经执行。
- 是否经过对应专业角色审查。
- 当前结论允许进入哪个 `agentic_next_action`。
- 失败时允许重试还是必须重做前序阶段。
- 完成或交接结束后是否已经清理 `agentic_id`。

标准推进语义：

| 场景 | 必需决策 |
| --- | --- |
| 表单字段完整且门禁通过 | 进入下一阶段。 |
| 表单字段缺失 | 阻断当前阶段并输出补充动作。 |
| 专业审查退回 | 根据审查结论设置 `agentic_next_action`，通常进入修复和验证。 |
| 前序输入发生变化 | 设置 `redo_from_stage`，从受影响阶段重新生成表单。 |
| 操作执行失败但输入仍有效 | 根据 `retry_policy` 在当前阶段重试。 |
| 风险、权限或标准冲突 | 停止并请求人工确认。 |
| 任务完成或交接结束 | 写入完成表单并清理 `agentic_id`。 |

重试和重做的区别必须明确：

- 重试不改变前序表单结论，只重新执行当前失败动作。
- 重做会废弃或替代受影响阶段的旧表单结论，并要求重新审查。

AIAgent 恢复任务时，应先读取最近一次表单状态和事件记录，再决定继续、重试、重做或阻断。

## 7. Jira 表单映射

每个工作流配置需要维护 Jira 表单映射。映射必须说明标准字段如何从 Jira 或工作空间配置获得。

概念结构：

```yaml
workspace: tapstate

jira_form_mapping:
  user: dev@example.com
  project: TAP
  issue_types:
    - Story
    - Bug
    - Task

  fields:
    business_goal:
      source: jira_description_section
      section: 目标
      required_from_stage: card_created
    acceptance_criteria:
      source: jira_field
      jira_field: customfield_acceptance
      writable: true
      required_from_stage: iteration_ready
    target_repo:
      source: jira_field
      jira_field: customfield_target_repo
      fallback: workspace_repo_mapping
      required_from_stage: takeover_gate
    verification_method:
      source: jira_description_section
      section: 验证方式
      required_from_stage: takeover_gate
    risk_level:
      source: jira_field
      jira_field: customfield_risk
      writable: true
      required_from_stage: iteration_ready

  lifecycle_status_mapping:
    card_created:
      jira_statuses:
        - Open
        - Backlog
    iteration_ready:
      jira_statuses:
        - Selected for Development
    takeover_gate:
      jira_statuses:
        - In Progress

  workspace_repo_mapping:
    default: tapstate/example-repo
    by_component:
      api: tapstate/tap-api
      web: tapstate/tap-web
    by_label:
      cli: tapstate/agentic-ops
```

`writable: true` 必须按逻辑字段逐项声明，只用于允许 `update-task-form` 写入的业务结论字段。负责人、assignee、代理所有权、Description 章节和 Comment 映射必须保持只读，并使用各自专用原子操作维护。

`source` 可以是：

- `jira_field`
- `jira_description_section`
- `jira_comment`
- `workspace_profile`
- `workspace_repo_mapping`
- `agenticops_runtime`
- `github_pr`
- `ci_system`

如果字段来自描述、评论模板或 `workspace_repo_mapping`，映射必须定义可读规则和失败行为。AIAgent 不允许自由解析未声明的自然语言段落，也不允许在 Jira 空间存在多个代码仓库时临场猜测目标仓库。

## 8. Gap 处理

Jira 对接不满足 AgenticOps 标准时，先适配，再决策。

| 缺口 | 处理方式 |
| --- | --- |
| Jira 有字段但名称不同 | 更新 Jira Form Mapping。 |
| Jira 字段存在但值为空 | AIAgent 阻断当前阶段，按项目资产结合代码形成补卡建议，经确认后使用通用 Jira 原子操作写回。 |
| Jira 没有字段但描述模板稳定包含 | 在映射中声明 `jira_description_section`。 |
| Jira 没有字段也没有稳定模板 | 记录 `missing_form_field`，请求流程负责人决策。 |
| Jira 状态无法对应生命周期阶段 | 记录 `lifecycle_mapping_gap`，请求工作流决策。 |
| 标准字段不适合某类任务 | 记录 `task_form_standard_gap`，请求是否调整标准。 |
| 专业审查节点无法映射 | 记录 `review_gate_mapping_gap`，请求工作流决策。 |
| 重试或重做规则缺失 | 记录 `retry_redo_policy_gap`，请求工作流配置或策略决策。 |
| 任务分类无法映射 | 记录 `task_class_mapping_gap`，请求研发工程师或流程负责人决策。 |
| 完成后无法清理 `agentic_id` | 记录 `agent_release_failed`，请求研发工程师决策是否人工释放。 |

稳定错误码建议：

- `missing_form_field`
- `unmapped_jira_field`
- `empty_required_form_field`
- `unsupported_form_source`
- `lifecycle_mapping_gap`
- `task_form_standard_gap`
- `review_gate_mapping_gap`
- `retry_redo_policy_gap`
- `task_class_mapping_gap`
- `assignee_mismatch`
- `assignee_changed`
- `agent_ownership_conflict`
- `agent_binding_lost`
- `agent_release_failed`

错误输出必须包含：

- 标准字段名。
- 当前生命周期阶段。
- 当前 Jira 项目和卡片编号。
- 已配置映射。
- 缺失或不匹配原因。
- 需要人工补充或决策的动作。

## 9. 与现有契约的关系

操作契约：

- 声明操作需要哪些标准字段。
- 声明缺少标准字段时的稳定错误码。
- 声明操作输出如何更新 `current_stage`、`agentic_next_action`、`retry_policy` 或 `redo_from_stage`。
- 不声明具体 Jira 自定义字段。

工作流配置：

- 声明工作空间、Jira 项目、状态映射、`transition` 映射和 Jira 表单映射。
- 声明任务分类到标准流程的映射。
- 声明专业审查节点、允许重试的失败类型和必须重做的阶段边界。
- 负责解释标准字段如何落到具体 Jira 事实。

证据模板：

- 使用标准字段渲染中文 Jira 评论、工作日志和证据。
- 不直接读取 Jira 自定义字段。

AI 员工手册：

- 要求 AIAgent 面向标准字段工作。
- 要求缺少标准字段时阻断或请求人工补充。

反馈闭环：

- 聚合缺失字段、映射缺口和生命周期不匹配。
- 聚合审查退回、重试次数、重做来源阶段和重复阻塞原因。
- 生成流程改进建议，但不能未经人工确认修改标准或 Jira 工作流。

## 10. 维护流程

新增、修改或废弃标准字段时，必须同步检查：

- `docs/forms/task-form-standard.md`
- 后续 `contracts/forms/` 机器可读契约。
- `docs/contracts/operation-contract.md`
- `docs/profiles/workflow-profile.md`
- `docs/templates/evidence-templates.md`
- `install-resources/basic/handbooks/ai-employee-handbook.md`
- 工作流配置中的 Jira Form Mapping。

字段变更分为三类：

| 变更 | 规则 |
| --- | --- |
| 新增可选字段 | 可以先加入标准，后续工作流配置逐步适配。 |
| 新增某阶段必填字段 | 必须进入决策记录，并同步补充门禁、模板和映射。 |
| 删除或重命名字段 | 必须提供迁移方案，不得直接破坏历史证据和反馈聚合。 |

## 11. 第一阶段落地建议

第一阶段先做文档和契约基线，不接真实 Jira 写操作：

1. 固定 Task Form Standard 和生命周期要求。
2. 在工作流配置文档中引用 Jira Form Mapping。
3. 在操作契约中把接管门禁的前置条件改为标准字段要求。
4. 后续新增 `contracts/forms/task-form-standard.yaml` 和 `contracts/forms/lifecycle-requirements.yaml`。
5. 后续新增工作流配置样例，只作为示例，不作为真实 Jira 默认配置。
6. 在反馈报告中聚合 `missing_form_field`、`lifecycle_mapping_gap`、审查退回、重试和重做事件。

这让 AgenticOps 先形成可审阅、可演进的标准，再逐步接入不同 Jira 工作流。
