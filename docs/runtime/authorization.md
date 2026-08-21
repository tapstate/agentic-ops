# AgenticOps developer 授权管理

## 边界

`ao-work auth` 是 developer 安装唯一的授权入口，同时维护研发员 `agent_id`、Jira email/token、Git author/committer 和 GitHub login。一个 developer 安装代表一名研发员；同一安装下的多个业务项目工作空间继承同一身份与凭证，但各自绑定 Project Profile。maintainer 的 `ao-maint` 不读取、修改或验证业务 Jira 凭证。

已删除的 `ao-work install identity|auth` 与 `ao-work auth jira` 不提供兼容入口。

## 安装时授权

Bootstrap 可选接收授权参数，但只负责把参数和标准输入转交 Python Runtime：

```sh
printf '%s\n' "$JIRA_TOKEN" | bash developer/bootstrap/install.sh \
  --agent-id <agent-id> \
  --jira-email <jira-email> \
  --git-name <git-name> \
  --git-email <git-email> \
  --github-login <github-login> \
  --token-stdin \
  --non-interactive
```

未传授权参数时，有交互终端就进入授权引导；无终端则安装成功并返回 `authorization_status=pending` 与 `ao-work auth` 下一步。

## 单独配置和查看

```sh
ao-work auth
ao-work auth --show
```

自动化配置：

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

重复执行就是更新或轮换授权；`--show` 只返回配置状态、`agent_id`、脱敏 email、Git 姓名和 GitHub login，不返回 token。

## 存储和验证

- 身份保存在当前安装 `user/identity.yaml`，凭证保存在 `user/.env`，均为 `0600`。
- email 和 token 必须来自同一次显式授权，不从进程环境、其它安装、其它工作空间或历史聊天拼接。
- token 只允许隐藏输入或标准输入，不进入参数、YAML、JSON 输出、日志、事件或提交。
- `ao-work auth` 只负责本地授权配置；`workspace init` 和任务入口根据 Project Profile 回读 Jira 当前身份与访问能力。
- schema v4 工作空间只保存 `install_identity_ref` 与项目事实，不保存身份或凭证。
- schema v3 与工作空间 `.agentic-ops/.env` 已停止作为授权来源；Runtime 在读取旧凭证和发送网络请求前失败关闭。
- 旧工作空间先运行 `ao-work auth`，再由指导员明确重新执行 `workspace init`。Runtime 不自动复制或删除旧 `.env`。

## TAP-12289 验收顺序

1. 使用 `ao-work auth` 配置当前 developer 安装账户。
2. 在独立业务项目 AI 工作空间用 `ao-work workspace init` 绑定 `tapdata` Profile。
3. 回读 Jira 当前用户、Project 和 TAP-12289 的基础事实。
4. 按真实任务流程执行接管及后续门禁，并验证本地状态与 Jira 可见留痕。

Description 只允许修改明确确认的受管内容，不得覆盖 TAP-12289 的原始缺陷日志。
