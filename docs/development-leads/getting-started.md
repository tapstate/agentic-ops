# 研发负责人上手

本文面向使用 AgenticOps 指挥 AIAgent 处理日常 Jira 任务的研发负责人。重点是安装、初始化项目 AI 工作空间，并让 AIAgent 按标准资产执行。

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

交互式初始化会复用已有配置，只询问缺失项。首次初始化时请准备 Jira 邮箱、Jira base URL 和 token 环境变量名；token 本身只放在本机环境变量中，不写入配置文件。

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

初始化完成后，研发负责人可以继续发送：

```text
列出我名下可以接管的 Jira 任务。
接管 TAP-123，并先说明计划、验证方式和风险点。
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
- Jira 邮箱和 Jira base URL。
- token 环境变量名，默认 `AGENTIC_OPS_JIRA_API_TOKEN`。
- 本地源码目录；默认是当前工作空间下的 `repos/<project>`。

脚本、CI 或非终端环境使用参数形式：

```sh
agentic-cli workspace init --project tapdata --jira-user <your-jira-email> --jira-base-url https://your-domain.atlassian.net
```

源码目录不是 `repos/tapdata` 时：

```sh
agentic-cli workspace init --project tapdata --jira-user <your-jira-email> --jira-base-url https://your-domain.atlassian.net --source-root /path/to/source
```

已有 `.agentic-ops/agent.json`、`.agentic-ops/profile.local.yaml` 或 AgenticOps 管理的 `AGENTS.md` 时，初始化会停止。确认覆盖后再执行：

```sh
agentic-cli workspace init --project tapdata --jira-user <your-jira-email> --confirm-existing-config
```

初始化成功后重点看：

- `jira_config_status`：`configured`、`needs_token_env` 或 `needs_configuration`。
- `profile_overlay`：当前工作空间的 `.agentic-ops/profile.local.yaml`。
- `agent_instructions`：当前工作空间的 `AGENTS.md`。

再运行：

```sh
agentic-cli profile resolve --project tapdata
agentic-cli preflight
```

## Jira 连接配置

`workspace init` 不写入 Jira API token。它只记录 base URL、email 和 `api_token_env`，token 放在本机环境变量中。

`agentic-cli list-tasks` 读取真实 Jira 配置的顺序：

1. 显式环境变量：`AGENTIC_OPS_JIRA_ADAPTER=real`、`AGENTIC_OPS_JIRA_BASE_URL`、`AGENTIC_OPS_JIRA_EMAIL`、`AGENTIC_OPS_JIRA_API_TOKEN`。
2. 当前项目 AI 工作空间：`.agentic-ops/jira.local.yaml`。
3. 个人项目层：`$AGENTIC_OPS_HOME/user/projects/<project>/jira.local.yaml`。
4. 个人全局层：`$AGENTIC_OPS_HOME/user/jira.local.yaml`。

配置示例：

```yaml
adapter: real
base_url: https://your-domain.atlassian.net
email: your-email@example.com
api_token_env: AGENTIC_OPS_JIRA_API_TOKEN
```

自定义 token 环境变量名：

```sh
agentic-cli workspace init --project tapdata --jira-user <your-jira-email> --jira-base-url https://your-domain.atlassian.net --jira-token-env TAPDATA_JIRA_TOKEN
```

`needs_token_env` 时：

```sh
read -s AGENTIC_OPS_JIRA_API_TOKEN
export AGENTIC_OPS_JIRA_API_TOKEN
agentic-cli list-tasks
```

`needs_configuration` 时：

```sh
agentic-cli workspace init --project tapdata --interactive
```

## 指挥 AIAgent

研发负责人可以用自然语言给 AIAgent 下达任务：

```text
按 ~/.agentic-ops/agent-guides.md 启用 AgenticOps。
列出我名下可以接管的 Jira 任务。
接管 TAP-123，并先说明计划、验证方式和风险点。
回写本次执行证据。
提交 TAP-123 本次执行的任务审计记录。
```

AIAgent 应按全局指引和当前工作空间资产执行，不依赖临场聊天上下文猜流程。

## 人工确认点

以下动作必须由研发负责人或对应专业角色确认后才能继续：

- 真实 Jira 写操作。
- Git 推送。
- 创建或更新拉取请求。
- 合并。
- 发布。
- 需求范围、验收标准、目标仓库或风险边界发生变化。

任务完成、阻塞或交接时，AIAgent 必须提交任务级审计记录。本地反馈报告只能用于按需分析和改进建议，不能替代 Jira、审计服务或目标仓库中的任务事实记录。
