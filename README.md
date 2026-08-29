# AgenticOps

AgenticOps 是公司级 Agentic 研发基础设施。Agent 负责执行，Adapter 把各平台事件转换为
标准操作，Gate 在副作用前依据 Policy、项目规则和任务授权做 `allow / ask / deny`
判定。

```text
Agent → Agent/Tool Adapter → Standard Request → Gate + Policy
  ↑                                                │
  └──────────── Standard Decision ← Workflow + Project
```

## 目录

- `contracts/`：标准请求、判定和 Adapter Manifest。
- `gate/`、`policies/`：平台无关门禁和公司规则。
- `workflow/`：任务阶段、授权、CI、证据和恢复。
- `projects/<project>/`：各产品项目的独立适配。
- `adapters/`：Agent Hook 与工具协议的薄转换。
- `bootstrap/`：安装、更新、回退和工作空间接线。
- `internal/`：仅供本仓库维护和发布，不进入产品安装。

## 维护者

```sh
git clone git@github.com:tapstate/agentic-ops.git
cd agentic-ops
./agenticops setup
```

`setup` 自动切换并跟踪 `develop`，把依赖、缓存、故事门禁和发布记录统一放在
`.local/`。详细说明见[维护指引](docs/maintenance-guide.md)。

## 使用者

```sh
./agenticops install --branch main
~/.agentic-ops/agenticops agents
~/.agentic-ops/agenticops init --workspace <项目工作空间> --project tapdata
~/.agentic-ops/agenticops start --agent <Agent ID> --workspace <项目工作空间>
```

省略 `--agent` 时接入当前 Product Root 已安装的全部 Agent；需要指定多个时重复传入。
安装时可把 `main` 换成发布分支，后续 `update` 始终跟随首次记录的分支。

工作空间的 `.agenticops/` 分为：

```text
.agenticops/
├── init.json                 # 可再生接线清单
├── workspace.json            # Product Root、项目和 Agent 绑定
└── tasks/
    ├── index.json            # 多任务注册表
    └── <JIRA-KEY>/           # 该任务的状态、授权、事件和 CI
```

一个工作空间绑定一个产品项目，可同时激活多个任务；一个任务可修改多个仓库。详见
[使用指引](docs/usage-guide.md)与[v1 架构](docs/architecture/agenticops-v1-architecture.md)。

## 验证与发布

```sh
internal/acceptance.sh quick
internal/acceptance.sh full
python3 internal/version.py
```

旧版 AgenticOps 固定在 `v0.7`。v1.x 在 `develop` 开发，通过受控发布进入 `main`。
