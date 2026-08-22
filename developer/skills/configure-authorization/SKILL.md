---
name: configure-authorization
description: Configure, update, or safely inspect the installation-scoped developer identity and Jira credential used by ao-work. Use when developer installation authorization is missing, invalid, rotated, or needs masked inspection before workspace initialization or task execution.
metadata:
  workplane: developer
---

# 配置安装授权

只使用顶层 `ao-work auth` 管理当前 developer 安装的研发员身份、Git/GitHub 执行身份和 Jira 凭证。不要调用已删除的 `ao-work install identity|auth` 或 `ao-work auth jira`，不要手工编辑安装目录文件，也不要在聊天、命令参数、日志或报告中接收 token。

一个 developer 安装代表一名研发员；同一安装下的多个业务项目工作空间继承同一身份和凭证，但各自保存独立 Project Profile 与 `install_identity_ref`。本 Skill 只属于 `developer` 工作面。

## 操作流程

1. 只需查看状态时运行：

```sh
ao-work auth --show
```

输出只允许包含配置状态、`agent_id`、脱敏 email、Git 姓名和 GitHub login，不返回 token。

2. 首次配置或更新时，在终端运行：

```sh
ao-work auth
```

Runtime 引导填写 `agent_id`、Jira email、Git author/committer 姓名与 email、GitHub login，并通过隐藏输入接收 Jira token。重复执行就是独立重新授权。

3. 自动化必须提供完整身份参数，token 只能经标准输入：

```sh
printf '%s\n' "$JIRA_TOKEN" | ao-work auth \
  --agent-id <agent-id> \
  --jira-email <jira-email> \
  --git-name <git-name> \
  --git-email <git-email> \
  --github-login <github-login> \
  --token-stdin \
  --non-interactive
```

4. 授权配置本身不猜测 Project，也不以独立命令探测 Jira。`workspace init` 或任务入口使用当前 Project Profile 回读 Jira 身份和访问能力；只有这些校验通过才继续真实任务。

## 阻断处理

- `interactive_terminal_required`：切换到终端运行，或提供完整非交互参数。
- `install_identity_incomplete` / `install_identity_invalid`：补齐或修正身份字段，不从主机名、全局 Git、其它安装、其它工作空间或历史聊天猜测。
- `authorization_token_empty` / `authorization_token_invalid`：通过隐藏输入或标准输入重新提供 token。
- `install_user_dir_invalid` / `install_identity_write_failed`：停止，修复当前安装 `user/` 的路径或权限，不改写到工作空间。
- `workspace_jira_identity_upgrade_required`：先配置安装授权，再由指导员明确重新执行 `workspace init`；Runtime 不自动复制或删除旧工作空间 `.env`。

安装授权写入当前安装的 `user/identity.yaml` 与 `user/.env`，权限必须为 `0600`。业务项目工作空间不得创建、读取或更新授权 `.env`。
