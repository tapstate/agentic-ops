# DE-005 恢复接管任务

> **目标故事合同。** 本文不维护当前完成度；以下阶段与输出只定义未来验收行为。`resume_takeover` 当前为 `capability_gap`，没有可调用的恢复命令；执行前必须查询能力目录，不得用本地 task 原语冒充恢复接管。涉及 Jira 评论时，现役入口只允许使用 `jira_comment` 的 `ao-work jira comment plan -> apply -> readback` 协议。

作为研发工程师，
我希望能恢复一个已接管但未完成的任务，
以便 AIAgent 继续同一个 `agentic_run_id` 的上下文，而不是重新开始或混淆多次执行记录。

### 触发方式

```sh
ao-work capability show resume_takeover
```

或自然语言：

```text
恢复 TAP-123 上次的接管任务。
```

### 前置条件

- 已存在接管记录。
- `agentic_run_id` 对应的 `issue`、`workspace`、`agent_id`、`task_class`、`process_id` 和任务阶段可验证。
- 当前 Jira 卡片和项目 profile 能确定负责人、代理绑定、状态和目标仓库。

### 主流程

1. AIAgent 查询 `resume_takeover`；当前为 `capability_gap` 时停止自动恢复并请求研发工程师人工核对，只有未来能力目录声明 `implemented` 后才可调用其现役命令。
2. CLI 从同一 `agentic_run_id` 的事件中恢复接管基准和最近任务阶段。
3. CLI 读取当前 Jira 卡片和当前用户，复核 `assignee`、`agentic_id` 和目标仓库。
4. CLI 使用操作契约校验操作阶段，并把 Jira 状态映射为 Standard Process Registry 阶段进行校验。
5. CLI 返回原任务阶段、标准流程阶段和下一步动作，不推进业务阶段。
6. AIAgent 调用 `inspect-workspace` 检查当前本地代码状态，再向研发工程师说明恢复点。
7. AIAgent 从返回的恢复点继续执行，不创建新的接管记录。

### 输出

```json
{
  "ok": true,
  "operation": "resume_takeover",
  "workspace": "tapstate",
  "issue_key": "TAP-123",
  "agentic_run_id": "TAP-123-takeover-20260721103012-a8f3",
  "target_repo": "tapstate/example-repo",
  "previous_stage": "takeover_started",
  "current_stage": "takeover_started",
  "standard_process_stage": "waiting_takeover",
  "agentic_next_action": "proceed"
}
```

### 失败处理

- `agentic_run_id` 不存在、workspace 不匹配或本地事件不可信时，只返回本地错误，不生成 Jira 评论材料。
- 当前 `workspace` 与 `agentic_run_id` 不匹配时，拒绝恢复。
- Jira `assignee` 已变化、代理绑定丢失或绑定其他 AIAgent 时，停止恢复，不自动抢回绑定。
- 当前目标仓库与接管时不一致时，停止恢复，不允许同一个 `agentic_run_id` 静默切换仓库。
- 操作阶段、任务分类、标准流程或 Jira 状态映射不一致时，停止并请求维护对应标准资产。
- 上次失败原因属于人工确认点时，AIAgent 不能自动继续。
- 可信任务级阻塞生成 `jira_feedback_file`。允许当前 AIAgent 写入时，经研发工程师确认后按 `jira_comment` 的 plan、apply、readback 协议写入；失去任务所有权时只把材料交给研发工程师或当前负责人。旧 `add_task_comment` 为 `capability_gap`，不得调用。

### 验收标准

- 恢复任务不会创建新的 `agentic_run_id`。
- 恢复前必须校验 `workspace`、`issue`、负责人、代理绑定、目标仓库、操作阶段和标准流程阶段。
- AIAgent 能说明从哪个操作阶段、哪个标准流程阶段恢复。
- 恢复过程继续写入同一个 run 的事件日志。
- `resume-takeover` 本身不写 Jira。
- 可信任务级阻塞能通过受控 `jira_comment` plan、apply、readback 形成 Jira 轨迹。

### 保护行为

- 恢复接管必须复用已有 `agentic_run_id`，不能创建新 `agentic_run_id`。
- 恢复前必须重新读取 Jira，校验 `workspace`、`issue`、负责人、代理绑定、目标仓库和流程阶段。
- 本地代码状态由恢复成功后的 `inspect-workspace` 单独检查。
- 上次停在人工确认点时，AIAgent 不能自动继续。
- 恢复成功不能生成 `takeover_resumed` 业务阶段或固定改写 `agentic_next_action`。
- 恢复过程必须继续写入同一个 run 的事件日志。

### 审核问题

- `agentic_run_id` 是否存在且与当前工作空间匹配。
- 当前 Jira 卡片负责人、代理绑定和目标仓库是否仍一致。
- 操作阶段与标准流程阶段是否分别通过对应契约校验。
- 本地代码状态是否允许继续，是否需要研发工程师确认。
- AIAgent 是否清楚说明 previous stage、current stage、standard process stage 和 next action。
- 恢复阻塞是否按写入资格形成 Jira 反馈或交给当前负责人。

### 验收证据

- `ao-work capability show resume_takeover` 输出的当前状态；能力实现后再补正式恢复输出。
- 同一 `agentic_run_id` 的 run summary 和 events。
- 输出中的 `previous_stage`、`current_stage`、`standard_process_stage`、`target_repo` 和 `agentic_next_action`。
- 恢复失败时的结构化失败记录。
- 任务级阻塞对应的 Jira 评论材料，以及 `jira_comment` plan、apply、readback 结果与完成审计记录。

### 关联设计

- `docs/contracts/operation-contract.md`
- `docs/processes/standard-process-registry.md`
- `docs/workflows/feedback-loop.md`
- `docs/architecture/full-design-implementation-design.md`
- `docs/architecture/resume-takeover-recovery-gate-design.md`
