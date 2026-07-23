# 反馈闭环

## 1. 目的

反馈闭环是 AgenticOps 的持续改进机制，用于分析 AIAgent 每天的执行日志，识别失败、阻塞、重复人工确认、有效经验和规则缺口，并生成改进建议。

第一阶段反馈通道只做分析和建议，不允许 AIAgent 自动修改 AgenticOps 源头规则。

## 2. 流程

```text
Go CLI 执行 operation
-> 产生结构化事件日志
-> 每天按 workspace 汇总
-> AIAgent 分析失败、卡点、重复人工确认、专业审查退回、重试、重做、有效经验和规则缺口
-> 生成改进建议
-> 人确认后更新 AgenticOps 规则 / 手册 / contracts / Go CLI
```

反馈闭环不只记录失败，也负责发现可固化经验。AIAgent 在具体环节中形成的有效处理方式，必须先以安全摘要进入事件、证据或 feedback proposal；只有重复出现、边界清晰、输入输出稳定后，才能建议升级为原子 operation、runbook、工作流配置、policy 或 template。

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
  "agent_id": "agent-local-7f31a2b",
  "run_id": "TAP-123-takeover-20260721103012-a8f3",
  "issue_key": "TAP-123",
  "assignee": "dev@example.com",
  "current_agent_id": "agent-local-7f31a2b",
  "task_type": "task_takeover",
  "task_class": "bug_fix",
  "process_id": "development_change_v1",
  "operation": "takeover_task",
  "current_stage": "takeover_gate",
  "next_action": "ask_owner",
  "ok": false,
  "code": "missing_target_repo",
  "duration_ms": 842,
  "human_gate": false,
  "requires_human_action": true,
  "review_gate": null,
  "review_decision": null,
  "retryable": false,
  "redo_from_stage": "takeover_gate",
  "current_agent_id_cleared": false,
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
agentic-cli feedback collect --workspace tapstate --date 2026-07-21
agentic-cli feedback analyze --workspace tapstate --date 2026-07-21
agentic-cli feedback report --workspace tapstate --date 2026-07-21
agentic-cli feedback propose --workspace tapstate --date 2026-07-21
```

## 6. 报告内容

每日反馈报告应包含：

- runs 总数。
- 成功数。
- 失败数。
- 阻塞数。
- 最常见失败码。
- 人工确认点。
- 专业审查退回。
- 重试次数和失败后仍未解决的节点。
- 重做来源阶段。
- 所有权冲突、assignee 变更和代理绑定丢失。
- 任务完成后未清理 `current_agent_id` 的记录。
- 重复问题。
- 可复用经验。
- 候选原子 operation。
- 改进建议。

## 7. 变更门禁

反馈进入 AgenticOps 源头规则前必须经过：

```text
Observation -> Proposal -> Accepted Change
```

AIAgent 可以生成 proposal，但不得未经人工确认直接修改项目规则、AI 员工手册、操作契约、工作流配置或 CLI 运行时。
