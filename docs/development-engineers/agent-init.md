# 初始化 AgenticOps 研发员

本文是 `developer` 工作面的人用初始化入口。维护 AgenticOps 源头项目请使用根 `AGENTS.md`、`maintainer/AGENTS.md` 和 `ao-maint`。

## 1. 安装并完成研发员授权

默认安装目录是 `~/.agentic-ops`，内容为稳定 `main` 的 developer-only sparse managed clone，不包含 `maintainer/`。

交互安装：

```sh
bash developer/bootstrap/install.sh
```

安装未传授权参数时，有终端会直接进入 `ao-work auth` 引导；无终端会完成安装并输出授权待办。后续随时可以单独运行：

```sh
<install-root>/bin/ao-work auth
<install-root>/bin/ao-work auth --show
```

自动化可以在安装时提供完整授权，Bootstrap 只转交给 Runtime：

```sh
printf '%s\n' "$JIRA_API_TOKEN" | bash developer/bootstrap/install.sh \
  --agent-id <agent-id> \
  --jira-email <jira-account-email> \
  --git-name <git-author-and-committer-name> \
  --git-email <git-author-and-committer-email> \
  --github-login <github-actor-login> \
  --token-stdin \
  --non-interactive
```

如果安装已经完成，使用目标安装的 `<install-root>/bin/ao-work auth` 即可独立配置或轮换。身份保存在安装目录 `user/identity.yaml`，凭证保存在 `user/.env`，权限均为 `0600`；token 不进入命令参数或输出。

## 2. 指定分支验证安装

不在 AgenticOps 源码仓库中时，使用远程启动入口：

```sh
(
  set -e
  bootstrap="$(gh api -H 'Accept: application/vnd.github.raw' \
    '/repos/tapstate/agentic-ops/contents/developer/bootstrap/install-verify-branch.sh?ref=develop')"
  printf '%s\n' "$bootstrap" | bash -s -- --source-branch develop --json
)
```

已经在 AgenticOps 源码仓库中时，也可以直接运行：

```sh
bash developer/bootstrap/install-verify-branch.sh \
  --source-branch develop \
  --json
```

远程启动必须先检查 `gh api` 成功，再把完整脚本交给 `bash`，避免 404 JSON 被当作命令执行。脚本会按 `--source-branch` 从同一分支取得 Bootstrap 公共库；不使用已被禁止的 `AGENTIC_OPS_REPO_URL` 或分支覆盖环境变量。远程分支模式生成可运行的 verification-only developer 安装，也支持与正式安装相同的可选授权参数；通过管道启动时标准输入用于脚本本身，安装后应单独运行生成结果中的 `ao-work auth` 完成授权。`--source-worktree <path>` 只验证本地安装边界，产物不可运行，也不能配置授权或初始化工作空间。正式 `install.sh`、`update.sh`、`rollback.sh` 拒绝维护 verification-only 目录。

## 3. 初始化业务项目工作空间

在独立业务项目 AI 工作空间运行：

```sh
<install-root>/bin/ao-work workspace init
```

非交互模式：

```sh
<install-root>/bin/ao-work workspace init \
  --non-interactive \
  --project tapdata \
  --source-pool-root <pool-root> \
  --confirm
```

`workspace init` 只接收项目、源码与确认参数，不接收 `agent_id`、Jira email/token 或 Git/GitHub 身份。Runtime 从当前安装继承身份和凭证，展示脱敏账户、Project Profile、Jira 站点与 Project Key、源码池和仓库供确认。

池根由 `--source-pool-root` 或安装目录 `user/config.yaml` 的 `source_pool_root` 提供。目录不存在时 init 创建并写入容器 README。`--workspace-root <路径>` 是 `ao-work` 顶层参数，必须放在 `workspace` 之前。

新工作空间固定生成 schema v5 `.agentic-ops/agent.json`，只保存项目事实、`install_identity_ref`、安装入口摘要和工作空间本地入口，不生成 `.agentic-ops/.env`。schema v4 及更早的工作空间会在联网前阻断；先使用目标安装运行 `ao-work auth`，再由指导员以 `<install-root>/bin/ao-work workspace init --confirm-existing-config` 明确重新初始化。Runtime 不自动复制或删除旧凭证，也不扫描 PATH。

初始化会写入当前工作空间 `AGENTS.md` 和 `.agents/skills/` 普通文件副本。不得创建指向安装根的 symlink，也不得加载根项目维护规则。

## 4. 开始任务前检查

```sh
<install-root>/bin/ao-work auth --show
./.agentic-ops/bin/ao-work workspace preflight
./.agentic-ops/bin/ao-work capability list
```

只有授权已配置且 workspace preflight 通过后，才能执行真实 Jira 任务。Jira 当前身份与 Project 权限由 workspace/task Runtime 入口回读。调用具体操作前执行 `./.agentic-ops/bin/ao-work capability show <operation>`；只有 `status=implemented` 且列出明确命令路径时才能调用。

正式接管入口：

```sh
./.agentic-ops/bin/ao-work takeover TAP-12289
```

Runtime 自动判断新接管、接纳存量或恢复；非新接管必须在人可见输出和 Jira Comment 中明文留痕。接管后信息分析和方案分级正常连续推进，只在设计审查、代码审查或风险决策暂停。

## 5. 禁止事项

- 不在 `~/.agentic-ops`、AgenticOps 源头仓库/worktree、业务源码仓库或另一个研发员的工作空间内初始化。
- 不调用已删除的 `ao-work install identity|auth`、`ao-work auth jira` 或 `agentic-cli`。
- 不手工修改 Runtime 管理的授权、`agent.json`、Profile overlay 或工作空间状态。
- 不从进程环境、其它安装、其它工作空间、本机全局配置或历史聊天发现凭证。
