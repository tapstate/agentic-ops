# AgenticOps 术语表

本页只解释 AgenticOps 的专有名词；Git、Jira、GitHub 和 CI 使用其原有含义。

| 名词 | 含义 |
|---|---|
| 产品根目录（Product Root） | 承载 AgenticOps 中央产品资产并提供 `agenticops` 入口的合规目录。源码产品根目录是维护工作面；安装产品根目录是使用工作面，默认位于 `~/.agentic-ops`。两者的本机状态均放在各自 `.local/`。 |
| 源码目录 | 维护者克隆的 AgenticOps Git 仓库，即源码产品根目录；在 `develop` 分支维护和运行产品。它不是业务项目工作空间。 |
| 项目工作空间 | 业务项目的本地工作目录。它保存 `.agenticops/` 初始化、配置和按任务隔离的运行数据，不复制中央规则。 |
| 产品项目（Project） | 一个业务项目的适配配置，位于 `projects/<project>/`，包含 Jira、仓库、分支、准入规则和 Runbook。 |
| Agent | 实际执行研发任务的平台，例如 Codex 或 Claude。 |
| Agent Adapter | 把 Agent 平台事件转换为 AgenticOps 标准操作的无状态适配层。 |
| Tool Adapter | 把 MCP、CLI 等工具操作映射为标准操作的无状态适配层。 |
| 标准契约（Standard Contract） | `contracts/` 中版本化的请求、判定和 Manifest 协议，是 Adapter 与 Gate 的共同边界。 |
| Gate | 副作用前的统一门禁：根据上下文和规则决定放行、请求确认或拒绝。 |
| Policy | 公司级操作与连续性规则，位于 `policies/`；不写业务项目特例。 |
| Workflow | 阶段、授权、CI、证据和恢复等确定性状态逻辑，位于 `workflow/`。 |
| 任务 | 一个 Jira 工作项在本地的执行单元；可关联多个代码仓库，状态和证据按任务隔离。 |
| 任务授权 | 对特定任务、仓库、工作分支、改动范围和验证方式的明确允许；范围变化后原授权失效。 |
| Hook | Agent 或工具在副作用前调用的拦截点。它执行 Gate 判定，但不是安全沙箱。 |
| Bootstrap | 安装、更新、回退和工作空间接线能力，位于 `bootstrap/`；不承载任务流程或规则。 |

术语之间的分层和调用关系见 [v1 工程架构](architecture/agenticops-v1-architecture.md)；具体的权限边界见 [权限与安全边界](security/permissions.md)。
