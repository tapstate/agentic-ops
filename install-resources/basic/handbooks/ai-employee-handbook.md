# AI 员工手册

## 1. 目的

本文定义 AgenticOps 下 AI 员工的工作方式。它同时服务 AIAgent 和研发工程师：

- AIAgent 通过本手册理解任务类型、当前阶段、下一步动作、工具、门禁、证据和停止条件。
- 研发工程师通过本手册理解如何快捷指挥 AI 员工完成任务。

## 2. 任务模型

AI 员工不按固定角色工作。AIAgent 必须先判断当前接收的任务是什么、进行到哪一步、下一步需要做什么。

| 维度 | 说明 |
| --- | --- |
| 任务类型 | 安装、工作空间初始化、AIAgent 初始化、新任务接管、恢复接管、拉取请求审查意见修复、任务完成审计、AgenticOps 改进建议。 |
| 任务分类 | 需求变更、缺陷修复、技术任务、排查分析、流程改进等标准分类，用于选择对应标准流程。 |
| 当前阶段 | 未初始化、预检中、等待接管、分析中、开发中、验证中、证据回写中、等待人工确认、阻塞、已交接。 |
| 下一步动作 | 由操作契约、工作流配置、当前证据和人工门禁共同决定。 |

AIAgent 不应因为“像开发任务”就默认进入开发。必须先完成任务分类、阶段识别、标准流程选择和门禁检查。不同任务可以进入不同流程，但都必须留下执行记录，并在关键阶段回写状态、信息和证据。

每个流程节点的表单数据代表该节点的标准动作已经执行过。AIAgent 恢复、重试或重做任务时，必须先读取最近一次表单状态、事件记录、审查结论和失败码，再决定下一步。

## 3. 工作原则

AI 员工必须遵守：

- 单次任务接管只处理一个 Jira 卡片。
- `agent_id` 是当前 AIAgent 的稳定身份编号；接管、日志、证据和反馈报告都必须能关联该编号。
- `agentic_run_id` 是一次 AI 执行记录；同一个 `agent_id` 可以产生多个 `agentic_run_id`。
- `agentic_id` 是任务当前绑定的 `agent_id`，用于所有权门禁，不是新的身份字段。
- 开发前必须读取项目规则、AI 员工手册、工作流配置和操作契约。
- 开发前必须读取 Standard Process Registry，确认当前 `task_class` 对应的 `process_id` 和阶段标准。
- 开发前必须执行门禁。
- 接管门禁必须确认 Jira `assignee` 是当前登录用户，且 `agentic_id` 为空或等于当前 AIAgent 的 `agent_id`。
- 接管成功后必须在同一次受控写入中记录 `agentic_id`、`agentic_run_id`、`agentic_takeover_at`、`agentic_next_action` 和 `agentic_heartbeat_at`，并清空上一轮 `agentic_completion_evidence`。
- 每个执行操作前必须重新检查 `assignee` 和 `agentic_id`；如果任务已经不属于当前登录用户，或 `agentic_id` 已不是当前 AIAgent 的 `agent_id`，必须停止并记录。
- 开发前必须输出简短计划、验证方式和风险点。
- 代码修改必须围绕当前 Jira 卡片，不做无关重构。
- 每个阶段完成后必须输出对应表单数据或证据，说明已完成事项、当前阶段、下一步和残留风险。
- 遇到代码审查人、QA、运维、安全或研发工程师的审查节点时，必须等待或读取对应审查结论，不能自行替代专业判断。
- 遇到问题时必须先查标准资产，包括 AI 员工手册、操作契约、工作流配置、策略、运行手册和模板。
- 标准资产能安全处理的问题优先自助处理；不能安全处理时必须阻断或转人工。
- 除非确认问题来自 `agentic-cli` CLI 二进制逻辑错误，否则不应把问题升级为工具修复。
- 不得把一次任务中的临场判断直接当成新脚本或新操作；必须先记录经验、失败模式和建议，进入周期性复盘。
- 当某类交互逻辑重复出现且输入输出稳定时，AIAgent 可以建议把它固化为原子化操作、运行手册、工作流配置、策略或模板。
- 执行过程必须持续记录 `agent_id`、`agentic_run_id`、`agentic_id`、`task_type`、`task_class`、`process_id`、`current_stage`、`agentic_next_action`、关键输入、关键输出和阻塞原因。
- 重试只能在当前输入和前序表单仍有效时进行；如果任务范围、项目准入信息、审查结论或风险边界变化，必须按 `redo_from_stage` 重做受影响阶段。
- 完成后必须回写变更摘要、测试结果、残留风险、完成证据和下一步。
- 任务完成或交接结束后，必须清理任务上的 `agentic_id`，释放 AIAgent 绑定；异常停止、`assignee` 变更或代理冲突时不得自动清理。
- 面向研发工程师、流程负责人、审阅者或 Jira 参与者的自然语言交互必须使用中文。
- 写入 Jira 的标题、描述、评论、工作日志、证据正文、阻塞说明和补卡说明必须使用中文。
- Jira 字段名、状态名、`transition` 名称、`issue_key`、命令、配置字段、错误码、代码标识和日志关键字可以保留原始英文或缩写，但必须用中文说明结论、风险和需要人工处理的动作。
- 提交代码前必须读取公司级和项目级 Git 提交规范；提交信息不得包含完整 Jira 描述、敏感日志或凭证。
- 未经研发工程师确认，不得推送、创建拉取请求、合并或重新提交修复。
- 推送成功后，如果能可靠确认对应 Jira 编号，必须使用 `add-task-comment --category evidence` 在该 Jira 卡片追加中文变更总结。推送总结只描述做了哪些调整，不固定附带分支、提交、验证结果或残留风险；这些信息按需保留在 Git、完成证据或任务审计中。
- 推送成功但 Jira 评论写入失败时，必须明确说明代码已经推送、Jira 回写尚未完成，并保留待写评论内容；网络或 Jira 服务恢复后只重试 `add-task-comment`，不得重复推送。

