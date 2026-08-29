# 远程一键引导安装

本指引用于业务使用者安装已发布的 AgenticOps；不用于维护产品源码。安装前完成
[Git SSH 授权指引](security/git-ssh-access.md)，并准备 Git、GitHub CLI（`gh`）、Bash 和
Python 3.9+。`gh` 必须登录到有本仓库读取权限的 GitHub 账号。

先确认 `gh` 登录状态：

```sh
gh auth status -h github.com
```

未登录时完成网页登录，不要把 token 写入命令行或仓库：

```sh
gh auth login --hostname github.com --git-protocol ssh --skip-ssh-key --scopes repo
```

`gh auth status` 成功时无需重复登录。然后使用当前账号从私有仓库读取受信 `main` 分支的
安装入口：

```sh
(
  set -euo pipefail
  bootstrap="$(gh api -H 'Accept: application/vnd.github.raw' \
    '/repos/tapstate/agentic-ops/contents/bootstrap/install.sh?ref=main')"
  printf '%s\n' "$bootstrap" | bash
)
```

安装入口会将同一分支稀疏克隆到 `~/.agentic-ops`，并先检查依赖和目录冲突；下载、认证或
克隆失败时不会继续安装。`gh api` 被拒绝时，核对当前账号的仓库访问权与 `repo` scope；
`git clone` 被拒绝时，按 Git SSH 指引检查密钥与仓库授权。

## 3. 初始化并启动 Agent

项目工作空间必须与安装目录分开。先列出可用 Agent，选择其中一个 ID；以下以 `codex`
为例：

```sh
~/.agentic-ops/agenticops agents
workspace="$HOME/agenticops-tapdata"
~/.agentic-ops/agenticops init --workspace "$workspace" --project tapdata --agent codex
~/.agentic-ops/agenticops doctor --workspace "$workspace"
~/.agentic-ops/agenticops start --agent codex --workspace "$workspace"
```

将 `codex` 替换为 `agents` 输出中的实际 ID。省略 `--agent` 时会接入全部已安装 Agent。

## 4. 在 Agent 中查看并接管任务

启动后，在 Agent 对话中先发送以下只读请求：

```text
列出当前工作空间已登记或可恢复的任务；再只读查询 Jira 中我可以接管的任务。不要改变 Jira 状态、创建任务状态或执行写操作。
```

若 Jira 原生连接未配置，Agent 必须明确说明该限制；此时直接提供 Jira key。接管时发送：

```text
接管 TAP-123。先读取 Jira 事实、项目准入规则和相关代码；判断任务类型，列全缺失项、目标仓库、工作分支、验证方式和风险。未经我的方案确认，不要进入实现或执行外部写操作。
```

Agent 提交方案后，按实际信息补全并确认授权范围：

```text
确认 TAP-123 的方案。授权仓库：<owner/repo>；工作分支：<branch>；基线：<branch>；变更范围：<范围>；验证：<命令或方法>。仅在此范围内实现、测试、提交、推送和创建/更新 PR；范围、风险或验证变化时停止并重新确认。
```

Agent 应回显任务阶段、实际变更仓库、验证结果、提交、PR 和 CI；证据回写 Jira 前必须展示
内容供确认。合并、发布、Tag、强推、历史改写和保护分支写入始终需要单独确认。

本地任务状态可随时只读回查：

```sh
python3 ~/.agentic-ops/workflow/task.py list --dir "$workspace"
python3 ~/.agentic-ops/workflow/task.py status --issue-key TAP-123 --dir "$workspace"
```

安装目录已存在时不要重复运行安装；使用 `~/.agentic-ops/agenticops update` 更新。更多
工作空间与恢复细节见[使用指引](usage-guide.md)。
