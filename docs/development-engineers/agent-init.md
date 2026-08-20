# 初始化 AgenticOps 研发员

本文是业务项目工作空间的人用初始化入口。它只面向 `developer` 工作面；维护 AgenticOps 源头项目请从根 `AGENTS.md` 和 `maintainer/AGENTS.md` 开始。

## 1. 安装共享 developer 运行环境

默认安装目录为 `~/.agentic-ops`。安装物是稳定 `main` 的 developer-only sparse managed clone，不代表研发员，也不包含 `maintainer/`。

从可信的 AgenticOps 源头检出执行：

```sh
bash developer/bootstrap/install.sh
```

安装完成后把命令目录加入当前终端：

```sh
export PATH="$HOME/.agentic-ops/bin:$PATH"
ao-work --help
```

## 1.1 指定分支验证安装（仅本地验证）

验证脚本用于本地功能验证，不参与生产发布流程。默认从官方远端 `tapstate/agentic-ops` 按 `--source-branch` 克隆（默认 `develop`），生成可运行的验证安装：

```sh
bash developer/bootstrap/install-verify-branch.sh \
  --source-branch develop \
  --json
```

默认写入独立验证目录（`~/test/agentic-ops-verify-<时间戳>`）；加 `--keep` 保留排障目录，`--log` 指定日志路径。该入口显式写入 `verification-only` 标记，拒绝写入 `~/.agentic-ops`，并在克隆前校验 `--source-branch` 已推送到官方远端。

远程模式的产物是可运行的验证安装：安装身份校验（origin、sparse 精确集、developer 分发白名单、shared 协议树）与生产一致，只把「HEAD 必须是 `origin/main` 祖先」放宽为「HEAD 可达于任一 `origin/*` 远端分支或 tag」。因此可以用它的 `ao-work workspace init` 初始化一名研发员做端到端验证。`install.sh`、`update.sh`、`rollback.sh` 仍拒绝把该验证目录当生产目录维护。

提供 `--source-worktree <path>` 时降级为本地流程验证：从本地源码目录克隆，只校验 sparse checkout、developer 分发白名单、shared 协议树和 `uv` 运行时同步能否跑通；其 origin 是本地路径，不可运行，也不能初始化研发员。用于验证尚未推送的本地改动能否正确完成安装。

没有 `agentic-cli` 兼容命令，也不需要 Go 或项目自有平台二进制。

## 1.2 配置安装目录身份与凭证（新工作空间必需）

D-048 阶段二起，新工作空间初始化前必须先把研发员身份与 Jira 凭证配置到安装目录（`<安装目录>/user/identity.yaml` 与 `user/.env`，0600）。`workspace init` 从安装目录继承执行身份与凭证，不再逐次接收 `--git-name/--git-email/--github-login` 与 `--jira-email/--token-stdin`：

```sh
printf '%s\n' "$JIRA_API_TOKEN" | ao-work install identity set \
  --agent-id <agent-id> \
  --jira-email <jira-account-email> \
  --git-name <git-author-and-committer-name> \
  --git-email <git-author-and-committer-email> \
  --github-login <github-actor-login> \
  --jira-token-stdin \
  --non-interactive
```

`--jira-token-stdin` 把 token 一并写入安装目录凭证（等价于后续再执行 `ao-work install auth set`）。查看/清除：`ao-work install identity show|remove`、`ao-work install auth show|remove`。安装目录未配置身份时，`install auth set` 会阻断（`install_identity_missing`），必须先配身份。

## 2. 初始化业务项目工作空间

一个业务项目工作空间代表一名研发员，并只绑定该研发员的一个 Jira 账户。在独立工作空间根目录运行：

```sh
ao-work workspace init
```

`workspace init` 默认使用第 1 节稳定 `main` 安装的 `~/.agentic-ops/bin/ao-work`；如需验证未发布分支，用第 1.1 节远程模式的验证安装（`--source-worktree` 本地模式不可运行）。

非交互初始化（脚本/CI）必须先完成第 1.2 节安装身份配置。`--workspace-root` 是 `ao-work` 顶层全局参数（默认当前目录 `.`），在目标工作空间目录内运行时省略：

```sh
cd "$WS_DIR"   # 业务项目工作空间根目录
ao-work workspace init \
  --non-interactive \
  --project tapdata \
  --agent-id <agent-id> \
  --source-pool-root <pool-root> \
  --confirm
```

必填项（非交互）：已配置且可回读的安装身份与凭据、`--non-interactive` 与 `--confirm`、`--project`（Project Profile id）、与安装身份一致的 `--agent-id`（只允许 `[0-9A-Za-z_-]`），以及池根（`--source-pool-root` 或安装目录 `user/config.yaml` 的 `source_pool_root` 二选一）。池根目录不存在时由 init 自动创建并写入容器 README（preflight 只读校验、不创建）。新工作空间必须生成 schema v4 `agent.json`；schema v3 只作为旧工作空间迁移输入，不得继续作为初始化结果。

交互入口会让公司员工指导员确认：

- `agent_id`；默认取规范化后的纯小写主机名，只允许 `[0-9A-Za-z_-]`。
- Project Profile、Jira 站点和 Project Key。
- 从安装身份继承的脱敏 Jira email；token 不重复输入。
- 默认代码仓库和源码目录。
- 从安装身份继承的 Git author/committer 与 GitHub actor login，不读取全局身份。

Jira 站点、Project、状态/字段映射和默认仓库由 Project Profile 提供，不要求逐项输入。后续任务的 Issue ID、经办人、状态和描述来自授权后的 Jira 卡片；run ID、摘要和时间由 Runtime 生成。每个任务只需审查 AI 提议的计划、范围、验证与权限，并确认高风险动作。

Runtime 会先执行工作空间边界、已有配置、身份冲突、授权、Jira Project 访问和 Git 访问等前置检查。任何检查失败都必须停止，不得手工伪造 `.agentic-ops/agent.json`。

## 3. 开始任务前检查

```sh
ao-work workspace preflight
ao-work auth jira show
ao-work auth jira verify
ao-work capability list
```

只有 `preflight` 与授权验证都成功后，才能读取或执行真实 Jira 任务。调用具体操作前继续执行 `ao-work capability show <operation>`；只有 `status=implemented` 且返回明确命令路径时才能调用，`capability_gap` 必须停止或转人工。Jira token 保存在安装目录 `user/.env`（第 1.2 节）或当前业务项目工作空间 `.agentic-ops/.env`（旧 schema 存量路径），两者由 init 阶段按 schema 选择其一；共享安装的其它业务工作空间和 maintainer 工作面不得继承。

授权完成后，普通任务只传 Jira key，并以接管为首个任务动作：

```sh
ao-work takeover TAP-12289
```

Runtime 从工作空间、Project Profile 和 Jira 自动生成或恢复本地运行上下文；用户只审查输出中的计划、范围、分支、验证和权限。该入口不写 Jira，也不表示正式接管已经完成。

初始化生成的当前工作空间 `AGENTS.md` 直接承载 developer Rule，并把 developer Skill 复制到 Codex 标准发现目录 `.agents/skills/`。标准资产只通过 `ao-work` 从受信安装根解析；AI 不得把 `developer/...` 当作业务仓库相对路径，也不得读取根项目维护规则或切换工作面。`workspace preflight` 会阻断 Skill 缺失、漂移、额外资产或 maintainer 污染。