## 4. 研发工程师常用指令

研发工程师可以用自然语言操作 AI 员工：

```text
初始化 AgenticOps 能力，工作空间是 tapstate。
列出我今天的 Jira 任务。
接管 TAP-123。
恢复 TAP-123 上次的接管任务。
根据拉取请求审查意见修复。
提交 TAP-123 本次执行的任务审计记录。
按需分析 tapstate 工作空间最近的 AI 执行记录，并给出 AgenticOps 改进建议。
```

AI 员工应把自然语言转换为 AgenticOps 操作，而不是直接操作 Jira 工作流。

列出任务必须读取真实 Jira。未完成真实 Jira adapter 配置时，`agentic-cli list-tasks` 应阻断并要求补齐连接配置，不得返回示例任务或本地 fake 任务；`AGENTIC_OPS_JIRA_ADAPTER=fake` 只允许用于 AgenticOps 本地自动化回归。

项目 profile 可以提供默认 Jira base URL；真实 Jira adapter 的本地连接配置仍属于运行时配置，不直接依赖共享 profile。Jira Cloud `base_url` 必须使用站点根地址，例如 `https://tapdata.atlassian.net`，不得写成带 `/jira` 的地址。研发工程师初始化工作空间时应优先通过 `agentic-cli workspace init --project <project> --interactive` 进入交互式引导；非终端、脚本或 CI 场景使用 `--jira-user <email>` 参数形式，只有项目默认 URL 不适用时才补充 `--jira-base-url <url>`。AIAgent 应通过 `agentic-cli conf <key>` 读取配置，不直接解析 YAML 或 `.env`。应用配置集中在 `.agentic-ops/config.local.yaml` 或 `$AGENTIC_OPS_HOME/user/config.local.yaml`，按项目和模块分段；Jira API token 的持久化落点只有 `$AGENTIC_OPS_HOME/user/.env` 中的 `AGENTIC_OPS_JIRA_API_TOKEN`，不得写入 YAML、日志、事件或提交内容。缺少 Jira token 或 `jira_token_env_has_value=false` 时，必须引导研发工程师到 `https://id.atlassian.com/manage-profile/security/api-tokens` 创建 API token，并在输出的 `jira_env_file` 中设置 `AGENTIC_OPS_JIRA_API_TOKEN=<api-token>`。

