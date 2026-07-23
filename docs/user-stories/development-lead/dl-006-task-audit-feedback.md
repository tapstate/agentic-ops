# DL-006 任务完成审计与反馈分析

作为研发负责人，
我希望 AIAgent 完成、阻塞或交接一个任务时立即提交任务级审计记录，
以便团队能按任务事实源追踪 AI 员工执行情况，并在需要时分析阻塞点、重复问题和 AgenticOps 改进机会。

### 触发方式

```sh
agentic-cli write-evidence --workspace tapstate --run-id <run_id>
agentic-cli release-agent --workspace tapstate --run-id <run_id> --issue-key TAP-123 --completion-evidence evidence.md
agentic-cli feedback bundle --workspace tapstate --run-id <run_id> --redact
agentic-cli feedback report --workspace tapstate --date 2026-07-21
agentic-cli feedback analyze --workspace tapstate --date 2026-07-21
agentic-cli feedback propose --workspace tapstate --date 2026-07-21
```

或自然语言：

```text
提交 TAP-123 本次执行的任务审计记录。
按需分析 tapstate 工作空间最近的 AI 执行记录，并给出 AgenticOps 改进建议。
```

### 前置条件

- 工作空间中存在对应 `run_id` 的事件日志和证据。
- Jira 卡片、审计服务或目标仓库证据链可作为任务级审计记录提交目标。
- 事件日志使用安全摘要，不包含 secrets、原始敏感日志、完整 Jira 描述或敏感代码片段。

### 主流程

1. AIAgent 在完成、阻塞或交接节点整理当前 `run_id` 的任务审计摘要。
2. CLI 写入证据，并在完成或交接后执行 `release-agent` 清理 `current_agent_id`。
3. AIAgent 将审计记录提交到 Jira 卡片、审计服务或目标仓库证据链。
4. 需要诊断时，CLI 生成脱敏 `feedback bundle`。
5. 需要复盘时，CLI 按需生成 `feedback report`。
6. AIAgent 基于任务审计记录和按需报告输出改进建议。
7. 改进建议进入 `Observation -> Proposal -> Accepted Change` 流程。
8. 未经人工确认，不自动修改 AgenticOps 源头规则。

### 输出

```json
{
  "ok": true,
  "operation": "feedback_report",
  "workspace": "tapstate",
  "date": "2026-07-21",
  "runs": 8,
  "succeeded": 5,
  "blocked": 2,
  "failed": 1,
  "report": "<project-ai-workspace>/.agentic-ops/feedback/reports/2026-07-21.md",
  "next_action": "review_proposals"
}
```

### 失败处理

- 没有事件日志时，提示检查工作空间反馈日志。
- 发现疑似敏感内容时，停止生成报告并提示需要脱敏。
- 发现重复失败码时，生成 proposal，但不自动修改源头规则。

### 验收标准

- 完成、阻塞或交接时能提交任务级审计记录。
- 能按需按 `workspace`、时间范围、失败码或任务类型生成反馈分析报告。
- 报告包含成功、失败、阻塞、人工确认点和重复问题。
- 报告不包含 secrets 或敏感原始内容。
- 写入 Jira 的工作日志必须使用中文。
- 改进建议必须经过人工确认后才能进入 AgenticOps 源头仓库。

### 保护行为

- AIAgent 完成、阻塞或交接任务时必须提交任务级审计记录。
- `release-agent` 完成清理后必须记录 `current_agent_id` 清理状态。
- 本地反馈报告不能替代 Jira 卡片、审计服务或目标仓库证据链中的任务审计记录。
- 反馈分析只能形成改进建议，不能自动修改 AgenticOps 源头规则。
- 审计记录和反馈报告不得包含 secrets 或敏感原始内容。

### 审核问题

- 任务最终状态是完成、阻塞还是交接。
- 审计记录最终写入 Jira 卡片、审计服务还是目标仓库证据链。
- `current_agent_id` 是否已清理或保留了未清理原因。
- 反馈报告是否只是按需分析，而不是任务完成主路径。
- 改进建议是否已经过人工确认。

### 验收证据

- `agentic-cli write-evidence --workspace <name> --run-id <run_id>` 输出。
- `agentic-cli release-agent --workspace <name> --run-id <run_id> --issue-key <issue>` 输出。
- 任务级审计记录或 Jira 中文工作日志。
- `agentic-cli feedback report --workspace <name> --date <date>` 输出。
- 脱敏反馈包和人工确认记录。

### 关联设计

- `docs/workflows/feedback-loop.md`
- `docs/templates/evidence-templates.md`
- `docs/runtime/problem-resolution-and-update.md`
- `docs/project-rules.md`
