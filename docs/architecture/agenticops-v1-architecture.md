# AgenticOps v1 工程架构

本文描述单任务工位产品架构。身份、四操作及恢复细则见[工位合同](single-task-station.md)，方向以[项目目标](../strategy/project-goals.md)为准。旧版本状态只由原版本清理；新版本不提供旧 Runtime。本文不代替真实应用启动与发布验收。

## 1. 核心决策

Agent 是执行主体；AgenticOps 只提供标准、规则门禁和少量确定性状态。确定性入口复用版本化标准和判定，Gate 不理解平台字段。旧版实现固定在 `v0.7`，能力只在解决当前问题时按新分层重建。

```text
Agent 原生工具 → Git / Jira / GitHub / 构建与测试
Agent → Workflow 状态变更入口 → 持锁校验 → 状态与证据
                   ↑
              Project / Policy / Gate 的标准判定
```

使用者工位只保证流程检查点有效，不自动拦截原生工具。Gate 保留标准协议与可复用判定，旧 Agent Hook/Tool Adapter 协议资产未自动接线；它们的存在不代表产品承诺外部调用被拦截。

## 2. 分层

| 层 | 目录 | 责任 |
|---|---|---|
| Contract | `contracts/` | 标准请求、判定、操作词表和 Manifest |
| Gate | `gate/` | 上下文解析与统一判定 |
| Policy | `policies/` | 公司级操作、连续性规则及非阻断方案调优策略 |
| Workflow | `workflow/` | 阶段、授权、CI、证据、恢复 |
| Project | `projects/<project>/` | Jira、分支、准入、验证和 Runbook |
| Adapter | `adapters/` | Agent/工具协议的无状态转换 |
| Bootstrap | `bootstrap/` | 源码目录、产品根目录（Product Root）与工位生命周期 |
| Maintenance Skill | `skills/` | 仅产品维护使用的流程验证与协作指引；不属于任何业务项目，也不安装或接线到业务工位 |
| Internal | `internal/` | AgenticOps 自身的审查和发布 |

规则按变化原因归属：平台差异只能进入 Adapter，项目差异只能进入 Project，公司共性进入 Policy，只有必须确定执行的状态逻辑进入 Workflow。

缺陷修复策略属于通用方案调优，不是 Gate 或质量门禁。通用目录位于 `policies/defect-repair-strategies.json`，项目可在 `projects/<project>/planning.json` 只覆盖默认选择；Workflow 容错解析后由 `task.py checklist/next` 向 Agent 提供 Q2 方案指导。它仅适用于规范化 `defect_fix`，配置不可用、Agent 未应用或方案偏离均只产生警告，不改变阶段推进、质量问题或授权判定。健康配置默认使用“最小充分修复”：完整消除已确认根因并保持架构稳定性、兼容性和容错性，同时限制无关扩散；它不等于追求最少代码行。

## 3. 通用 Agent 适配

公共入口不维护 Agent 枚举。`bootstrap/agent_registry.py` 从 `adapters/agents/*/manifest.json` 发现 Agent。每个 Manifest 声明生成接线与 `retired_artifacts`。退役列表只用于核验并迁移以前托管的文件，不按平台名称在 Bootstrap 写特例。当前内置 Agent 不生成通用 Hook，旧协议能力字段供显式标准判定与协议测试使用。每个 Manifest 声明：

- Agent ID、入口和协议能力；
- `ask` 不可用时的保守降级；
- 要生成的工位接线；
- 本地启动方式。

新增 Agent 只增加一个目录、Manifest、必要的薄适配、模板和测试。Adapter 不得保存状态、依赖 Policy/Project/Workflow 或定义新操作语义。`tests/test_adapter_boundary.py` 对每个 Agent 约束文件数、代码量、依赖和状态写入。

## 4. 安装目录与工位

用户只需使用安装目录和工位。安装目录默认是 `~/.agentic-ops`，通过 `agenticops` 提供生命周期和工位接线；产品源码目录只服务产品维护，不构成用户工作模型。

- 安装目录的 `update` 只跟随安装时记录的分支，且要求 HEAD 与本地安装记录严格一致；
- 安装目录所有非 Git 本地状态都进入 `.local/`；
- `.local/product.json` 记录仓库、跟踪分支、生命周期同步提交及 `source_pool`；`.local/gate/events.jsonl` 只记录直接在安装目录执行且无法归属任务的门禁事件，避免把产品状态误写成项目工位状态；
- 安装目录不包含 `internal/` 或仅供产品维护的 Skill。

`.local/` 是本机可删除、不可提交的产品运行区，不是规则或业务事实源。除生命周期配置外，它保存由本安装目录成功初始化过的工位登记；登记用于跨 epoch 切换前确认所有工位均已解绑，不发现、不扫描、更不自动修改业务目录。首次安装或 setup 创建空登记；跨 epoch 时登记缺失、损坏或无法读取均停止切换。生命周期操作使用 `.local/lifecycle.lock/` 串行 update、rollback 与工位绑定变更。普通生成接线由下一次 `start` 刷新，也可通过 `doctor` 和 `repair` 检查、修复。检测到托管 Hook 退役时，start 和普通 repair 保留现场并报告迁移范围；用户明确选择 `repair --accept-checkpoint-migration` 后，先校验全部旧 Hook 的归属哈希，再移除并刷新。此选择不是持久门禁开关，不会改写 task/run 或授权记录。

