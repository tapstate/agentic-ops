# 研发负责人上手

本文面向使用 AgenticOps 指挥 AIAgent 处理日常 Jira 任务的研发负责人。研发负责人不需要理解 AgenticOps 源码结构，重点是完成安装、项目 AI 工作空间初始化，并让 AIAgent 按标准资产执行。

## 快速开始

AgenticOps 安装是全局动作，项目初始化是工作空间动作。安装脚本会把 `tapstate/agentic-ops` clone 到 `~/.agentic-ops`，更新到 `origin/main` 最新版本，校验 `install-resources/checksums.txt`，再把当前平台已经编译并提交的 `agentic-cli` 复制到 `~/.agentic-ops/bin/agentic-cli`。安装过程不在研发负责人机器上编译。

### 标准路径

1. 确认 GitHub CLI 已登录，并具备访问 `tapstate/agentic-ops` 私有仓库的权限：

```sh
gh auth status
```

如果尚未登录，先执行：

```sh
gh auth login -h github.com -p ssh -s repo
```

2. 安装 AgenticOps：

```sh
gh api -H 'Accept: application/vnd.github.raw' \
  '/repos/tapstate/agentic-ops/contents/scripts/install.sh?ref=main' \
  | AGENTIC_OPS_REPO_URL='git@github.com:tapstate/agentic-ops.git' bash
```

`/repos/.../install.sh?ref=main` 必须用引号包起来，避免 zsh 把 `?ref=main` 当成通配符。`AGENTIC_OPS_REPO_URL` 显式指定 SSH clone 地址；如果需要改用其它 clone 地址，可以替换该变量值。

如果本机已经存在 `~/.agentic-ops`，安装脚本会进入更新模式，展示当前 ref 和目标分支，并要求确认后才更新。非交互环境必须在研发负责人确认后显式增加 `AGENTIC_OPS_ASSUME_YES=1`：

```sh
gh api -H 'Accept: application/vnd.github.raw' \
  '/repos/tapstate/agentic-ops/contents/scripts/install.sh?ref=main' \
  | AGENTIC_OPS_ASSUME_YES=1 AGENTIC_OPS_REPO_URL='git@github.com:tapstate/agentic-ops.git' bash
```

3. 创建并进入项目 AI 工作空间：

```sh
mkdir -p ~/agentic-ops-tapdata
cd ~/agentic-ops-tapdata
```

4. 初始化工作空间：

```sh
agentic-cli workspace init --workspace tapdata --jira-user harsen@tapdata.io --jira-project TAP
```

5. 在 `~/agentic-ops-tapdata` 启动 Codex。

6. 给 Codex 发送：

```text
初始化 AgenticOps 能力，工作空间是 tapdata。
```

Codex 应读取 AI 资产入口、执行 `agentic-cli agent init --workspace tapdata` 和 `agentic-cli preflight --workspace tapdata`，然后说明当前可用能力、人工确认点和下一步指令。

### Codex 初始化路径

如果研发负责人希望由 Codex 完成安装检查和初始化，可以先创建工作目录并在其中启动 Codex：

```sh
mkdir -p ~/agentic-ops-tapdata
cd ~/agentic-ops-tapdata
```

然后给 Codex 发送：

```text
安装 https://github.com/tapstate/agentic-ops/blob/main/agent-init.md 并初始化
```

Codex 应按 `agent-init.md` 检查当前目录、确认 `gh` 登录状态、确认或安装 `agentic-cli`、初始化工作空间、初始化 AIAgent 能力，并在预检通过后提示如何开始工作。

### 下一步指令

初始化完成后，研发负责人可以继续发送：

```text
列出我名下可以接管的 Jira 任务。
接管 TAP-123，并先说明计划、验证方式和风险点。
回写本次执行证据。
提交 TAP-123 本次执行的任务审计记录。
```

## 工作空间初始化

`~/.agentic-ops` 是全局安装目录，也是 AgenticOps 的 managed clone；项目 AI 工作空间是具体业务项目的运行目录，例如 `tapstate/` 或 `tapdata/`。不要在 `~/.agentic-ops` 或 AgenticOps 源头仓库中初始化业务工作空间。

初始化时需要明确：

- Jira 用户。
- Jira 项目。
- 项目 AI 工作空间目录。
- Jira 空间到代码仓库的映射。
- 本地源码根目录。
- 工作流配置。

`tapdata` 示例要求当前安装版本中存在匹配的 `install-resources/basic/profiles/tapdata.yaml`。如果使用其它工作空间，`--workspace`、`--jira-user` 和 `--jira-project` 必须与对应工作流配置匹配。

```sh
agentic-cli workspace init --workspace tapdata --jira-user harsen@tapdata.io --jira-project TAP
```

## 指挥 AIAgent

研发负责人可以用自然语言给 AIAgent 下达任务：

```text
初始化 AgenticOps 能力，工作空间是 tapdata。
列出我名下可以接管的 Jira 任务。
接管 TAP-123，并先说明计划、验证方式和风险点。
回写本次执行证据。
提交 TAP-123 本次执行的任务审计记录。
```

AIAgent 应读取 [AI 资产入口](../../install-resources/basic/ai-assets/README.md)，再按 AI 员工手册、操作契约、工作流配置、策略和模板推进。研发负责人不应要求 AIAgent 依赖临场聊天上下文猜流程。

## 人工确认点

以下动作必须由研发负责人或对应专业角色确认后才能继续：

- 真实 Jira 写操作。
- Git 推送。
- 创建或更新拉取请求。
- 合并。
- 发布。
- 需求范围、验收标准、目标仓库或风险边界发生变化。

任务完成、阻塞或交接时，AIAgent 必须提交任务级审计记录。本地反馈报告只能用于按需分析和改进建议，不能替代 Jira、审计服务或目标仓库中的任务事实记录。
