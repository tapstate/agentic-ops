# AgenticOps 使用指引

## 1. 安装

准备 Git、Bash 和 Python 3.9+：

```sh
git clone git@github.com:tapstate/agentic-ops.git
cd agentic-ops
./agenticops install --branch main
```

默认安装到 `~/.agentic-ops`。要使用发布分支，只在安装时改
`--branch <发布分支>`；选择会写入 `.local/product.json`，以后 `update` 自动跟随，
不再重复传分支。自定义安装目录使用 `--install-home`。

## 2. 初始化项目工作空间

先查看 Product Root 当前提供的 Agent：

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
├── workspace.json            # Product Root、项目、Agent 集合
└── tasks/
    ├── index.json            # 任务注册与激活状态
    └── <JIRA-KEY>/           # 该任务的状态、授权、事件和 CI
```

`init.json` 和 Agent 配置可重新生成；`workspace.json` 是工作空间配置；`tasks/` 是
业务运行数据。Policy、Project Skill 和 Runtime 不复制到工作空间。

## 4. 检查、启动、更新

```sh
~/.agentic-ops/agenticops doctor --workspace <项目工作空间>
~/.agentic-ops/agenticops repair --workspace <项目工作空间>
~/.agentic-ops/agenticops start --agent <Agent ID> --workspace <项目工作空间>
~/.agentic-ops/agenticops update
~/.agentic-ops/agenticops rollback
```

`start` 只允许启动已经绑定到该工作空间的 Agent，并把 `--` 后参数原样交给 Agent。
`doctor` 只读检查；`repair` 安全重建接线并迁移旧工作空间状态，不改任务语义。
`update` 只 fast-forward 到已记录分支；`rollback` 回到最近一次更新前的提交。

## 5. 接管与恢复任务

```sh
python3 ~/.agentic-ops/workflow/task.py list --dir <项目工作空间>
python3 ~/.agentic-ops/workflow/task.py init --issue-key TAP-123 --task-class defect_fix --dir <项目工作空间>
python3 ~/.agentic-ops/workflow/task.py status --issue-key TAP-123 --dir <项目工作空间>
```

任务类型为 `defect_fix`、`feature_change`、`technical_task`。多个任务可同时
`active`；存在歧义时必须显式传 `--issue-key`。Agent 应按项目准入要求登记每个
仓库的工作分支、基线、范围和验证方式，人工确认方案后再签发任务授权。

Jira、Git、GitHub PR/CI 仍是事实源。合并、发布、Tag、保护分支写入、强推和历史改写
不被普通任务授权覆盖；事实、权限或外部写入结果不明确时必须停止，不能手改
`.agenticops/` 或换工具绕过门禁。

平台接线细节见 [Claude 验证](testing/e2e-claude.md)和
[Codex 验证](testing/e2e-codex.md)；权限边界见[安全说明](security/permissions.md)。
