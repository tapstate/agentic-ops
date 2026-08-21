---
name: initialize-project-workspace
description: Initialize or repair an AgenticOps business-project workspace from the current developer installation authorization and a Project Profile. Use when creating a workspace, reinitializing a schema-v3 workspace, or resolving deterministic workspace preflight blockers.
metadata:
  workplane: developer
---

# 初始化业务项目工作空间

始终使用当前 developer 安装的 Python Runtime。新工作空间先完成安装级 `ao-work auth`，再运行 `ao-work workspace init`；不要直接创建或修改根 `AGENTS.md`、`.agentic-ops/agent.json`、Profile overlay、Skill 副本或工作空间索引，也不要在工作空间创建授权 `.env`。

## 标准流程

1. 查看当前安装授权：

```sh
ao-work auth --show
```

未配置时运行 `ao-work auth`。Token 只能通过隐藏输入或安全标准输入进入 Runtime；不要从环境、其它安装、其它工作空间或历史聊天发现凭证。

2. 在目标业务项目 AI 工作空间根目录运行：

```sh
ao-work workspace init
```

3. 确认 Runtime 展示的安装级 `agent_id`、脱敏 Jira 账户、Git/GitHub 执行身份、Project Profile、Jira 站点和 Project Key、源码池与仓库。Project 与流程事实来自 Profile，不逐项猜测；身份和凭证来自当前安装，不在 init 中重新输入。

4. Runtime 执行工作空间边界、受管路径、已有配置、安装身份指纹、Profile/Connection、Jira 身份与 Project 访问、源码池和 Git 仓库访问预检。普通 `workspace preflight` 不能确认重绑；覆盖已有配置只能由指导员显式重新运行并确认 `workspace init`。

5. 初始化成功结果固定为 schema v4。`.agentic-ops/agent.json` 只保存项目事实和 `install_identity_ref`；`agent_id`、Jira accountId、Git 执行身份与 token 均不写入工作空间。Runtime 把 developer Skill 作为普通文件副本写入 `.agents/skills/`，不得创建指向安装根的 symlink。

6. 只有 `ok=true`、`preflight_status=passed` 和 `post_preflight_status=passed` 都成立后，才进入 Jira 任务操作。`skipped_repositories` 非空时明确提示缺少仓库权限，不得声称源码池全部就绪。

## 非交互模式

先单独完成安装授权，再初始化项目：

```sh
printf '%s\n' "$JIRA_TOKEN" | ao-work auth \
  --agent-id <agent-id> \
  --jira-email <email> \
  --git-name <git-name> \
  --git-email <git-email> \
  --github-login <github-login> \
  --token-stdin \
  --non-interactive

ao-work workspace init \
  --non-interactive \
  --project <profile> \
  --source-pool-root <pool-root> \
  --confirm
```

`--workspace-root <路径>` 是顶层参数，必须放在 `workspace` 之前。`workspace init` 不接受 `--agent-id`、Jira email/token 或 Git/GitHub 身份参数。

## 阻断处理

- `install_identity_missing` / `install_identity_invalid` / `jira_credentials_missing`：停止，运行当前安装的 `ao-work auth`。
- `install_identity_drift`：停止，核对是否用了错误安装；不得修改工作空间字段绕过。
- `workspace_jira_identity_upgrade_required`：schema v3 已失效；先重新授权，再由指导员确认重新初始化。Runtime 不自动读取、复制或删除旧工作空间 `.env`。
- `jira_workspace_mismatch` / `workspace_project_identity_drift`：核对 Profile、Connection 和 Project；普通 preflight 不得代替重绑确认。
- `workspace_managed_path_unsafe` / `workspace_index_path_unsafe`：移除越界路径或 symlink，检查身份或状态是否被篡改。
- `git_url_rewrite_forbidden` / `source_repository_mismatch`：移除 URL rewrite，确保 raw/effective fetch/push 精确指向目标 GitHub 仓库。
- `source_repository_access_failed`：修复 GitHub 登录、SSH key、网络或权限后重试。
- `existing_config_confirmation_required`：先核对已有配置；非交互覆盖必须显式传入 `--confirm-existing-config`。

任何阻断都不得通过手工补写 `agent.json`、工作空间 `.env` 或索引伪造成功。
