---
name: configure-authorization
description: Configure, update, or safely inspect the installation-scoped developer identity and Jira credential used by ao-work. Use when developer installation authorization is missing, invalid, rotated, or needs masked inspection before workspace initialization or task execution.
metadata:
  workplane: developer
---

# 配置安装授权

只使用顶层 `ao-work auth` 管理当前 developer 安装的研发员身份、Git 提交身份、Git SSH 远端授权、GitHub CLI 授权和 Jira 凭证。三类 Git/GitHub 事实必须分别回读；`gh api user` 不能表述为 SSH push actor 证明。不要调用已删除的 `ao-work install identity|auth` 或 `ao-work auth jira`，不要手工编辑安装目录文件，也不要在聊天、命令参数、日志或报告中接收 token。

一个 developer 安装代表一名研发员；同一安装下的多个业务项目工作空间继承同一身份和凭证，但各自保存独立 Project Profile 与 `install_identity_ref`。本 Skill 只属于 `developer` 工作面。

## 操作流程

1. 只需查看状态时运行：

```sh
ao-work auth --show
```

输出只允许包含配置状态、`agent_id`、脱敏 email、Git 姓名、GitHub login、授权模式、安装授权路径状态和公钥指纹，不返回 token、私钥或完整敏感配置。

2. 首次配置或更新时，在终端运行：

```sh
ao-work auth
```

Runtime 引导填写 `agent_id`、Jira email、Git author/committer 姓名与 email、GitHub login，并明确选择 `global` 或 `installation`：

- `global` 只复用并校验机器现有 Git/SSH/`gh`，输入 `REUSE-GLOBAL` 后继续；不得修改全局配置。
- `installation` 保持全局授权不变，在当前安装 `user/ssh/` 与 `user/gh/` 建立隔离授权，输入 `USE-INSTALLATION` 后通过 GitHub CLI 官方设备流程登录。SSH 固定走 `ssh.github.com:443`，只使用安装私钥且不回退 SSH Agent。

Runtime 通过隐藏输入接收 Jira token。首次 `installation` GitHub 登录必须交互完成，不得用 Jira token、环境变量或全局 `gh` 静默代替。

3. 自动化必须提供完整身份参数，token 只能经标准输入：

```sh
printf '%s\n' "$JIRA_TOKEN" | ao-work auth \
  --agent-id <agent-id> \
  --jira-email <jira-email> \
  --git-name <git-name> \
  --git-email <git-email> \
  --github-login <github-login> \
  --execution-auth-mode global \
  --token-stdin \
  --non-interactive
```

该示例只适用于机器全局 Git/`gh` 已匹配候选身份。安装级授权首次配置必须改在终端交互执行；后续非交互调用只允许复用已经完整且回读一致的安装授权。

4. 发现已有不同授权时，先展示脱敏 `existing`、`candidate` 与 `change_digest`。只有候选完全相同且显式传入 `--confirm-replace-authorization <change_digest>` 才可更新受管身份/配置。不同安装 `gh` 账户、既有私钥、自定义 `core.sshCommand`、非受管路径或宽松权限必须失败关闭，普通确认参数不能放行。

5. 授权配置本身不猜测 Project，也不以独立命令探测 Jira。`workspace init` 或任务入口使用当前 Project Profile 回读 Jira 身份和访问能力；只有这些校验通过才继续真实任务。模式、执行身份或公钥指纹变化后必须由指导员明确重绑既有工作空间。

## 阻断处理

- `interactive_terminal_required`：切换到终端运行，或提供完整非交互参数。
- `install_identity_incomplete` / `install_identity_invalid`：补齐或修正身份字段，不从主机名、全局 Git、其它安装、其它工作空间或历史聊天猜测。
- `install_execution_authorization_upgrade_required`：旧身份缺少模式；明确选择 `global` 或 `installation`，不得静默默认。
- `global_authorization_identity_mismatch`：切换机器现有 Git/`gh` 账户，或选择 `installation`；Runtime 不修改全局配置。
- `installation_github_interactive_authorization_required`：在终端完成安装目录隔离的 GitHub 官方登录；不得注入 GitHub token。
- `existing_authorization_change_confirmation_required`：审查脱敏差异并精确绑定 `change_digest`；不得用任意非空文本代替。
- `existing_authorization_unmanaged_conflict` / `existing_authorization_permissions_unsafe`：停止并人工核对；Runtime 不接管非受管配置、不轮换既有私钥、不覆盖不同 `gh` 账户，也不静默收紧机器已有路径权限。
- `authorization_token_empty` / `authorization_token_invalid`：通过隐藏输入或标准输入重新提供 token。
- `install_user_dir_invalid` / `install_identity_write_failed`：停止，修复当前安装 `user/` 的路径或权限，不改写到工作空间。
- `workspace_jira_identity_upgrade_required`：先配置安装授权，再由指导员明确重新执行 `workspace init`；Runtime 不自动复制或删除旧工作空间 `.env`。

安装身份与 Jira 凭证写入当前安装的 `user/identity.yaml` 与 `user/.env`，权限必须为 `0600`。`installation` 模式的私有目录为 `0700`，SSH 私钥和受管配置为 `0600`；GitHub CLI 按其官方机制在隔离 `GH_CONFIG_DIR` 保存凭证。业务项目工作空间不得创建、读取或更新授权 `.env`，业务构建/测试子进程不得继承安装 SSH/`gh` 凭证。删除安装前必须提示用户按公钥指纹在 GitHub 撤销远端 SSH key。
