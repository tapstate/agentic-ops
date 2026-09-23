# AgenticOps 术语表

本页只解释 AgenticOps 的专有名词；Git、Jira、GitHub 和 CI 使用其原有含义。

| 名词 | 含义 |
|---|---|
| 安装目录（Installation Directory） | 承载 AgenticOps 中央产品资产并提供 `agenticops` 入口的目录，默认位于 `~/.agentic-ops`；本机配置位于 `.local/`。产品源码目录仅用于维护 AgenticOps，不是用户工作目录。 |
| 源码目录 | 维护者克隆的 AgenticOps Git 仓库，即源码产品根目录；在 `develop` 分支维护和运行产品。它不是业务项目工位。 |
| 项目工位 | 业务项目的本地工作目录。一工位、一套完整 source、一个 current；config/runtime 分离持久配置与运行现场，不复制中央规则。正式档案保存在绑定 Product Root 的 `.archive/`。 |
| 产品项目（Project） | 一个业务项目的适配配置，位于 `projects/<project>/`，包含 Jira、仓库、分支、准入规则和 Runbook。 |
| Agent | 实际执行研发任务的平台，例如 Codex 或 Claude。 |
| Agent Adapter | 通过 Manifest 与模板声明 Agent 接线、指引和启动方式的无状态适配层，不执行工具 Hook。 |
| Tool Adapter | 声明原生 MCP 接线的无状态适配层；旧 MCP/CLI 分类执行链已退役。 |
| 标准契约（Standard Contract） | `contracts/` 中版本化的请求、判定和 Manifest 协议，是 Adapter 与 Gate 的共同边界。 |
| Gate | 标准判定内核，供确定性入口复用上下文与规则检查；使用者原生工具不再自动接入。 |
| Policy | 公司级操作、连续性规则与通用非阻断调优策略，位于 `policies/`；不写业务项目特例。 |
| 修复策略 | 只影响 `defect_fix` 方案生成倾向的 advisory 调优配置。Q1 展示当前选择，Q2 消费方案指导；它不进入 Gate、质量阻断或授权必填绑定。 |
| Workflow | 阶段、授权、CI、证据和恢复等确定性状态逻辑，位于 `workflow/`。 |
| 任务 | 一个 Jira 工作项在本地的执行单元；可关联多个代码仓库，状态和证据按任务隔离。 |
| 工位编号 | station_id，工位生成时创建的稳定身份。重新生成工位产生新身份。 |
| 执行编号 | run_id，一次接管的身份；恢复不变，清理后再接管产生新编号。 |
| 操作编号 | operation_id，一次可恢复生命周期操作的身份；重试不换编号。 |
| 任务授权 | 对特定任务、仓库、工作分支、改动范围和验证方式的明确允许；范围变化后原授权失效。 |
| Hook | 平台或 Git 的事件入口。使用者通用 Agent Hook 已退役；源码仓库 Git Hook 保留且独立于 Workflow。 |
| Bootstrap | 安装、更新、回退和工位接线能力，位于 `bootstrap/`；不承载任务流程或规则。 |

术语之间的分层和调用关系见 [v1 工程架构](architecture/agenticops-v1-architecture.md)；具体的权限边界见 [权限与安全边界](security/permissions.md)。
