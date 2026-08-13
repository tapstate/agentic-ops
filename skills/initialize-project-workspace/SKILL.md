---
name: initialize-project-workspace
description: Initialize or repair an AgenticOps business-project workspace by confirming the developer identity, Jira Project Profile, workspace-scoped Jira account, source repository, and deterministic preflight results. Use when a company employee instructor creates a developer workspace, authorization is incomplete, preflight blocks task execution, or an existing partial workspace needs safe repair.
---

# 初始化业务项目工作空间

始终使用 Python Runtime 入口，不直接创建或修改 `.agentic-ops/agent.json`、授权 `.env`、Profile overlay 或工作空间索引。

## 标准流程

1. 在目标业务项目工作空间根目录运行：

```sh
agentic-cli workspace init
```

2. 引导公司员工指导员确认：

- `agent_id`：默认是规范化后的纯小写主机名；只能包含 `[0-9A-Za-z_-]`。
- Jira 项目空间：Project Profile、Jira 站点和 Project Key。
- 当前研发员账户：只显示脱敏 email；token 使用隐藏输入。
- 默认源码仓库和本地源码目录。

3. 只有初始化摘要正确时才确认。Runtime 随后执行只读预检，包括工作空间边界、已有配置、`agent_id` 冲突、Profile、授权身份、Jira Project 访问和 Git 仓库访问。
4. `ok=true`、`preflight_status=passed` 且 `post_preflight_status=passed` 后，才能继续拉取研发员名下 Jira 任务。

## 阻断处理

- `agent_id_conflict`：停止，让指导员修改当前研发员的 `agent_id`，不得复用另一工作空间身份。
- `jira_credentials_missing` 或 `jira_authorization_failed`：在当前初始化入口补全同一账户的 email 和 token；不得跨工作空间继承凭证。
- `jira_workspace_mismatch`：核对 Project Profile、Connection 和 Project Key，不得临场改写映射。
- `source_repository_access_failed`：修复 GitHub 登录、SSH key、网络或仓库权限后重试。
- `existing_config_confirmation_required`：先核对已有配置；非交互模式只有明确提供 `--confirm-existing-config` 才能覆盖。

阻断结果出现时，不手工补写 `agent.json` 伪造初始化成功。

## 非交互模式

脚本或 CI 必须明确提供身份、Profile 和确认，并通过安全标准输入传 token：

```sh
printf '%s\n' "$JIRA_TOKEN" | agentic-cli workspace init \
  --non-interactive \
  --project <profile> \
  --agent-id <agent-id> \
  --jira-email <email> \
  --token-stdin \
  --confirm
```

不得把 token 放入命令参数、日志、报告或聊天内容。
