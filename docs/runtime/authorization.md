# AgenticOps developer 授权管理

## 边界

`ao-work auth` 是 developer 安装唯一的授权入口。一个 developer 安装代表一名研发员；同一安装下的多个业务项目工作空间继承同一安装身份与凭证，但各自绑定 Project Profile。maintainer 的 `ao-maint` 不读取、修改或验证这些业务授权。

授权中必须区分三类事实：

| 事实 | 配置字段 | 能证明什么 |
| --- | --- | --- |
| 提交身份 | Git author/committer name 与 email | 本地 commit 的 author/committer |
| Git 远端授权 | SSH 密钥、SSH 配置和远端访问结果 | Git SSH 连接使用的密钥；`global` 模式下不能仅凭 `gh` 登录证明实际 push actor |
| GitHub CLI 授权 | `gh api user` 回读的 login | `gh` API 调用账户，不等同于 SSH 推送账户 |

Jira email/token 仍属于同一安装授权，但不复用为 GitHub 登录。已删除的 `ao-work install identity|auth` 与 `ao-work auth jira` 不提供兼容入口。

## 两种执行授权模式

运行 `ao-work auth` 时必须明确选择 `--execution-auth-mode`：

- `global`：只复用机器现有 Git、SSH 和 `gh` 授权。Runtime 回读全局 Git name/email 与 `gh api user` 并要求与候选身份一致；不写全局 `.gitconfig`、SSH 配置、SSH Agent 或 `gh` 配置。SSH push actor 不能独立证明，输出必须保留该证据边界。
- `installation`：在当前安装的 `user/ssh/` 维护独立 Ed25519 密钥、SSH 配置和 `known_hosts`，在 `user/gh/` 使用独立 `GH_CONFIG_DIR`。SSH 固定通过 `ssh.github.com:443` 连接 GitHub，远端 URL 仍保持 `git@github.com:<owner>/<repo>.git`；`IdentitiesOnly yes`、`IdentityAgent none` 和移除 `SSH_AUTH_SOCK` 禁止回退到全局 SSH Agent。

模式、执行身份或安装 SSH 公钥指纹会进入 `install_identity_ref`。变更后，既有工作空间会因身份指纹漂移而失败关闭，必须由指导员明确重新执行 `workspace init`。

## 安装与单独配置

首次 Bootstrap 尚无安装级身份，只能使用调用者当前已登录且有仓库权限的 `gh`/Git 启动账户下载脚本和创建 managed clone。完成 `installation` 授权后，Runtime 会为该 managed clone 固化安装专属 `core.sshCommand`，后续更新继续走安装专属 SSH；Bootstrap 不解析或迁移授权。

交互配置和脱敏查看：

```sh
<install-root>/bin/ao-work auth
<install-root>/bin/ao-work auth --show
```

交互模式会展示授权模式和受影响路径：选择 `global` 时必须输入 `REUSE-GLOBAL`，表示仅复用而不修改全局授权；选择 `installation` 时必须输入 `USE-INSTALLATION`，表示全局授权保持不变。首次安装级授权随后通过 GitHub CLI 官方设备登录，回读 `gh api user` 并登记安装公钥。组织启用 SSO 时，仍可能需要用户在 GitHub 上为该 SSH key 单独授权。

`global` 模式可以非交互配置，但机器现有 Git/`gh` 身份必须已经匹配：

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

`installation` 的首次 GitHub 登录必须在交互终端完成；`--non-interactive` 不接收 GitHub token，也不回退复用全局 `gh`。安装专属 SSH/`gh` 已完整配置且回读一致后，后续非交互调用才可复用它。

Bootstrap 的授权参数与 `ao-work auth` 相同。例如明确复用全局授权：

```sh
printf '%s\n' "$JIRA_TOKEN" | bash developer/bootstrap/install.sh \
  --agent-id <agent-id> \
  --jira-email <jira-email> \
  --git-name <git-name> \
  --git-email <git-email> \
  --github-login <github-login> \
  --execution-auth-mode global \
  --token-stdin \
  --non-interactive
```

未传授权参数时，有交互终端就进入授权引导；无终端则安装成功并返回 `authorization_status=pending` 与 `ao-work auth` 下一步。

## 已有授权保护

任何写入前，Runtime 必须只读检查已有安装身份、SSH/`gh` 路径和 managed clone 的 `core.sshCommand`，只输出账户、模式、路径状态、公钥指纹和配置摘要，不读取或显示私钥、token 或完整敏感配置。

- 不存在：允许创建。
- 受管且相同：只做幂等回读。
- 安装身份、模式或受管配置不同：先返回 `existing_authorization_change_confirmation_required`，展示脱敏的 `existing`、`candidate` 和 `change_digest`；只有用完全相同候选重新执行并传入 `--confirm-replace-authorization <change_digest>` 才能更新。
- 非受管路径、既有私钥轮换、不同安装级 `gh` 账户或自定义 `core.sshCommand`：失败关闭。普通交互、`--non-interactive`、任意非空确认文本都不能覆盖，必须先单独核对和处理风险。
- 目录或私有文件权限过宽：返回 `existing_authorization_permissions_unsafe`，不会无提示 `chmod` 机器已有授权。

旧 `identity.yaml` 缺少授权模式时返回 `install_execution_authorization_upgrade_required`；必须明确选择模式，不能静默默认。

## 存储、执行与恢复

- 身份保存在 `user/identity.yaml`，Jira 凭证保存在 `user/.env`，均为 `0600`。
- 安装级模式使用 `user/ssh/`、`user/gh/`，私有目录为 `0700`，私钥和受管 SSH 配置为 `0600`。
- GitHub CLI 按其官方凭证存储机制保存登录；Runtime 不接收、回显或把 GitHub token 写入工作空间、命令参数、日志或 Git。
- GitHub 主机密钥来自版本化受信资产。严格校验失败时必须更新受审查资产，不能临场关闭校验或无校验接受 `ssh-keyscan`。
- GitHub 登录、公钥登记和本地身份落盘是可恢复的分阶段操作，不宣称跨外部系统原子提交。失败输出应指明可重试动作，不自动删除全局配置、远端公钥或现有安装凭证。
- 删除安装目录前，用户必须按输出的公钥指纹在 GitHub 撤销对应 SSH key；Runtime 不自动删除远端密钥。
- 直接 Git、SSH 和 `gh` 操作按所选模式使用受控环境；业务项目的构建、测试和其它任意验证子进程继续使用无凭证隔离环境，看不到安装私钥、SSH Agent、`GH_CONFIG_DIR` 或 GitHub 凭证。

## 工作空间验证

`workspace init` 和任务入口从当前安装读取身份与凭证，并按 Project Profile 回读 Jira 身份、Project 权限和仓库访问。schema v5 工作空间只保存 `install_identity_ref` 与项目事实，不保存身份或凭证。

schema v4 及更早工作空间和工作空间 `.agentic-ops/.env` 已停止作为授权来源；Runtime 在读取旧凭证和发送网络请求前失败关闭。先运行目标安装的 `ao-work auth`，再由指导员明确重新执行 `workspace init`。Runtime 不自动复制或删除旧 `.env`。
