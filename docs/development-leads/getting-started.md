# 研发负责人上手

本文面向使用 AgenticOps 指挥 AIAgent 处理日常 Jira 任务的研发负责人。研发负责人不需要理解 AgenticOps 源码结构，重点是完成安装、项目 AI 工作空间初始化，并让 AIAgent 按标准资产执行。

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
agentic-cli workspace init --project tapdata --jira-user <your-jira-email>
```

6. 启动 Codex。

```sh
codex
```

7. 按全局指引启用 AgenticOps。

```text
按 ~/.agentic-ops/agent-guides.md 启用 AgenticOps。
```

这句话明确要求 AIAgent 先读取 `~/.agentic-ops/agent-guides.md`，再依赖当前项目 AI 工作空间中的 `AGENTS.md`、`.agentic-ops/agent.json`，以及安装目录 `~/.agentic-ops/install-resources/basic/` 下的 AI 资产。新 AIAgent 不需要、也不应依赖研发负责人本机的 Obsidian wiki、个人长期记忆或上一段聊天上下文完成初始化。

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
安装 https://github.com/tapstate/agentic-ops/blob/main/agent-init.md 并初始化。项目是 tapdata，Jira 用户是 <your-jira-email>。
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

`~/.agentic-ops` 是全局安装目录，也是 AgenticOps 的 managed clone；项目 AI 工作空间是具体业务项目的运行目录，例如 `tapstate/` 或 `tapdata/`。不要在 `~/.agentic-ops` 或 AgenticOps 源头仓库中初始化业务工作空间。

初始化时需要明确：

- Jira 用户。
- 项目配置项，例如 `tapdata`。
- 项目 AI 工作空间目录。
- 本地源码目录；未指定时默认使用当前项目 AI 工作空间下的 `repos/<project>`。

`tapdata` 示例要求当前安装版本中存在匹配的 `install-resources/basic/projects/tapdata/profile.yaml`。Jira 项目、Jira 到代码仓库的映射、流程、表单和模板由项目包 profile 定义。本地 Jira 用户、项目 AI 工作空间目录、本地源码目录、运行日志目录和反馈目录由 `workspace init` 写入当前工作空间 `.agentic-ops/profile.local.yaml`，不复制完整共享 profile。

推荐使用交互式初始化，让 CLI 检查已有配置并只询问缺失项：

```sh
agentic-cli workspace init --project tapdata --interactive
```

脚本、CI 或非终端环境使用完整参数形式：

```sh
agentic-cli workspace init --project tapdata --jira-user <your-jira-email> --jira-base-url https://your-domain.atlassian.net
```

如果源码目录不是默认的 `repos/tapdata`，初始化时显式确认：

```sh
agentic-cli workspace init --project tapdata --jira-user <your-jira-email> --jira-base-url https://your-domain.atlassian.net --source-root /path/to/source
```

如果当前目录已经存在 `.agentic-ops/agent.json`、`.agentic-ops/profile.local.yaml` 或 AgenticOps 管理的 `AGENTS.md` 配置块，初始化会停止并要求确认。确认需要覆盖已有本地配置时重新执行：

```sh
agentic-cli workspace init --project tapdata --jira-user <your-jira-email> --confirm-existing-config
```

## Jira 连接配置

`workspace init` 不会复制或写入 Jira API token。交互式初始化会从已有环境变量、当前工作空间配置和个人项目层配置读取默认值，已配置项只需回车确认，缺失项才会询问。初始化时提供或确认 Jira base URL 后，CLI 会在个人项目层生成 `$AGENTIC_OPS_HOME/user/projects/<project>/jira.local.yaml`，并默认使用 `AGENTIC_OPS_JIRA_API_TOKEN` 作为 token 环境变量名。`agentic-cli list-tasks` 需要真实 Jira adapter，CLI 会按以下顺序读取运行时本地配置：

1. 显式环境变量：`AGENTIC_OPS_JIRA_ADAPTER=real`、`AGENTIC_OPS_JIRA_BASE_URL`、`AGENTIC_OPS_JIRA_EMAIL`、`AGENTIC_OPS_JIRA_API_TOKEN`。
2. 当前项目 AI 工作空间：`.agentic-ops/jira.local.yaml`。
3. 个人项目层：`$AGENTIC_OPS_HOME/user/projects/<project>/jira.local.yaml`。
4. 个人全局层：`$AGENTIC_OPS_HOME/user/jira.local.yaml`。

推荐把非敏感连接信息放在个人层，并用 `api_token_env` 引用本机环境变量：

```yaml
adapter: real
base_url: https://your-domain.atlassian.net
email: your-email@example.com
api_token_env: AGENTIC_OPS_JIRA_API_TOKEN
```

如果需要自定义 token 环境变量名：

```sh
agentic-cli workspace init --project tapdata --jira-user <your-jira-email> --jira-base-url https://your-domain.atlassian.net --jira-token-env TAPDATA_JIRA_TOKEN
```

查看最终合并结果和字段来源：

```sh
agentic-cli profile resolve --project tapdata
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

AIAgent 应读取 [AI 资产入口](../../install-resources/basic/ai-assets/README.md)，再按 AI 员工手册、操作契约、工作流配置、策略和模板推进。研发负责人不应要求 AIAgent 依赖临场聊天上下文猜流程。

当研发负责人说“按 `~/.agentic-ops/agent-guides.md` 启用 AgenticOps。”时，AIAgent 应先读取全局指引，再使用当前工作空间生成的 `AGENTS.md`、`.agentic-ops/agent.json` 和 `agentic-cli agent init` 输出定位本地 AI 资产入口；不得要求读取研发负责人个人 wiki。

## 人工确认点

以下动作必须由研发负责人或对应专业角色确认后才能继续：

- 真实 Jira 写操作。
- Git 推送。
- 创建或更新拉取请求。
- 合并。
- 发布。
- 需求范围、验收标准、目标仓库或风险边界发生变化。

任务完成、阻塞或交接时，AIAgent 必须提交任务级审计记录。本地反馈报告只能用于按需分析和改进建议，不能替代 Jira、审计服务或目标仓库中的任务事实记录。
