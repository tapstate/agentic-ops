# DL-005 恢复接管任务

作为研发负责人，
我希望能恢复一个已接管但未完成的任务，
以便 AIAgent 继续同一个 `run_id` 的上下文，而不是重新开始或混淆多次执行记录。

### 触发方式

```sh
agentic-cli resume-takeover --run-id TAP-123-takeover-20260721103012-a8f3 --workspace tapstate
```

或自然语言：

```text
恢复 TAP-123 上次的接管任务。
```

### 前置条件

- 已存在接管记录。
- `run_id` 对应的 `issue`、`workspace`、`task_type`、`current_stage` 和目标仓库可验证。
- 本地工作区仍能定位到相关代码状态。

### 主流程

1. AIAgent 调用 `resume_takeover` 操作。
2. CLI 读取 `run_id` 对应的 run summary 和 events。
3. CLI 校验当前 `workspace`、`issue`、负责人、目标仓库和本地分支状态。
4. CLI 返回上次阶段、已完成动作、失败原因、下一步建议。
5. AIAgent 向研发负责人简短说明恢复点。
6. AIAgent 从恢复点继续执行，而不是重新生成新的接管记录。

### 输出

```json
{
  "ok": true,
  "operation": "resume_takeover",
  "workspace": "tapstate",
  "issue_key": "TAP-123",
  "run_id": "TAP-123-takeover-20260721103012-a8f3",
  "previous_stage": "verification_failed",
  "current_stage": "verification_failed",
  "next_action": "fix_and_verify"
}
```

### 失败处理

- `run_id` 不存在时，提示可恢复的最近 run。
- 当前 `workspace` 与 `run_id` 不匹配时，拒绝恢复。
- 本地代码状态不一致时，要求研发负责人确认。
- 如果上次失败原因属于人工确认点，AIAgent 不能自动继续。

### 验收标准

- 恢复任务不会创建新的 `run_id`。
- 恢复前必须校验 `workspace`、`issue`、负责人和目标仓库一致。
- AIAgent 能说明从哪个阶段恢复。
- 恢复过程继续写入同一个 run 的事件日志。

### 保护行为

- 恢复接管必须复用已有 `run_id`，不能创建新 `run_id`。
- 恢复前必须校验 `workspace`、`issue`、负责人、目标仓库和本地代码状态。
- 上次停在人工确认点时，AIAgent 不能自动继续。
- 恢复过程必须继续写入同一个 run 的事件日志。

### 审核问题

- `run_id` 是否存在且与当前工作空间匹配。
- 当前 Jira 卡片负责人和目标仓库是否仍一致。
- 本地代码状态是否允许继续，是否需要研发负责人确认。
- AIAgent 是否清楚说明 previous stage、current stage 和 next action。

### 验收证据

- `agentic-cli resume-takeover --run-id <run_id> --workspace <name>` 输出。
- 同一 `run_id` 的 run summary 和 events。
- 输出中的 `previous_stage`、`current_stage` 和 `next_action`。
- 恢复失败时的结构化失败记录。

### 关联设计

- `docs/contracts/operation-contract.md`
- `docs/processes/standard-process-registry.md`
- `docs/workflows/feedback-loop.md`
- `docs/architecture/full-design-implementation-design.md`
