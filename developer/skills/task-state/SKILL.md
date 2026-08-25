---
name: task-state
description: Use when an AIAgent needs to initialize or inspect the local durable state for an approved Jira task before later workflow operations are available.
metadata:
  workplane: developer
---

# 任务状态

本 Skill 只在业务项目 AI 工作空间的 `developer` 工作面使用。

## 初始化

在 Jira 任务身份和工作空间绑定已经确认后，调用：

```sh
ao-work \
  --workspace-root <project-ai-workspace> \
  task init \
  --connection-id <connection-id> \
  --jira-issue-id <jira-issue-id> \
  --issue-key <ISSUE-KEY> \
  --project-key <PROJECT> \
  --agentic-run-id <agentic-run-id>
```

只有 `ok=true` 且 `status=completed` 时才可继续。`workplane_mismatch`、`task_identity_mismatch` 或 `task_lock_timeout` 必须停止并按 `required_human_action` 处理。

所有 Issue Key、`agentic_run_id` 和其它路径组成标识都必须原样交给 Runtime 校验。不得使用 `../`、绝对路径或 symlink 改写 `.agentic-ops/tasks`、`.agentic-ops/locks`、任务目录或报告目录。

## 读取

需要恢复或确认任务事实时调用：

```sh
ao-work \
  --workspace-root <project-ai-workspace> \
  task inspect \
  --issue-key <ISSUE-KEY>
```

该命令同时返回本地持久任务状态与受控 Jira `task_facts`：其中包含脱敏的 Description 任务目标/必要执行线索、评论补充线索和候选仓库/分支提案。它不会输出或持久化原始 Description 或评论正文；读取失败时必须按稳定失败码停止，不得用聊天上下文补全。

不得直接修改 Runtime 管理的 JSON / NDJSON，不得用 `.superpowers/` 内容替代任务状态。