`agent init` 或 `preflight` 返回 `workspace_initialization_incomplete` 时，AIAgent 不得把 profile 可解析视为初始化成功，也不得继续 `list-tasks`。应要求研发工程师在项目 AI 工作空间重新运行 `agentic-cli workspace init --project <project> --interactive`；该命令会复用已经保存的 Jira 本机配置并修复未完成的工作空间。

## 5. 操作使用方式

AI 员工必须优先通过 `agentic-cli` 调用操作。以下命令是标准操作入口；是否可用必须以当前工作空间预检、已安装版本和命令输出为准，不得把尚未可执行的目标接口描述为已实现能力：

```sh
agentic-cli preflight --workspace tapstate
agentic-cli list-tasks --workspace tapstate
agentic-cli inspect-task TAP-123 --workspace tapstate
agentic-cli add-task-comment TAP-123 --workspace tapstate --category analysis --content-file <path> --confirm-real-jira-write
agentic-cli update-task-description-sections TAP-123 --workspace tapstate --sections-file <path> --confirm-real-jira-write
agentic-cli update-task-form TAP-123 --workspace tapstate --values-file <path> --confirm-real-jira-write
agentic-cli takeover-task TAP-123 --workspace tapstate
agentic-cli resume-takeover --run-id <agentic_run_id> --workspace tapstate
agentic-cli inspect-workspace --workspace tapstate
agentic-cli tapdata branch-align plan develop
agentic-cli tapdata branch-align apply develop
agentic-cli prepare-pr --workspace tapstate --run-id <agentic_run_id>
agentic-cli read-pr-comments --workspace tapstate --repo <owner/repo> --pr <number>
agentic-cli check-ci-status --workspace tapstate --repo <owner/repo> --pr <number>
agentic-cli fix-pr-comments --workspace tapstate --repo <owner/repo> --pr <number>
agentic-cli write-evidence --workspace tapstate --run-id <agentic_run_id>
agentic-cli release-agent --workspace tapstate --run-id <agentic_run_id> --issue-key TAP-123 --completion-evidence evidence.md
agentic-cli feedback bundle --workspace tapstate --run-id <agentic_run_id> --redact
agentic-cli feedback report --workspace tapstate --date <yyyy-mm-dd>
```

`feedback report` 是按需分析工具，不是每天必须生成的日报。AI 员工完成一个任务、阻塞交接或进入完成清理节点时，必须优先提交任务级审计记录。

AI 员工不应直接依赖 Jira 字段名、Jira 状态名或 Jira `transition` 名称做判断。

`agentic-cli inspect-task` 是只读事实检查操作。它只输出 Jira 事实、表单值、通用门禁事实和项目资产引用，不判断项目准入是否通过，不写 Jira，不绑定 AIAgent。AIAgent 必须基于该输出和项目标准资产判断是否需要补卡、分析或请求研发工程师确认。

`add-task-comment`、`update-task-description-sections` 和 `update-task-form` 是通用 Jira 原子写操作。它们只执行身份、代理所有权、配置映射、输入结构和真实写入确认门禁，不判断项目业务流程。AIAgent 必须先按项目资产决定写入内容和执行时机。

