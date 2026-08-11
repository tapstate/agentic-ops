# 研发工程师上手

本文面向使用 AgenticOps 指挥 AIAgent 处理日常 Jira 任务的研发工程师。重点是安装、初始化项目 AI 工作空间，并让 AIAgent 按标准资产执行。

## 快速开始

选择一条路径执行。路径 A 适合先在终端完成安装；路径 B 适合让 Codex 托管初始化。

### 路径 A：终端安装，Codex 初始化能力

1. 登录 GitHub。

```sh
gh auth login -h github.com -p ssh -s repo
```

2. 安装 AgenticOps。

```sh
gh api -H 'Accept: application/vnd.github.raw' \
  '/repos/tapstate/agentic-ops/contents/scripts/install.sh?ref=main' \
  | AGENTIC_OPS_REPO_URL='git@github.com:tapstate/agentic-ops.git' bash
```

3. 让当前终端读取安装后的命令路径。

```sh
source "$HOME/.zshrc"
agentic-cli --version
```

4. 创建并进入项目 AI 工作空间。

```sh
mkdir -p ~/agentic-ops-tapdata
cd ~/agentic-ops-tapdata
```

5. 初始化工作空间。

```sh
agentic-cli workspace init --project tapdata --interactive
```

交互式初始化会复用已有配置，只询问缺失项。Tapdata 的 Jira base URL 默认使用 `https://tapdata.atlassian.net`；Jira API token 只保存到本机 `$AGENTIC_OPS_HOME/user/.env` 的 `AGENTIC_OPS_JIRA_API_TOKEN`，不写入 YAML 配置。

6. 启动 Codex。

```sh
codex
```

7. 按全局指引启用 AgenticOps。

```text
按 ~/.agentic-ops/agent-guides.md 启用 AgenticOps。
```

AIAgent 会从全局指引、当前工作空间 `AGENTS.md` 和安装资产初始化，不依赖个人 wiki 或上一段聊天上下文。

### 路径 B：让 Codex 托管初始化

1. 创建并进入项目 AI 工作空间。

```sh
mkdir -p ~/agentic-ops-tapdata
cd ~/agentic-ops-tapdata
```

2. 启动 Codex。

```sh
codex
```

3. 让 Codex 托管安装、工作空间初始化和能力初始化。

```text
安装 https://github.com/tapstate/agentic-ops/blob/main/agent-init.md 并初始化。项目是 tapdata，请使用交互式引导配置项目 AI 工作空间和 Jira 连接。
```

### 下一步指令

初始化完成后，研发工程师可以继续发送：

```text
列出我名下可以接管的 Jira 任务。
接管 TAP-123；信息不足时先结合代码形成补卡建议并写回 Jira，接管后先把修复计划写入 Jira 等我确认。
确认该设计，并授权在当前 Jira 工作项、仓库、任务分支、目标分支和验证范围内连续推进到拉取请求审查；范围或风险变化时停下。
回写本次执行证据。
提交 TAP-123 本次执行的任务审计记录。
```

## 常见问题

### `gh` 未登录或权限不足

检查 GitHub 登录状态。

```sh
gh auth status
```

重新登录 GitHub。

```sh
gh auth login -h github.com -p ssh -s repo
```

### 已安装后再次更新

交互式更新 AgenticOps。

```sh
gh api -H 'Accept: application/vnd.github.raw' \
  '/repos/tapstate/agentic-ops/contents/scripts/install.sh?ref=main' \
  | AGENTIC_OPS_REPO_URL='git@github.com:tapstate/agentic-ops.git' bash
```

已确认更新时，非交互更新 AgenticOps。

```sh
gh api -H 'Accept: application/vnd.github.raw' \
  '/repos/tapstate/agentic-ops/contents/scripts/install.sh?ref=main' \
  | AGENTIC_OPS_ASSUME_YES=1 AGENTIC_OPS_REPO_URL='git@github.com:tapstate/agentic-ops.git' bash
```

### 当前终端找不到 `agentic-cli`

