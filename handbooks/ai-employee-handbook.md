# AI 员工手册

## 1. 目的

本文定义 AgenticOps 下 AI 员工的工作方式。它同时服务 AIAgent 和研发 owner：

- AIAgent 通过本手册理解任务类型、当前阶段、下一步动作、工具、gate、证据和停止条件。
- 研发 owner 通过本手册理解如何快捷指挥 AI 员工完成任务。

## 2. 任务模型

AI 员工不按固定角色工作。AIAgent 必须先判断当前接收的任务是什么、进行到哪一步、下一步需要做什么。

| 维度 | 说明 |
| --- | --- |
| 任务类型 | 安装、工作空间初始化、AIAgent 初始化、新任务接管、恢复接管、PR comments 修复、工作日志上报、AgenticOps 改进建议。 |
| 当前阶段 | 未初始化、预检中、等待接管、分析中、开发中、验证中、证据回写中、等待人工确认、阻塞、已交接。 |
| 下一步动作 | 由 operation contract、workspace profile、当前 evidence 和人工门禁共同决定。 |

AIAgent 不应因为“像开发任务”就默认进入开发。必须先完成任务识别、阶段识别和 gate 检查。

## 3. 工作原则

AI 员工必须遵守：

- 单次任务接管只处理一个 Jira issue。
- 开发前必须读取项目规则、AI 员工手册、workspace profile 和 Operation Contract。
- 开发前必须执行 gate。
- 开发前必须输出简短计划、验证方式和风险点。
- 代码修改必须围绕当前 issue，不做无关重构。
- 完成后必须回写变更摘要、测试结果、残留风险和下一步。
- 未经研发 owner 确认，不得 push、创建 PR、merge 或重新提交修复。

## 4. 研发负责人常用指令

研发 owner 可以用自然语言操作 AI 员工：

```text
初始化 AgenticOps 能力，工作空间是 tapstate。
列出我今天的 Jira 任务。
接管 TAP-123。
恢复 TAP-123 上次的接管任务。
根据 PR comments 修复。
汇总今天 tapstate 工作空间的 AI 执行日志，并给出 AgenticOps 改进建议。
```

AI 员工应把自然语言转换为 AgenticOps operation，而不是直接操作 Jira workflow。

## 5. 操作使用方式

AI 员工必须优先通过 `agent-task-ops` 调用 operation。以下命令是第一阶段目标接口，除非当前工作空间预检证明工具可用，否则不得声称已经实现：

```sh
agent-task-ops preflight --workspace tapstate
agent-task-ops list-tasks --workspace tapstate
agent-task-ops takeover-task TAP-123 --workspace tapstate
agent-task-ops resume-takeover --run-id <run_id> --workspace tapstate
agent-task-ops write-evidence --run-id <run_id>
agent-task-ops feedback report --workspace tapstate --date <yyyy-mm-dd>
```

AI 员工不应直接依赖 Jira 字段名、Jira 状态名或 Jira transition 名称做判断。

## 6. 停止条件

以下情况必须停止并请求人工确认：

- owner 不匹配。
- Jira issue 未进入允许接管范围。
- 需求范围、验收标准、目标仓库或验证方式缺失。
- 实际影响范围超出 Jira 已确认边界。
- 需要改变复杂度、风险等级或需求范围。
- 权限不足。
- 测试无法运行。
- 连续修复失败。
- PR comments 存在需要取舍的修改。
- 任何 push、PR、merge、发布或线上风险相关动作。

## 7. 证据要求

AI 员工每次任务接管必须能形成证据链：

- `run_id`
- issue key
- workspace
- task type
- current stage
- next action
- 接管成功或失败记录
- 变更摘要
- 验证结果
- 残留风险
- 下一步
- PR 链接或阻塞原因

证据不得包含 secrets、tokens、private keys、原始敏感日志、完整 Jira 描述或敏感代码片段。

## 8. 完成行为

AI 员工完成本地开发和验证后，必须停在人工确认点：

```text
本地开发完成。
已记录变更摘要、验证结果和残留风险。
等待研发 owner 确认是否允许 push / PR。
```

AI 员工不得把“代码已修改”视为“任务已完成”。任务完成仍需要研发 owner、CI、PR Review 和后续验收流程。
