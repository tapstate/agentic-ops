# DE-006 任务完成审计与反馈分析

> **目标故事合同。** 本文不维护当前完成度；以下命令和输出定义未来验收合同，执行前必须以能力目录为准。内部 `task` / `report` 原语与 Jira 评论不能冒充完整审计或研发员释放。

AO-11 已新增 `task_run_audit` 的本地协议基线，用于真实任务推进到 PR 审查时保存授权清单、受信事实、事件链和强制复盘。它只覆盖本文目标合同的一部分；`write_evidence`、`release_agent` 与聚合反馈报告仍以能力目录中的缺口状态为准。

作为研发工程师，
我希望 AIAgent 完成、阻塞或交接一个任务时立即提交任务级审计记录，
以便团队能按任务事实源追踪 AI 员工执行情况，并在需要时分析阻塞点、重复问题和 AgenticOps 改进机会。

### 触发方式

```sh
ao-work capability show write_evidence
ao-work capability show release_agent
ao-work capability show feedback_bundle
```

或自然语言：

```text
提交 TAP-123 本次执行的任务审计记录。
按需分析 tapstate 工作空间最近的 AI 执行记录，并给出 AgenticOps 改进建议。
```

### 前置条件

- 工作空间中存在对应 `agentic_run_id` 的事件日志和证据。
- Jira 卡片、审计服务或目标仓库证据链可作为任务级审计记录提交目标。
- 事件日志使用安全摘要，不包含 secrets、原始敏感日志、完整 Jira 描述或敏感代码片段。

### 主流程

1. AIAgent 在完成、阻塞或交接节点整理当前 `agentic_run_id` 的任务审计摘要。
2. CLI 写入证据，并在完成或交接后写入中文终态 Comment、关闭本地 run；不清理不存在的 Agentic Jira 字段。
3. AIAgent 将审计记录提交到 Jira 卡片、审计服务或目标仓库证据链。
4. 需要诊断时，CLI 生成脱敏 `feedback bundle`。
5. 需要复盘时，CLI 按需生成 `feedback report`。
6. AIAgent 基于任务审计记录和按需报告输出改进建议。
7. 改进建议进入 `Observation -> Proposal -> Accepted Change` 流程。
8. 未经人工确认，不自动修改 AgenticOps 源头规则。
9. 用户触发“AO问题反馈”时，按 `ao_problem_feedback/v1` 输出来源、版本、上下文、实际/期望、最小复现、影响、外部事实、证据、人工介入、假设、修复载体、最小回归、验收、缺失事实和脱敏声明；逐项标记已提供、不适用或未获取。

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
  "agentic_next_action": "review_proposals"
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
- 真实任务推进到 PR 审查时，必须由 Runtime 采集 Jira、Git、GitHub 和验证事实；AI 手写的自报事件不能形成正式通过结论。
- 结果必须逐项审查自动化缺口、人工摩擦、输出质量和不合理流程，并完整记录人工干预、等待、失败、重试及排序后的优化候选。
- AO 问题只有来源与版本可追溯、实际/期望明确、具备复现或等价 fixture、最小回归、验收和脱敏声明时才能标记 `repair_readiness: ready`；否则必须标记 `needs_information` 并输出最小补齐动作。

### 保护行为

- AIAgent 完成、阻塞或交接任务时必须提交任务级审计记录。
- `release-agent` 实现后必须记录终态 Comment 的回读结果和本地 run 收口状态。
- 本地反馈报告不能替代 `.agentic-ops/tasks/<ISSUE-KEY>/` 中的任务审计记录；Jira 回写关键结论和稳定引用，审计服务属于后续可选扩展。
- 反馈分析只能形成改进建议，不能自动修改 AgenticOps 源头规则。
- developer 只上报问题，不在业务工作空间或稳定安装中自修；AgenticOps 源头 `developer/**` 只由 maintainer 工作面维护。
- 审计记录和反馈报告不得包含 secrets 或敏感原始内容。
- `task_run_audit` 的授权清单、Runtime 受信事实、hash-chain、禁止动作检查和复盘不得被普通自然语言总结替代。

### 审核问题

- 任务最终状态是完成、阻塞还是交接。
- 审计记录最终写入 Jira 卡片、审计服务还是目标仓库证据链。
- 终态 Comment 是否已回读确认，本地 run 是否已收口；失败时是否保留明确原因。
- 反馈报告是否只是按需分析，而不是任务完成主路径。
- 改进建议是否已经过人工确认。

### 验收证据

- `ao-work capability show write_evidence` 与 `release_agent` 输出的当前状态；能力实现后再补目标命令输出。
- 任务级审计记录或 Jira 中文工作日志。
- `ao-work capability show feedback_report` 输出的当前状态；能力实现后再补目标命令输出。
- 脱敏反馈包和人工确认记录。

### 关联设计

- `docs/workflows/feedback-loop.md`
- `docs/templates/evidence-templates.md`
- `docs/runtime/problem-resolution-and-update.md`
- `docs/runtime/ao-problem-feedback-reporting.md`
- `docs/project-rules.md`