重新读取 zsh 配置。

```sh
source "$HOME/.zshrc"
agentic-cli --version
```

临时修复当前终端 PATH。

```sh
case ":$PATH:" in
  *":$HOME/.agentic-ops/bin:"*) ;;
  *) export PATH="$HOME/.agentic-ops/bin:$PATH" ;;
esac
agentic-cli --version
```

使用完整路径验证安装结果。

```sh
~/.agentic-ops/bin/agentic-cli --version
```

### zsh 提示 `no matches found`

使用带引号的 GitHub API 路径。

```sh
gh api -H 'Accept: application/vnd.github.raw' \
  '/repos/tapstate/agentic-ops/contents/scripts/install.sh?ref=main' \
  | AGENTIC_OPS_REPO_URL='git@github.com:tapstate/agentic-ops.git' bash
```

## 工作空间初始化

不要在 `~/.agentic-ops` 或 AgenticOps 源头仓库中初始化业务工作空间。项目 AI 工作空间是具体业务项目的运行目录，例如 `~/agentic-ops-tapdata`。

推荐命令：

```sh
mkdir -p ~/agentic-ops-tapdata
cd ~/agentic-ops-tapdata
agentic-cli workspace init --project tapdata --interactive
```

交互式初始化会检查已有配置，只询问缺失项。首次初始化前准备好：

- 项目配置项，例如 `tapdata`。
- Jira 邮箱；Tapdata 的 Jira base URL 默认是 `https://tapdata.atlassian.net`。
- Jira API token；首次缺失时交互式初始化会提示输入，并保存到 `$AGENTIC_OPS_HOME/user/.env` 的 `AGENTIC_OPS_JIRA_API_TOKEN`。
- 本地源码目录；默认是当前工作空间下的 `repos/<project>`，目录不存在时初始化会从项目 profile 的默认 GitHub 仓库下载代码。

脚本、CI 或非终端环境使用参数形式：

```sh
agentic-cli workspace init --project tapdata --jira-user <your-jira-email>
```

源码目录不是 `repos/tapdata` 时：

```sh
agentic-cli workspace init --project tapdata --jira-user <your-jira-email> --source-root /path/to/source
```

如果 `source_root` 已存在且非空，初始化不会覆盖、拉取或切换分支；如果目录不存在或为空，初始化会执行 `git clone`，并在终端持续显示下载进度。克隆失败时，先检查 GitHub SSH 权限，或使用 `--source-root` 指向已有本地源码目录。

已有完整的 `.agentic-ops/agent.json`、`.agentic-ops/profile.local.yaml` 和 AgenticOps 管理的 `AGENTS.md` 时，初始化会停止。确认覆盖后再执行：

```sh
agentic-cli workspace init --project tapdata --jira-user <your-jira-email> --confirm-existing-config
```

上一次初始化中断且只留下部分受管文件时，重新运行同一条 `workspace init` 命令会自动修复，不要求 `--confirm-existing-config`。初始化会先保存 Jira 本机配置和 token，再下载源码；源码下载失败不会丢失已经输入的 Jira 配置，也不会新建可被误认为初始化完成的 workspace overlay。

初始化成功后重点看：

- `jira_config_status`：`configured`、`needs_jira_api_token` 或 `needs_configuration`。
- `source_checkout_status`：`cloned` 表示已下载源码，`existing` 表示复用了已有源码目录。
- `profile_overlay`：当前工作空间的 `.agentic-ops/profile.local.yaml`。
- `agent_instructions`：当前工作空间的 `AGENTS.md`。

再运行：

```sh
agentic-cli profile resolve --project tapdata
agentic-cli preflight
```

`agent init` 和 `preflight` 都会检查 `.agentic-ops/agent.json`、`.agentic-ops/profile.local.yaml`、`AGENTS.md` 管理块及 `source_root`。任何一项缺失时，工作空间都不会被标记为可接管任务，需重新运行 `workspace init` 完成修复。

## Jira 连接配置

