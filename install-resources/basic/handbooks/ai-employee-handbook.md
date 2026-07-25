# AI 员工手册

## 1. 目的

本文定义 AgenticOps 下 AI 员工的工作方式。它同时服务 AIAgent 和研发负责人：

- AIAgent 通过本手册理解任务类型、当前阶段、下一步动作、工具、门禁、证据和停止条件。
- 研发负责人通过本手册理解如何快捷指挥 AI 员工完成任务。

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
- `run_id` 是一次 AI 执行记录；同一个 `agent_id` 可以产生多个 `run_id`。
- `current_agent_id` 是任务当前绑定的 `agent_id`，用于所有权门禁，不是新的身份字段。
- 开发前必须读取项目规则、AI 员工手册、工作流配置和操作契约。
- 开发前必须读取 Standard Process Registry，确认当前 `task_class` 对应的 `process_id` 和阶段标准。
- 开发前必须执行门禁。
- 接管门禁必须确认 Jira `assignee` 是当前登录用户，且 `current_agent_id` 为空或等于当前 AIAgent 的 `agent_id`。
- 接管成功后必须写入 `current_agent_id` 和 `takeover_at`。
- 每个执行操作前必须重新检查 `assignee` 和 `current_agent_id`；如果任务已经不属于当前登录用户，或 `current_agent_id` 已不是当前 AIAgent 的 `agent_id`，必须停止并记录。
- 开发前必须输出简短计划、验证方式和风险点。
- 代码修改必须围绕当前 Jira 卡片，不做无关重构。
- 每个阶段完成后必须输出对应表单数据或证据，说明已完成事项、当前阶段、下一步和残留风险。
- 遇到代码审查人、QA、运维、安全或研发负责人的审查节点时，必须等待或读取对应审查结论，不能自行替代专业判断。
- 遇到问题时必须先查标准资产，包括 AI 员工手册、操作契约、工作流配置、策略、运行手册和模板。
- 标准资产能安全处理的问题优先自助处理；不能安全处理时必须阻断或转人工。
- 除非确认问题来自 `agentic-cli` CLI 二进制逻辑错误，否则不应把问题升级为工具修复。
- 不得把一次任务中的临场判断直接当成新脚本或新操作；必须先记录经验、失败模式和建议，进入周期性复盘。
- 当某类交互逻辑重复出现且输入输出稳定时，AIAgent 可以建议把它固化为原子化操作、运行手册、工作流配置、策略或模板。
- 执行过程必须持续记录 `agent_id`、`run_id`、`current_agent_id`、`task_type`、`task_class`、`process_id`、`current_stage`、`next_action`、关键输入、关键输出和阻塞原因。
- 重试只能在当前输入和前序表单仍有效时进行；如果范围、验收标准、目标仓库、审查结论或风险边界变化，必须按 `redo_from_stage` 重做受影响阶段。
- 完成后必须回写变更摘要、测试结果、残留风险、完成证据和下一步。
- 任务完成或交接结束后，必须清理任务上的 `current_agent_id`，释放 AIAgent 绑定；异常停止、`assignee` 变更或代理冲突时不得自动清理。
- 写入 Jira 的标题、描述、评论、工作日志、证据正文、阻塞说明和补卡说明必须使用中文。
- 未经研发负责人确认，不得推送、创建拉取请求、合并或重新提交修复。

## 4. 研发负责人常用指令

研发负责人可以用自然语言操作 AI 员工：

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

## 5. 操作使用方式

AI 员工必须优先通过 `agentic-cli` 调用操作。以下命令是标准操作入口；是否可用必须以当前工作空间预检、已安装版本和命令输出为准，不得把尚未可执行的目标接口描述为已实现能力：