安装目录配置的源码池是下载缓存：`repositories/<owner>/<repo>.git` 是 bare 仓库；`shared-repositories/<owner>/<repo>` 是与缓存对象独立的完整共享参考仓库，由 `bootstrap/shared-repositories.json` 登记、Project Profile 引用。Bootstrap 的通用 ensure/update/status 仅管理显式准备、快进更新和本地核验；初始化、接管、任务开始及查询不自动刷新。共享参考仓不进入工位任务基线、状态或 epoch，不随 station purge 清理，不承担业务事实源职责。使用边界见[工位源码与材料](../usage/station-materials.md)。

## 5. 单任务工位

一个工位绑定一个 Project，当前任务存入 .agenticops/current-task.json；没有当前任务且无未完成 operation 才为空闲。source 中每个仓库均有独立 Git 元数据，不使用共享对象依赖。config 保存持久配置，runtime 保存唯一活动环境，archive 保存不可变正式档案及追加回执。

完整工程基线保存 Profile、版本解析输入、每仓 origin/ref/SHA 与摘要；任务修改仓另存分支、目标、范围、验证和交付事实，引用冻结基线。上下文通过 task.py repository context 返回已核验路径，Agent 在同一工位会话使用原生工具操作，不另建会话状态或复制中央规则。

## 6. 四操作与恢复

takeover 先登记操作意图和当前任务，再准备完整独立源码与 runtime；失败保留同 run/op。archive 可归档未完成任务，冻结后禁止继续开发且仍占用。release 在项目交付与验收核对及研发明确确认后归档、回收现场并解绑。clean 在未完成任务的精确处置授权和有效档案之后清理，保留源码仓库及 refs，成功后才允许下个任务。

状态以工位锁串行，写请求绑定 run/revision，跨文件副作用以操作意图、回读、阶段和幂等回执恢复，不宣称事务或防篡改。未知归属、清理范围变化、路径漂移或资源停止失败均阻止解绑。

## 7. 生成、清理与升级分层

Bootstrap 生成可再生接线、稳定绑定、空 current 和四类目录；repair 只修复同代际接线。station purge/detach 只处理空闲且无未完成操作的工位，验证生成归属，拒绝未知状态或非空 runtime，移除受管接线、绑定和状态。source/config/archive 与用户文件保留；重新生成时非空持久材料需明确 --reuse-materials，并生成新 station_id，不继承旧 run 授权。

同版本生成→任务闭环→purge→生成先独立验收。升级是第二层：升级器比较当前与目标 `station_state_epoch`；相同时可直接切换，变化时要求工位登记为空。任务退出、状态验证和解绑仍由原版本的 `station purge` 负责，升级器不读取任务、operation、runtime 或旧状态。目标版本只接受自身 epoch，repair 不跨 epoch 采用；登记缺失、损坏、非空或无法核验时保持原产品版本。当前 epoch 以机器契约为准。

项目适配继续管理 Jira、分支、验证和资源配方；外部同步只记录来源和回读，不替代 Git/PR/CI 事实。当前任务的方案、Q1-Q4、授权与实际提交核验继续使用 run/revision，旧证据不能借给新任务。

## 8. 连续性与安全

未迁移的辅助能力优先由 Agent 原生能力完成；没有安全自动路径时，只暂停当前副作用并输出人工接力。事实不可信、权限不足、高风险操作或外部写入结果不明必须停止。

使用者运行不接线通用工具 Hook。确定性状态检查在 Workflow 的实际变更入口执行；外部操作不因有副作用而进入 Gate。显式调用 Gate 标准 API 的协议校验仍失败关闭，但不能据此宣称已限制所有原生调用。直接编辑状态文件不受支持，也不具备防篡改保证。

Hook 是流程控制点，不是安全沙箱。不得关闭 Agent 平台原生沙箱或把未命中透传配置成无条件外部写权限；凭证最小权限、服务端保护、CI 和人工审查仍是最终边界。合并、发布、Tag、保护分支写入、强推和历史改写不被普通任务授权覆盖。Agent Hook、共享 Adapter Runtime 和 Tool Adapter 分类策略属于发布信任根，修改后禁止自动发布，必须通过受保护 `main` 的独立人工审查 PR 完成升级。

## 9. 架构验收

- 公共入口可发现任意合规 Agent Manifest，不存在固定平台枚举。
- Gate 只接受标准协议，Adapter 重量门禁通过。
- 新工位不生成通用工具 Hook；旧托管 Hook 只经显式迁移移除，任一目标漂移时保留现场。
- 源码目录和安装产品根目录共用结构、入口和 `.local/` 约定。
- 工位分离持久配置、独立源码、单份运行产物、归档和当前状态。
- 单任务、多仓库上下文唯一，方案确认绑定变化阻止检查点推进；重复请求不连跳阶段。
- 多个工位互不共享 Git 元数据和可写 runtime；启动权限不扩展到其它工位。
- 独立源码、origin、完整基线摘要、清理和顺序复用行为均由可执行测试约束。
- 新项目适配不修改公共 Gate；产品安装不包含 `internal/`。
- 四项固定测试覆盖 Runtime、资源、安装接线和发布治理。
