---
name: configure-authorization
description: Safely inspect, set, modify, remove, and verify AgenticOps Jira authorization without exposing API tokens. Use when Jira credentials are missing, invalid, bound to the wrong scope, or need rotation before a project task can run.
---

# 配置授权

使用 `agentic-cli auth jira` 管理授权，不要让用户手工编辑 `.env`，也不要在聊天、命令参数或报告中接收 token。

允许模式：`source_maintenance`、`project_execution`。

## 操作流程

1. 运行 `auth jira list` 查看可选 Connection。
2. 运行 `auth jira show --connection-id <id>` 查看有效授权状态和来源。
3. 根据复用范围选择 scope：
   - `user`：同一用户跨项目复用，写入 `~/.agentic-ops/user/.env`。
   - `workspace`：只供当前业务项目工作空间使用，覆盖 user scope。
4. 交互式执行设置或修改：

```sh
agentic-cli auth jira set \
  --connection-id <id> \
  --scope user \
  --interactive
```

5. 运行 `auth jira verify --connection-id <id>` 验证站点、身份和 API 能力。
6. 只有 `verified=true` 才继续真实 Jira 任务操作。

## 非交互设置

email 可以使用 `--email`；token 只能从标准输入传入：

```sh
printf '%s\n' "$JIRA_TOKEN" | agentic-cli auth jira set \
  --connection-id <id> \
  --scope workspace \
  --email <jira-email> \
  --token-stdin
```

不要把 token 直接写入命令行参数。不要在自动化日志中打印输入管道。

## 修改与删除

- 重复执行 `set` 只更新明确提供的字段，保留其它授权字段。
- 使用 `remove --field email|token|all` 删除指定 scope 的授权。
- 删除后再次执行 `show`，确认 effective scope 是否回退到其它来源。

输出只允许包含布尔状态、脱敏 email、配置来源、Jira 用户标识和字段数量；不得包含 token、Authorization header 或原始认证响应。