`workspace init` 不写入 Jira API token 到 YAML。应用配置集中写入 `$AGENTIC_OPS_HOME/user/config.local.yaml`，按项目和模块分段；Jira API token 的持久化落点只有 `$AGENTIC_OPS_HOME/user/.env` 中的 `AGENTIC_OPS_JIRA_API_TOKEN`。

`agentic-cli list-tasks` 读取真实 Jira 配置的顺序：

1. 显式环境变量：`AGENTIC_OPS_JIRA_ADAPTER=real`、`AGENTIC_OPS_JIRA_BASE_URL`、`AGENTIC_OPS_JIRA_EMAIL`、`AGENTIC_OPS_JIRA_API_TOKEN`。
2. 当前项目 AI 工作空间：`.agentic-ops/config.local.yaml`。
3. 个人层：`$AGENTIC_OPS_HOME/user/config.local.yaml` 和 `$AGENTIC_OPS_HOME/user/.env`。

配置示例：

```yaml
projects:
  tapdata:
    jira:
      adapter: real
      base_url: https://tapdata.atlassian.net
      email: your-email@example.com
```

`needs_jira_api_token` 时：

先打开 [Atlassian API token 页面](https://id.atlassian.com/manage-profile/security/api-tokens) 创建 token，再把下面内容写入输出中的 `jira_env_file`：

```dotenv
AGENTIC_OPS_JIRA_API_TOKEN=<api-token>
```

然后重新验证：

```sh
agentic-cli list-tasks
```

`AGENTIC_OPS_JIRA_API_TOKEN` 是固定配置名，不需要在 YAML 中声明。CLI 会先读当前进程环境变量，再读取 `$AGENTIC_OPS_HOME/user/.env`。真实 `.env` 属于本机配置，不能提交；如果 `agentic-cli preflight` 或 `agentic-cli list-tasks` 输出 `jira_token_env_has_value: false`，说明当前进程环境和本机 `.env` 都没有读到有效 token。

外部脚本和 AIAgent 读取配置时应通过统一入口，不直接解析 YAML 或 `.env`：

```sh
agentic-cli conf paths.user_config
agentic-cli conf jira.base_url --workspace tapdata
agentic-cli conf jira.api_token_configured --workspace tapdata
```

`needs_configuration` 时：

```sh
agentic-cli workspace init --project tapdata --interactive
```

## 指挥 AIAgent

研发工程师可以用自然语言给 AIAgent 下达任务：

```text
按 ~/.agentic-ops/agent-guides.md 启用 AgenticOps。
列出我名下可以接管的 Jira 任务。
接管 TAP-123；信息不足时先结合代码形成补卡建议并写回 Jira，接管后先把修复计划写入 Jira 等我确认。
回写本次执行证据。
提交 TAP-123 本次执行的任务审计记录。
```

AIAgent 应按全局指引和当前工作空间资产执行，不依赖临场聊天上下文猜流程。

## 人工确认点

以下动作必须由研发工程师或对应专业角色确认后才能继续：

- 真实 Jira 写操作。
- 向 `master`、`main`、`develop`、`release/*` 或其它保护分支推送。
- 合并、发布、Git Tag、强推或历史改写。
- 合并。
- 发布。
- 需求范围、验收标准、目标仓库或风险边界发生变化。

研发工程师确认版本化设计或修复计划时，可以一次性授予工作项级连续执行授权。有效授权覆盖当前任务范围内的实现、验证、提交、任务分支推送、必要 Jira 回写以及创建目标为 `develop` 的拉取请求，AIAgent 不再逐项中断，完成后统一提交拉取请求审查包。保护分支推送、合并、发布、Git Tag、强推、历史改写和范围变化不在该授权内。

任务完成、阻塞或交接时，AIAgent 必须把任务级审计记录写入项目 AI 工作空间的 `.agentic-ops/tasks/<ISSUE-KEY>/` 目录，并将关键结论和稳定引用回写 Jira。本地反馈报告只能用于按需分析和改进建议，不能替代本地任务审计记录。
