---
name: task-state
description: Use when an AIAgent needs to initialize or inspect the local durable state for an approved Jira task before later workflow operations are available.
allowed_modes:
  - project_execution
---

# 任务状态

本 Skill 只在业务项目 AI 工作空间的 `project_execution` 模式使用。

## 初始化

在 Jira 任务身份和工作空间绑定已经确认后，调用：

```sh
agentic-cli \
  --workspace-root <project-ai-workspace> \
  --mode project_execution \
  task init \
  --connection-id <connection-id> \
  --jira-issue-id <jira-issue-id> \
  --issue-key <ISSUE-KEY> \
  --project-key <PROJECT> \
  --agentic-run-id <agentic-run-id>
```

只有 `ok=true` 且 `status=completed` 时才可继续。`workspace_mode_mismatch`、`task_identity_mismatch` 或 `task_lock_timeout` 必须停止并按 `required_human_action` 处理。

## 读取

需要恢复或确认本地事实时调用：

```sh
agentic-cli \
  --workspace-root <project-ai-workspace> \
  --mode project_execution \
  task inspect \
  --issue-key <ISSUE-KEY>
```

不得直接修改 Runtime 管理的 JSON / NDJSON，不得用 `.superpowers/` 内容替代任务状态。
