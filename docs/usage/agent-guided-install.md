# Agent引导安装指引

适合把安装和项目工作空间初始化交给 Codex、Claude Code 等 AI Agent 完成。你仍然负责确认目录、账号访问权限以及 Agent 提出的任何外部写入或高风险操作；AgenticOps 的 Hook 和 Gate 会继续约束后续任务副作用。

这条路径只安装使用工作面并初始化一个 TapData 项目工作空间，不接管 Jira 任务、不修改业务仓库，也不提交、推送或合并代码。

## 1. 创建空工作空间目录

选择一个不在 `~/.agentic-ops`、`~/.agentic-ops-repos` 或任何业务仓库内的位置。目录必须为空，避免 AgenticOps 初始化时覆盖你的文件。以下以 `~/agenticops-tapdata` 为例：

```sh
mkdir -p "$HOME/agenticops-tapdata"
cd "$HOME/agenticops-tapdata"
```

开始前还要确保本机已有 Git、Python 3.9+，且 Git SSH 可以读取 `tapstate/agentic-ops` 与 TapData 项目所需仓库。具体检查见[Git SSH 授权指引](../security/git-ssh-access.md)。

## 2. 从该目录启动 Agent

在当前目录启动你平时使用的 Agent，例如在终端直接启动 Codex 或 Claude Code。此时目录仍然只是空工作空间；不要先手动克隆 AgenticOps，也不要在这里克隆 TapData 业务仓库。

## 3. 把安装请求发送给 Agent

将下面提示词中的文档地址、项目名、工作空间和 Source Pool 按实际情况替换后，发给同一会话中的 Agent。示例使用现役 `main` 的首次使用指引，明确要求它只在当前目录初始化工作空间：

```text
根据 https://raw.githubusercontent.com/tapstate/agentic-ops/main/docs/usage-guide.md 安装并初始化 tapdata 项目：工作空间为当前 cwd 目录，源码池使用 ~/.agentic-ops-repos。

开始前先确认当前 cwd 是空目录，且它不在安装目录、源码池或业务仓库内；检查 Git、Python 3.9+ 与 Git SSH 权限。按文档安装到默认的 ~/.agentic-ops，初始化当前 cwd 为 tapdata 工作空间，并执行 doctor 验证接线。不要接管 Jira 任务、准备业务仓库、修改业务代码、提交、推送或合并；遇到权限、目录冲突或外部写入确认时停下并说明原因和所需决定。
```

## 完成标准

Agent 应回报以下可核验结果：

- 使用工作面已安装在 `~/.agentic-ops`；
- 当前 cwd 已初始化为 `tapdata` 工作空间，包含生成的 `./agenticops` 与 `.agenticops/workspace.json`；
- `./agenticops doctor` 已通过；
- Source Pool 绑定为 `~/.agentic-ops-repos`。它只是受控业务仓库主工作树的根目录；后续任务实际在工作空间 `.agenticops/worktrees/` 下的 linked worktree 中执行。

安装和初始化结束后，先结束这次从空目录启动的 Agent 会话，再从该工作空间重新启动 Agent：`./agenticops start codex` 或 `./agenticops start claude`。这样 Agent 才会在启动时加载刚生成的项目指引和 Hook；首次使用 Codex 时按 `/hooks` 提示审核并信任该 Hook。随后才按[首次使用指引](../usage-guide.md)接管具体 Jira 任务。