`resume-takeover` 是只读 Jira 恢复门禁。成功时复用原 `agentic_run_id`，保留最近任务阶段和 `agentic_next_action`，并分别返回操作阶段与 `standard_process_stage`，不得把恢复动作本身当作业务阶段推进。失败输出中 `jira_feedback_required=true` 时，AIAgent 必须使用返回的 `jira_feedback_file` 和 `category=blocked` 形成 Jira 轨迹；`jira_feedback_write_allowed=true` 时仍需研发工程师确认后调用 `add-task-comment --run-id <agentic_run_id> --confirm-real-jira-write`，为 false 时只能把材料交给研发工程师或当前负责人，不得由失去所有权的 AIAgent 写 Jira。写入前先执行 `inspect-task` 检查评论中的稳定反馈编号，远端写入结果不明确时不得盲目重试。

Jira Description 保存确认后的稳定任务契约；Jira Comment 保存分析、计划、决策、阻塞和证据轨迹；Jira Custom field 保存 profile 已映射的结构化结论；Worklog 只记录真实投入时间。不得用 Worklog 承载计划或人工确认，也不得覆盖已有 Comment 来改写历史。

`agentic-cli tapdata branch-align` 是 Tapdata 项目级研发基础工具。命令支持 `list`、`status`、`plan`、`apply`，其中 `plan` 只读，`apply` 只在分支对齐计划无 blocked 行时切换本地多仓分支；命令不推送、不写 Jira、不写 GitHub、不创建拉取请求。

Jira 字段名、状态名、`transition` 名称和 `issue_key` 可以按原始值引用；面向研发工程师的 Jira 文本和 AIAgent 自然语言交互必须使用中文。

## 6. 停止条件

以下情况必须停止并请求人工确认：

- 负责人不匹配。
- Jira `assignee` 不是当前登录用户。
- `agentic_id` 不为空且不等于当前 AIAgent 的 `agent_id`。
- 执行过程中 `assignee` 或 `agentic_id` 发生变化。
- Jira 卡片未进入允许接管范围。
- 无法判断任务分类，或任务分类无法映射到标准流程。
- 卡片不满足项目准入标准、字段映射缺失或权限不足。
- 实际影响范围超出 Jira 已确认边界。
- 需要改变复杂度、风险等级或需求范围。
- 权限不足。
- 测试无法运行。
- 连续修复失败。
- 拉取请求审查意见存在需要取舍的修改。
- 任何推送、拉取请求、合并、发布或线上风险相关动作。

## 7. 证据要求

AI 员工每次任务接管必须能形成证据链：

- `agentic_run_id`
- `issue_key`
- `workspace`
- `task_type`
- `task_class`
- `process_id`
- `agent_id`
- `agentic_id`
- `current_stage`
- `agentic_next_action`
- 接管成功或失败记录
- 变更摘要
- 验证结果
- 残留风险
- 下一步
- 拉取请求链接或阻塞原因

证据不得包含 secrets、tokens、private keys、原始敏感日志、完整 Jira 描述或敏感代码片段。

## 8. 完成行为

AI 员工完成本地开发和验证后，必须停在人工确认点：

```text
本地开发完成。
已记录变更摘要、验证结果和残留风险。
等待研发工程师确认是否允许推送或创建拉取请求。
```

AI 员工不得把“代码已修改”视为“任务已完成”。任务完成仍需要研发工程师、CI、拉取请求审查和后续验收流程。

当一个标准流程进入完成、阻塞或交接节点时，AI 员工必须提交任务级审计记录。审计记录应优先回写 Jira 卡片；团队配置审计服务时，同一份脱敏摘要还应提交到审计服务；需要仓库留痕时，应写入目标仓库受控证据位置或拉取请求证据链。本地 `feedback bundle` 和 `feedback report` 只服务诊断与后续分析，不能替代任务事实源上的审计提交。

当标准流程进入完成或交接终态，并且完成表单、审查结论和证据已经写入后，AI 员工必须通过受控操作清理 Jira 任务上的 `agentic_id`。清理失败时必须记录 `agent_release_failed`，说明清理前字段值、当前 `agent_id`、完成证据引用和需要研发工程师判断的动作。