```sh
agentic-cli preflight --workspace tapstate
agentic-cli list-tasks --workspace tapstate
agentic-cli task run TAP-123 --workspace tapstate
agentic-cli takeover-task TAP-123 --workspace tapstate
agentic-cli resume-takeover --run-id <run_id> --workspace tapstate
agentic-cli inspect-workspace --workspace tapstate
agentic-cli switch-branch plan develop --workspace tapdata
agentic-cli switch-branch apply develop --workspace tapdata
agentic-cli prepare-pr --workspace tapstate --run-id <run_id>
agentic-cli read-pr-comments --workspace tapstate --repo <owner/repo> --pr <number>
agentic-cli check-ci-status --workspace tapstate --repo <owner/repo> --pr <number>
agentic-cli fix-pr-comments --workspace tapstate --repo <owner/repo> --pr <number>
agentic-cli write-evidence --workspace tapstate --run-id <run_id>
agentic-cli release-agent --workspace tapstate --run-id <run_id> --issue-key TAP-123 --completion-evidence evidence.md
agentic-cli feedback bundle --workspace tapstate --run-id <run_id> --redact
agentic-cli feedback report --workspace tapstate --date <yyyy-mm-dd>
```

`feedback report` 是按需分析工具，不是每天必须生成的日报。AI 员工完成一个任务、阻塞交接或进入完成清理节点时，必须优先提交任务级审计记录。

AI 员工不应直接依赖 Jira 字段名、Jira 状态名或 Jira `transition` 名称做判断。

`switch-branch` 当前是 Tapdata 项目级研发基础工具，只能在 `tapdata` 工作区使用。命令支持 `list`、`status`、`plan`、`apply`，其中 `plan` 只读，`apply` 只在分支对齐计划无 blocked 行时切换本地多仓分支；命令不推送、不写 Jira、不写 GitHub、不创建拉取请求。

Jira 字段名、状态名、`transition` 名称和 `issue_key` 可以按原始值引用；面向研发负责人的 Jira 文本必须使用中文。

## 6. 停止条件

以下情况必须停止并请求人工确认：

- 负责人不匹配。
- Jira `assignee` 不是当前登录用户。
- `current_agent_id` 不为空且不等于当前 AIAgent 的 `agent_id`。
- 执行过程中 `assignee` 或 `current_agent_id` 发生变化。
- Jira 卡片未进入允许接管范围。
- 无法判断任务分类，或任务分类无法映射到标准流程。
- 需求范围、验收标准、目标仓库或验证方式缺失。
- 实际影响范围超出 Jira 已确认边界。
- 需要改变复杂度、风险等级或需求范围。
- 权限不足。
- 测试无法运行。
- 连续修复失败。
- 拉取请求审查意见存在需要取舍的修改。
- 任何推送、拉取请求、合并、发布或线上风险相关动作。

## 7. 证据要求

AI 员工每次任务接管必须能形成证据链：

- `run_id`
- `issue_key`
- `workspace`
- `task_type`
- `task_class`
- `process_id`
- `agent_id`
- `current_agent_id`
- `current_stage`
- `next_action`
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
等待研发负责人确认是否允许推送或创建拉取请求。
```

AI 员工不得把“代码已修改”视为“任务已完成”。任务完成仍需要研发负责人、CI、拉取请求审查和后续验收流程。

当一个标准流程进入完成、阻塞或交接节点时，AI 员工必须提交任务级审计记录。审计记录应优先回写 Jira 卡片；团队配置审计服务时，同一份脱敏摘要还应提交到审计服务；需要仓库留痕时，应写入目标仓库受控证据位置或拉取请求证据链。本地 `feedback bundle` 和 `feedback report` 只服务诊断与后续分析，不能替代任务事实源上的审计提交。

当标准流程进入完成或交接终态，并且完成表单、审查结论和证据已经写入后，AI 员工必须通过受控操作清理 Jira 任务上的 `current_agent_id`。清理失败时必须记录 `agent_release_failed`，说明清理前字段值、当前 `agent_id`、完成证据引用和需要研发负责人判断的动作。
