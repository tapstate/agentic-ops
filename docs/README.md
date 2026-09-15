# AgenticOps 文档总纲

本地执行与 Jira 同步的边界由项目目标和架构定义；质量使用指引负责初始快照、本地确认、非阻断同步及 PR 后警告汇总，契约负责可恢复记录格式。

本文是现役人读文档的结构入口。新建或调整文档时，先在本页或对应主题的子级总纲明确目标、范围、层级、职责和导航关系；再细化正文。仅当文档过长，或稳定内容被多个页面复用时，才拆分子文档。

单任务研发工位的目标是让一个工作空间顺序完成完整工程准备、任务处理、归档和释放，并通过多个工位并行；尚未完成的任务也可先归档标记“未完成”，再按确认范围清理工位。[项目目标](strategy/project-goals.md)声明目标方向与现役边界；[v1 工程架构](architecture/agenticops-v1-architecture.md)继续描述已实现内核，并导航至[单任务工位设计合同](architecture/single-task-station.md)。后者集中定义尚未实现的身份、数据归属、双仓库清单、四操作、中断恢复、升级与可复用验收合同；因其需要独立实现评审而单篇维护，不把目标语义反写成现役能力。工作项、排期、评审进度和执行结果仍由 Jira 管理。

完整工程基线的机器数据由 [engineering-baseline 契约](../contracts/engineering-baseline.schema.json)描述；设计合同的“双仓库信息”章节说明基础模块与 Git 核验的调用边界。基线值构造、只读对象检查不等于新工位生命周期已经接线，不能据此改写现有工作空间状态。

TapData 的活动仓库范围以 `projects/tapdata/repositories.json` 的 `repositories` 为准；文档仓库 docs/docs-en 已解除活动登记，t-layer3-test 保留为验证依赖。`retired_repositories` 只保留旧 worktree 清理所需的可信 origin，不允许新任务准备或分支对齐；清理仍执行路径、Git 身份、租约和脏文件检查。解除登记不删除既有仓库、分支或任务材料。该兼容扩展不改变工作空间状态结构或 epoch；单任务工位目标的升级边界见[设计合同](architecture/single-task-station.md)。

维护 Agent 的协作优化由[维护指引](maintenance-guide.md)说明模型依据、平台能力边界与验证方式，[Skill 维护规范](skill-maintenance.md)负责指令审查标准，根 `AGENTS.md` 保存日常协作约定，`skills/` 保存初始化与接管测试的具体指引。目标是在已有授权内持续完成工作，减少重复确认和重复验证；不改变产品门禁、授权或工作空间状态契约。

TapData 的测试缺口分析、Java 影响范围和 Maven 模块测试属于项目构建与验证适配：[构建、测试与本地运行](../projects/tapdata/runbooks/build-test-and-local-run.md)说明用例判断依据、框架缺口处置、有效 Maven 模型的采集、跨仓消费关系、原生 Maven 执行清单、依赖 Jar 及报告核验的使用边界；项目脚本只准备清单和分析证据，不执行测试或改变任务阶段。项目任务 Skill 引导 Agent 在已有验收方案内自主分析，不替代共同质量检查点。

CI 用例开发与质量核对继续由上述 TapData 构建指引承载：从确认预期定义断言、在业务任务内开发、验证旧新行为并接回模块全量执行，同时说明 PR CI 的原生报告回读、运行及源码绑定与范围披露；质量检查与证据文档负责结果的流程绑定，不另设用例任务状态机。

任务级集成测试报告处理也由上述构建指引说明：原生获取报告后，项目解析器读取本地 XML 或下载的 ZIP，保留多仓多 PR 结果、执行状态和证据缺口。跨仓消费版本未核实只作披露，不新增门禁；解析器不执行网络调用、测试或任务状态写入。

已有开发成果的恢复以“继续已有分支/PR”和“从当前目标分支新建分支”两条路径组织：架构文档定义基线与状态归属，扩展使用下的任务授权指引说明本地或远端分支接管、版本规划核验和恢复命令，项目 Skill 负责引导用户选择和持续执行，不另建任务状态机。

使用者流程以确定性检查点为中心：项目目标定义保证边界，架构文档定义 Workflow、Policy 与平台原生权限的职责，使用指引说明旧 Hook 的显式迁移和绑定 run/阶段的命令，PROD-002 记录检查点与迁移验收。外部工具调用不再自动进入 AgenticOps Gate；源码仓库自身的 Git Hook 与发布治理独立保留。

| 主题 | 总纲与权威文档 | 适用内容 |
|---|---|---|
| 产品定位与架构 | [项目目标](strategy/project-goals.md)、[v1 工程架构](architecture/agenticops-v1-architecture.md)、[术语表](glossary.md) | 产品边界、分层、稳定术语、流程检查点与 Agent 原生权限责任及迁移准绳 |
| 使用与维护 | [首次使用指引](usage-guide.md)、[必需 MCP 配置](usage/mcp-setup.md)、[Agent引导安装指引](usage/agent-guided-install.md)、[任务授权指引](usage/task-authorization.md)、[扩展使用索引](usage/README.md)、[维护指引](maintenance-guide.md)、[Skill 维护规范](skill-maintenance.md) | 首次安装到接管任务、Claude Code/Codex 的必需 Jira MCP 接线、GitHub 工具的自主选择边界、由 AI Agent 在空工作空间完成安装与初始化、脚本加载任务、准入、受控基线与实施授权、可选安装与 Source Pool 预下载、更新和回退、项目工作空间根 `./agenticops` 薄入口、受控仓库准备、当前工作空间会话中的任务执行上下文、任务恢复与精确清理、Skill 分类与发现接线、证据标签，以及日常运行和维护 |
| 安全与验证 | [权限与安全边界](security/permissions.md)、[Git SSH 授权指引](security/git-ssh-access.md)、[Claude 端到端验证](testing/e2e-claude.md)、[Codex 端到端验证](testing/e2e-codex.md) | 凭证、以工作空间为单位的 Agent 文件系统授权、Workflow 检查点与原生 Git/GitHub 写操作边界、访问诊断和端到端验收 |
| 产品合同 | [v1 用户故事总纲](user-stories/v1/README.md) | 稳定的产品能力、保护行为和验收证据 |

文档链接权威来源而不重复维护相同规则。具体工作项、进度、阻塞和验收由 Jira 管理，不在本树新增平行执行计划。

缺陷质量协作属于“使用与维护”主题：[质量检查与证据](usage/quality-checkpoints.md)说明检查点、非阻断修复策略调优、编码后 Jira Test 关联、Test Type 跟进、用户处置、非阻断 Jira 状态同步、PR Ready 核对和回写恢复；项目验证方式以 `projects/<project>/quality.json` 为准，修复策略以 `policies/defect-repair-strategies.json` 为通用事实源并允许项目只覆盖默认选择，数据结构以 `contracts/quality-*.schema.json` 为准，不在使用文档另设任务阶段。

旧版 AgenticOps 的设计、合同和操作说明以 Git Tag `v0.7` 为准，不在 v1 现役文档树保留重复版本。
