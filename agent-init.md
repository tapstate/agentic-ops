# AgenticOps 初始化入口

本文面向 Codex / AIAgent。研发负责人在项目 AI 工作空间中启动 Codex 后，可以发送：

```text
安装 https://github.com/tapstate/agentic-ops/blob/main/agent-init.md 并初始化
```

收到该指令后，Codex 应按本文初始化当前项目 AI 工作空间中的 AgenticOps 能力。

## 目标

- 确认或安装 AgenticOps 全局命令 `agentic-cli`。
- 在当前项目 AI 工作空间内初始化工作空间配置。
- 初始化当前 AIAgent 的 AgenticOps 能力。
- 向研发负责人说明下一步可以如何开始工作。

## 前置判断

初始化前先确认当前目录：

- 当前目录必须是项目 AI 工作空间，例如 `~/agentic-ops-tapdata`。
- 不得在 `~/.agentic-ops` 中初始化业务工作空间。
- 不得在 `tapstate/agentic-ops` 源头仓库中初始化业务工作空间。
- 当前目录应可写，并用于保存 `.agentic-ops/` 运行状态、执行记录和反馈记录。

如果目录不符合要求，停止并要求研发负责人切换到正确的项目 AI 工作空间。

## 初始化步骤

### 1. 检查或安装 AgenticOps

先检查 `agentic-cli` 是否可用：

```sh
agentic-cli --version
```

如果命令不存在，但 `~/.agentic-ops/bin/agentic-cli` 已存在，说明通常只是当前 shell 没有配置 `PATH`。先执行：

```sh
case ":$PATH:" in
  *":$HOME/.agentic-ops/bin:"*) ;;
  *) export PATH="$HOME/.agentic-ops/bin:$PATH" ;;
esac
agentic-cli --version
```

如果这样可以输出版本，不要重新安装，继续后续初始化步骤。

如果命令不存在，使用安装入口：

```sh
gh auth status
gh api -H 'Accept: application/vnd.github.raw' \
  '/repos/tapstate/agentic-ops/contents/scripts/install.sh?ref=main' \
  | AGENTIC_OPS_REPO_URL='git@github.com:tapstate/agentic-ops.git' bash
```

如果 `gh auth status` 失败，先要求研发负责人完成 GitHub CLI 登录：

```sh
gh auth login -h github.com -p ssh -s repo
```

`/repos/.../install.sh?ref=main` 必须用引号包起来，避免 zsh 把 `?ref=main` 当成通配符。`AGENTIC_OPS_REPO_URL` 显式指定 SSH clone 地址；如果当前机器只能使用其它 clone 地址，再由研发负责人确认后替换该变量值。

如果检测到 `~/.agentic-ops` 已存在，安装脚本会进入更新模式。Codex 不得自行确认更新；必须先向研发负责人说明当前目录会被更新到 `origin/main` 最新版本，并等待明确同意。获得同意后，非交互执行时使用：

```sh
gh api -H 'Accept: application/vnd.github.raw' \
  '/repos/tapstate/agentic-ops/contents/scripts/install.sh?ref=main' \
  | AGENTIC_OPS_ASSUME_YES=1 AGENTIC_OPS_REPO_URL='git@github.com:tapstate/agentic-ops.git' bash
```

安装脚本会把 `tapstate/agentic-ops` clone 到 `~/.agentic-ops`，更新到 `origin/main` 最新版本，校验 `install-resources/checksums.txt`，并把当前平台已经编译并提交的 `install-resources/<os-arch>/agentic-cli` 复制到 `~/.agentic-ops/bin/agentic-cli`。安装过程不在当前机器上编译。

安装后再次执行：

```sh
case ":$PATH:" in
  *":$HOME/.agentic-ops/bin:"*) ;;
  *) export PATH="$HOME/.agentic-ops/bin:$PATH" ;;
esac
agentic-cli --version
```

### 2. 初始化项目 AI 工作空间

确认研发负责人提供了以下信息：

- 工作空间名称，例如 `tapdata`。
- Jira 用户，例如 `harsen@tapdata.io`。
- Jira 空间，例如 `TAP`。

在当前项目 AI 工作空间目录中执行：

```sh
agentic-cli workspace init --workspace tapdata --jira-user harsen@tapdata.io --jira-project TAP
```

初始化参数必须与当前 AgenticOps 版本中的 `install-resources/basic/profiles/<workspace>.yaml` 匹配。以 `tapdata` 为例，`workspace`、`jira.user` 和 `jira.project` 必须分别匹配 `tapdata`、`harsen@tapdata.io` 和 `TAP`。

### 3. 初始化 AIAgent 能力

执行：

```sh
agentic-cli agent init --workspace tapdata
agentic-cli preflight --workspace tapdata
```

如果 `preflight` 失败，停止接管任务，并把缺失配置、权限或路径问题说明给研发负责人。

## 初始化完成后的回复

初始化完成后，向研发负责人说明：

- 当前工作空间名称。
- 当前 `agentic-cli` 版本。
- 工作空间预检结果。
- 必须人工确认的动作：真实 Jira 写操作、推送、创建或更新拉取请求、合并、发布、范围变更。
- 下一步可以发送的指令。

可用下一步示例：

```text
列出我名下可以接管的 Jira 任务。
接管 TAP-123，并先说明计划、验证方式和风险点。
回写本次执行证据。
提交 TAP-123 本次执行的任务审计记录。
```

## 安全边界

- 不保存 secrets、tokens、private keys 或原始敏感日志。
- 不把 `~/.agentic-ops` 当作项目 AI 工作空间。
- 不把 AgenticOps 源头仓库当作业务项目 AI 工作空间。
- 不在初始化阶段执行真实 Jira 写操作、Git 推送、创建拉取请求、合并或发布。
- 接管真实 Jira 任务前，必须通过 `agentic-cli preflight --workspace <workspace>`。
