# AgenticOps 术语表

本页解释 AgenticOps 及项目适配中需要统一的术语；Git、Jira、GitHub 和 CI 使用其原有含义。

| 名词 | 含义 |
|---|---|
| 安装目录（Installation Directory） | 承载 AgenticOps 中央产品资产并提供 `agenticops` 入口的目录，默认位于 `~/.agentic-ops`；本机配置位于 `.local/`。产品源码目录仅用于维护 AgenticOps，不是用户工作目录。 |
| 源码目录 | 维护者克隆的 AgenticOps Git 仓库，即源码产品根目录；在 `develop` 分支维护和运行产品。它不是业务项目工位。 |
| 项目工位 | 业务项目的本地工作目录。一工位、一套完整 source、一个 current；config/runtime 分离持久配置与运行现场，不复制中央规则。正式档案保存在绑定 Product Root 的 `.archive/`。 |
| IT（Integration Test） | 指 TapData 的集成测试。测试级别按项目 Wiki 和目标源码判断，与是否在 CI 执行分开；多级测试分别验收，具体材料见[共同验证材料](usage/quality-checkpoints.md#共同验证材料)。 |
| 产品项目（Project） | 一个业务项目的适配配置，位于 `projects/<project>/`，包含 Jira、仓库、分支、准入规则和 Runbook。 |
| 完整应用源码集 Profile | `full-application` 指定完整应用对应的源码仓库集合；接管准备独立仓库和冻结基线，不表示依赖、构建、应用启动或健康检查已完成。 |
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
| PR Ready | 全部目标 PR 已推进 GitHub Ready for review，并完成身份、当前 Head、OPEN 与非 Draft 的原生回读确认。预检通过仅表示可提审，不代表提审完成；实现合同见[质量检查与证据](usage/quality-checkpoints.md#pr-ready-核对)。 |
| 任务授权 | 对特定任务、仓库、工作分支、改动范围和验证方式的明确允许；范围变化后原授权失效。 |
| Hook | 平台或 Git 的事件入口。使用者通用 Agent Hook 已退役；源码仓库 Git Hook 保留且独立于 Workflow。 |
| Bootstrap | 安装、更新、回退和工位接线能力，位于 `bootstrap/`；不承载任务流程或规则。 |

术语之间的分层和调用关系见 [v1 工程架构](architecture/agenticops-v1-architecture.md)；具体的权限边界见 [权限与安全边界](security/permissions.md)。

## 当前清理与归档职责

| 术语 | 含义 |
|---|---|
| 工作清理 | 按清单清理生成物及用户选定配置，保留当前任务与授权。 |
| 任务清理 | 工作清理和任务资源收尾，撤销活动授权并解除任务绑定。 |
| 任务归档 | 尽力追加已有结果与缺项记录，不冻结任务；归档写入失败按原操作处理，未获重试或放弃决定时暂停依赖步骤。 |
| release | 组合尽力归档、任务清理及结果补记，不代替业务完成验收。 |
| 工位注销 | 调用同版本退出后移除受管接线与绑定，默认保留源码和配置。 |
| 环境配置 | 跨任务可复用资产，用户明确选择才清理，与任务生成物分别管理。 |

执行、保护和恢复只由[单任务工位合同](architecture/single-task-station.md)定义。

## 目标生命周期术语

以下尚待实现，不改变上表现役含义；行为唯一见[工位目标合同](architecture/single-task-station.md#任务生命周期目标合同)。

| 术语 | 目标含义 |
|---|---|
| 归档接续点 | 可跨工位核实并继续的持久成果集合，不是完成或释放任务 |
| 清理任务 | 清除任务工作数据和独占资源，保留工位用户配置 |
| 清理工位 | 清除工位使用积累，恢复当前版本初始状态，保留接线与登记 |
| 执行接管 | 目标以同一 Jira 站点与任务 ID 协调当前执行者，区别于本地写入保护和任务授权；旧安装支持范围依实际版本 |
| 闲置阈值 | 目标距 Jira 协调记录最后报告活动满 8 小时提示可能闲置，不证明本地停写或授权删除；本地活动不代替 Jira 报告时间 |
