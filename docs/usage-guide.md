# AgenticOps 使用指引

不熟悉本文术语时，先查看[术语表](glossary.md)。

## 1. 安装

本节用于业务使用者安装已发布的 AgenticOps，不用于维护产品源码。安装前准备 Git、
GitHub CLI（`gh`）、Bash 和 Python 3.9+；`gh` 必须登录到有本仓库读取权限的 GitHub
账号。Git SSH 的配置、验证和撤销见[Git SSH 授权指引](security/git-ssh-access.md)。

先确认 `gh` 登录状态：

```sh
gh auth status -h github.com
```

未登录时完成网页登录；不要把 token 写入命令行或仓库：

```sh
gh auth login --hostname github.com --git-protocol ssh --skip-ssh-key --scopes repo
```

`gh auth status` 成功时无需重复登录。随后使用当前账号从私有仓库读取受信 `main` 分支的
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
`git clone` 被拒绝时，按 Git SSH 授权指引检查密钥与仓库授权。不要把产品源码仓库当作
业务安装目录。

## 2. 初始化项目工作空间

先查看产品根目录（Product Root）当前提供的 Agent：

```sh
~/.agentic-ops/agenticops agents
```

接入全部 Agent：

```sh
~/.agentic-ops/agenticops init --workspace <项目工作空间> --project tapdata
```

只接入部分 Agent 时重复传入：

```sh
~/.agentic-ops/agenticops init --workspace <项目工作空间> --project tapdata --agent <Agent-ID-1> --agent <Agent-ID-2>
```

没有 `both` 特殊值，也不限制 Agent 数量。一个工作空间绑定一个产品项目，可接管该
项目下任意多个任务；一个任务可修改多个仓库。

## 3. 工作空间数据

```text
.agenticops/
├── init.json                 # 初始化版本与生成产物哈希
├── workspace.json            # 产品根目录、项目、Agent 集合
└── tasks/
    ├── index.json            # 任务注册与激活状态
    └── <JIRA-KEY>/           # 该任务的状态、授权、事件和 CI
```

`init.json` 和 Agent 配置可重新生成；`workspace.json` 是工作空间配置；`tasks/` 是
业务运行数据。Policy、Project Skill 和 Runtime 不复制到工作空间。

## 4. 检查、更新与回退

```sh
~/.agentic-ops/agenticops doctor --workspace <项目工作空间>
~/.agentic-ops/agenticops repair --workspace <项目工作空间>
~/.agentic-ops/agenticops update
~/.agentic-ops/agenticops rollback
```

`doctor` 只读检查；`repair` 安全重建接线并迁移旧工作空间状态，不改任务语义。
`update` 只 fast-forward 到已记录分支；`rollback` 回到最近一次更新前的提交。

## 5. 启动 Agent、查看并接管任务

启动已绑定的 Agent：

```sh
~/.agentic-ops/agenticops start --agent <Agent ID> --workspace <项目工作空间>
```

`start` 会先刷新接线，并把 `--` 后参数原样交给 Agent。在对话中先请求只读任务清单：

```text
列出当前工作空间已登记或可恢复的任务；再只读查询 Jira 中我可以接管的任务。不要执行写操作。
```

本地注册表只保存已接管任务；Jira 仍是待接管任务的事实源。若 Agent 没有可用 Jira 原生
连接，应明确报告，而不是编造任务清单。

```sh
python3 ~/.agentic-ops/workflow/task.py list --dir <项目工作空间>
python3 ~/.agentic-ops/workflow/task.py status --issue-key TAP-123 --dir <项目工作空间>
```

接管时向 Agent 发送：

```text
接管 TAP-123。先读取 Jira 事实、项目准入规则和相关代码；列全缺失项、目标仓库、工作分支、验证方式和风险。未经我的方案确认，不要进入实现或执行外部写操作。
```

Agent 提交方案后，按实际信息补全并发送：

```text
确认 TAP-123 的方案。授权仓库：<owner/repo>；工作分支：<branch>；基线：<branch>；变更范围：<范围>；验证：<命令或方法>。仅在此范围内实现、测试、提交、推送和创建/更新 PR；范围、风险或验证变化时停止并重新确认。
```

Agent 应回显任务阶段、实际变更仓库、验证结果、提交、PR 和 CI；证据回写 Jira 前必须展示
内容供确认。

任务类型为 `defect_fix`、`feature_change`、`technical_task`。多个任务可同时 `active`；
存在歧义时必须显式绑定 issue key。Agent 必须按项目准入要求登记每个仓库的工作分支、
基线、范围和验证方式；研发工程师确认方案并签发任务授权后，才能进入实现。

Jira、Git、GitHub PR/CI 仍是事实源。合并、发布、Tag、保护分支写入、强推和历史改写
不被普通任务授权覆盖；事实、权限或外部写入结果不明确时必须停止，不能手改
`.agenticops/` 或换工具绕过门禁。

平台接线细节见 [Claude 验证](testing/e2e-claude.md)和
[Codex 验证](testing/e2e-codex.md)；权限边界见[安全说明](security/permissions.md)。
