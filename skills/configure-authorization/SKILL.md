---
name: configure-authorization
description: Safely inspect, set, modify, remove, and verify the single Jira account owned by an AgenticOps business-project workspace without exposing API tokens. Use when workspace Jira credentials are missing, invalid, or need rotation before a project task can run.
---

# 配置授权

使用 `agentic-cli auth jira` 管理授权，不要让用户手工编辑 `.env`，也不要在聊天、命令参数或报告中接收 token。

一个业务项目 AgenticOps 工作空间是一名研发员，只维护一个 Jira 账户。`~/.agentic-ops` 共享安装不保存研发员身份，不同工作空间不得自动继承或拼接凭证。允许模式：`source_maintenance`、`project_execution`。

## 操作流程

1. 运行 `auth jira show` 查看研发员账户状态和来源。
2. 交互式执行设置或修改：

```sh
agentic-cli auth jira set
```

3. 运行 `auth jira verify` 验证当前项目站点、身份和 API 能力。
4. 只有 `verified=true` 才继续真实 Jira 任务操作。

Connection 从当前项目 Profile 推导；安装中只有一个 Connection 时自动选择。只有多个站点且当前工作空间尚未绑定时，才查看 `auth jira list` 并使用高级参数 `--connection-id`。

## 非交互设置

email 可以使用 `--email`；token 只能从标准输入传入：

```sh
printf '%s\n' "$JIRA_TOKEN" | agentic-cli auth jira set \
  --email <jira-email> \
  --token-stdin
```

不要把 token 直接写入命令行参数。不要在自动化日志中打印输入管道。

## 修改与删除

- 重复执行 `set` 只更新明确提供的字段，保留其它授权字段。
- 使用 `remove --field email|token|all` 删除当前工作空间研发员账户的指定字段。
- 删除后再次执行 `show`，确认账户状态。

输出只允许包含布尔状态、脱敏 email、配置来源、Jira 用户标识和字段数量；不得包含 token、Authorization header 或原始认证响应。
