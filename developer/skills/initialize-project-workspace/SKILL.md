---
name: initialize-project-workspace
description: Initialize or repair an AgenticOps business-project workspace by confirming the developer identity, Jira Project Profile, workspace-scoped Jira account, source repository, and deterministic preflight results. Use when a company employee instructor creates a developer workspace, authorization is incomplete, preflight blocks task execution, or an existing partial workspace needs safe repair.
metadata:
  workplane: developer
---

# 初始化业务项目工作空间

始终使用已安装的 Python Runtime 入口，由 `ao_work` 模块位置自定位 developer managed clone；不得传入或从环境选择其它安装根。不要直接创建或修改根 `AGENTS.md`、`.agentic-ops/agent.json`、授权 `.env`、Profile overlay 或工作空间索引；根 `AGENTS.md` 若存在必须是当前工作空间内的普通文件，不得使用 symlink。

## 标准流程

1. 在目标业务项目工作空间根目录运行：

```sh
ao-work workspace init
```

2. 引导公司员工指导员确认一次性工作空间配置：

- `agent_id`：默认是规范化后的纯小写主机名；只能包含 `[0-9A-Za-z_-]`。
- Jira 项目空间：Project Profile、Jira 站点和 Project Key。
- 当前研发员账户：只显示脱敏 email；token 使用隐藏输入。
- 默认源码仓库和本地源码目录。
- 一次性执行身份：Git author/committer 姓名邮箱与 GitHub actor login；默认只来自本向导已确认的 `agent_id` 和 Jira email，不读取全局 Git/GitHub 身份。

Jira 站点、Project Key、状态/字段映射和默认仓库来自 Project Profile，不逐项询问；每个任务的 Issue ID、经办人、状态和描述由授权后的 Jira 读取，run ID、摘要和时间由 Runtime 生成。后续任务只审查 AI 提出的计划、范围、验证、权限和高风险动作。

3. 只有初始化摘要正确时才确认。Runtime 随后执行只读预检，包括工作空间边界、全部受管路径、已有配置、`agent_id` 冲突、Profile、授权身份、Jira Project 访问和 Git 仓库访问。普通 `workspace preflight` 不能确认或覆盖漂移；重绑只能由指导员显式执行并确认 `workspace init`。
   初始化还把 developer Skill 作为普通文件副本写入当前工作空间 `.agents/skills/`，供 Codex 按仓库范围自动发现；规则正文进入当前 `AGENTS.md`，标准资产由 `ao-work` 从受信安装根解析。不得创建指向安装根的 Skill symlink，也不得把 `developer/...` 当作业务仓库相对路径。后续 `workspace preflight` 必须阻断 Skill 缺失、内容漂移、额外资产或 maintainer 污染。
4. `ok=true`、`preflight_status=passed` 且 `post_preflight_status=passed` 后，只能检查用户显式给出的 Jira 任务。当前 `list_tasks` 是能力缺口，不得声称可以自动拉取研发员名下待办。
5. 完成配置必须是 schema v3，并固化 `connection_id`、严格 HTTPS Jira 站点根、`jira_site`、实时验证的 `jira_account_id`、Project Key、默认仓库和源码规范路径。effective Profile 的这些值每次都必须与 `agent.json` 相同。业务仓库只接受精确 GitHub SCP/SSH/HTTPS URL，raw/effective fetch/push 全部一致，且不得配置 Git URL rewrite。

## 阻断处理

- `agent_id_conflict`：停止，让指导员修改当前研发员的 `agent_id`，不得复用另一工作空间身份。
- `jira_credentials_missing` 或 `jira_authorization_failed`：在当前初始化入口补全同一账户的 email 和 token；不得跨工作空间继承凭证。
- `jira_workspace_mismatch`：核对 Project Profile、Connection 和 Project Key，不得临场改写映射。
- `workspace_jira_identity_upgrade_required` 或 `jira_workspace_identity_drift`：停止读取凭证和访问 Jira，重新初始化并确认站点与账户绑定。
- `workspace_project_identity_drift`：停止执行；普通 preflight 不得代替指导员确认重绑。
- `workspace_managed_path_unsafe` 或 `workspace_index_path_unsafe`：移除越界路径或 symlink，核对是否发生身份/状态篡改。
- `git_url_rewrite_forbidden` 或 `source_repository_mismatch`：移除 URL rewrite，核对 raw/effective fetch/push 都直接指向精确 GitHub 仓库。
- `source_repository_access_failed`：修复 GitHub 登录、SSH key、网络或仓库权限后重试。
- `existing_config_confirmation_required`：先核对已有配置；非交互模式只有明确提供 `--confirm-existing-config` 才能覆盖。

阻断结果出现时，不手工补写 `agent.json` 伪造初始化成功。

## 非交互模式

脚本或 CI 必须明确提供身份、Profile 和确认，并通过安全标准输入传 token。`--workspace-root` 是 `ao-work` 顶层全局参数（默认当前目录 `.`，必须放在 `workspace` 之前）；在目标工作空间目录内运行时省略即可：

```sh
printf '%s\n' "$JIRA_TOKEN" | ao-work workspace init \
  --non-interactive \
  --project <profile> \
  --agent-id <agent-id> \
  --source-pool-root <pool-root> \
  --jira-email <email> \
  --git-name <git-author-and-committer-name> \
  --git-email <git-author-and-committer-email> \
  --github-login <github-actor-login> \
  --token-stdin \
  --confirm
```

从其它目录初始化指定工作空间时，把 `--workspace-root <路径>` 放在 `ao-work` 之后、`workspace` 之前。

必填项（非交互）：

- `--non-interactive` 与 `--confirm`：确认初始化摘要，缺一不可。
- `--project <profile>`：Project Profile id（如 `tapdata`）；Jira 站点、Project Key 与默认仓库无 CLI 参数，全部取自该 Profile。
- `--agent-id <id>`：只允许 `[0-9A-Za-z_-]`。
- `--jira-email` 与 `--token-stdin`：必须成对；token 从 stdin 第一行读取。已用 `ao-work install auth set` 配置安装目录凭证时可省略。
- 池根必配：`--source-pool-root` 或 `~/.agentic-ops/user/config.yaml` 的 `source_pool_root` 二选一，否则 `source_pool_root_invalid` 阻断。池根目录不存在时 init 自动创建（preflight 只读校验，apply 创建并写容器 README）。

可选：

- `--source-root`：缺省为池模式（源码语义 = 池根）；显式传入非池根路径则为普通源码模式。
- `--git-name` / `--git-email` / `--github-login`：all-or-none；已用 `ao-work install identity set` 配置安装身份时可省略。
- `--confirm-existing-config`：覆盖已有不同完整配置时需要。

不得把 token 放入命令参数、日志、报告或聊天内容。
