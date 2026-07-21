# 反馈闭环

## 1. 目的

Feedback Loop 是 AgenticOps 的持续改进机制，用于分析 AIAgent 每天的执行日志，识别失败、阻塞、重复人工确认和规则缺口，并生成改进建议。

第一阶段反馈通道只做分析和建议，不允许 AIAgent 自动修改 AgenticOps 源头规则。

## 2. 流程

```text
Go CLI 执行 operation
-> 产生结构化事件日志
-> 每天按 workspace 汇总
-> AIAgent 分析失败、卡点、重复人工确认、规则缺口
-> 生成改进建议
-> 人确认后更新 AgenticOps 规则 / 手册 / contracts / Go CLI
```

## 3. 事件位置

事件日志必须写入具体项目 AI 工作空间：

```text
<project-ai-workspace>/
  .agentic-ops/
    runs/
      2026-07-21/
        TAP-123-takeover-20260721103012-a8f3/
          events.ndjson
          summary.json
          evidence.md
    feedback/
      daily/
        2026-07-21.md
        2026-07-21.json
```

`~/.agentic-ops` 不保存具体任务运行日志。

## 4. 事件结构

事件日志使用 NDJSON，每条事件只记录安全摘要。

```json
{
  "timestamp": "2026-07-21T10:30:12+08:00",
  "workspace": "tapstate",
  "run_id": "TAP-123-takeover-20260721103012-a8f3",
  "issue_key": "TAP-123",
  "task_type": "task_takeover",
  "operation": "takeover_task",
  "current_stage": "takeover_gate",
  "next_action": "ask_owner",
  "ok": false,
  "code": "missing_target_repo",
  "duration_ms": 842,
  "human_gate": false,
  "requires_human_action": true,
  "safe_message": "Jira issue 缺少目标仓库信息"
}
```

不得记录：

- secrets
- tokens
- private keys
- 原始敏感日志
- 完整 Jira 描述
- 敏感代码片段

## 5. 反馈命令

第一阶段建议 operation：

```sh
agent-task-ops feedback collect --workspace tapstate --date 2026-07-21
agent-task-ops feedback analyze --workspace tapstate --date 2026-07-21
agent-task-ops feedback report --workspace tapstate --date 2026-07-21
agent-task-ops feedback propose --workspace tapstate --date 2026-07-21
```

## 6. 报告内容

每日反馈报告应包含：

- runs 总数。
- 成功数。
- 失败数。
- 阻塞数。
- 最常见失败码。
- 人工确认点。
- 重复问题。
- 改进建议。

## 7. 变更门禁

反馈进入 AgenticOps 源头规则前必须经过：

```text
Observation -> Proposal -> Accepted Change
```

AIAgent 可以生成 proposal，但不得未经人工确认直接修改项目规则、AI 员工手册、Operation Contract、Workflow Profile 或 CLI Runtime。
