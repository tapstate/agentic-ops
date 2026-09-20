# AgenticOps 文档总纲

质量输入格式预检和完成等待合并的使用边界由[扩展使用总纲](usage/README.md)导航至质量与任务授权指引；质量执行事件增加未执行原因的兼容边界由机器工位契约管理，本版 epoch 为 10，升级继续遵守原版退出并 purge。

配置化工位清理由[任务授权指引](usage/task-authorization.md#配置化清理入口)说明两个独立名单、确认请求及失败恢复入口；[工位合同](architecture/single-task-station.md#配置化清理计划版本-4)维护版本 4 的源码先复位顺序及与版本 3 的兼容边界。名单负责分类，归属与生命周期检查仍是副作用前提，不接入构建工具执行器。

[维护指引](maintenance-guide.md#5-验证)负责诊断检查、绑定候选的正式四项验收、耗时报告与证据 v5 使用；[INT-001](user-stories/v1/int-001-release-governance.md)规定验收完整性、失败失效和首次信任根升级边界。运行进度与性能验收结果仍以 Jira 为准。

TapData 的按需 Wiki 阅读由 [tapdata-wiki](../projects/tapdata/skills/tapdata-wiki/SKILL.md) 说明检索与源码核验边界；项目 Profile 仅引用中央登记的共享仓库。[架构总纲](architecture/agenticops-v1-architecture.md)定义中央共享材料归属；[工位源码与材料](usage/station-materials.md)负责共享仓库准备、显式更新和故障处理，不引入任务知识快照或自动刷新。

TapData 集成测试协作由 [tapdata-ci-test](../projects/tapdata/skills/tapdata-ci-test/SKILL.md) 负责用例分析、编写、执行与报告修复；[tapdata-task](../projects/tapdata/skills/tapdata-task/SKILL.md) 负责在研发节点调用、授权、质量记录与外部跟进。集成测试技能按需使用 tapdata-wiki；[构建、测试与本地运行](../projects/tapdata/runbooks/build-test-and-local-run.md) 保留具体操作与报告工具说明。技能能力缺失不增加流程门禁，已有验收条件仍由原质量合同处理，不建立第二套测试状态。

任务退出与编码准备由[工位合同](architecture/single-task-station.md)统一定义：重置工位保留配置、独立源码和正式档案，按任务独占目录回收运行产物，并核验源码成果、开发基线与空闲条件。该主题覆盖目录归属、一次范围确认、中断恢复、由工位 `git_name` 和 run 生成的分支及 PR 处置、以及 epoch 兼容边界；不覆盖工位卸载或自动恢复历史任务。项目目标负责方向，工位合同负责可执行语义与验收，使用指引和项目 Skill 负责入口与操作说明，机器契约约束持久字段，避免重复维护规则。

源码池仅承担下载加速：[工位合同](architecture/single-task-station.md)定义先缓存后独立源码的准备与恢复边界，[工位源码与材料](usage/station-materials.md)说明缓存位置、复用与清理；项目目标保持工位独立性。

原版本清理器自身失效时的一次性空工位恢复归入[维护指引](maintenance-guide.md#旧版空工位的一次性恢复)，只说明产品维护导出与重建边界，不作为产品升级兼容入口。

本地执行与 Jira 同步的边界由项目目标和架构定义；质量使用指引负责初始快照、本地确认、非阻断同步及 PR 后警告汇总，契约负责可恢复记录格式。

本文是现役人读文档的结构入口。新建或调整文档时，先在本页或对应主题的子级总纲明确目标、范围、层级、职责和导航关系；再细化正文。仅当文档过长，或稳定内容被多个页面复用时，才拆分子文档。

现役工位采用单任务模型：source 保存完整独立工程，config 保存持久配置，runtime 是唯一运行现场，archive 保存正式档案，.agenticops 只绑定一个 current 与 operation。[项目目标](strategy/project-goals.md)负责方向，[工程架构](architecture/agenticops-v1-architecture.md)负责分层，[工位合同](architecture/single-task-station.md)负责身份、四操作、恢复及可复用验收边界；机器基线见 [engineering-baseline](../contracts/engineering-baseline.schema.json)。功能存在不等于真实 TapData 应用已验证运行，执行证据与发布结论仍在 Jira。

生成与清理先在同版本形成闭环：初始化、任务处理、归档释放或清理、工位 purge、再次初始化。跨版本升级是第二层编排：升级器只比较不兼容标记；标记变化时必须确认 Product Root 的工位登记为空，原版本负责完成任务和 purge，目标版本只生成新工位，不在线迁移任务或解释旧 Runtime 状态。登记缺失、损坏或无法读取时停止切换。[更新与回退](usage/update-and-rollback.md)维护这个使用顺序。

TapData 活动仓库以 repositories.json 为准；docs/docs-en 已解除，t-layer3-test 保留为可选验证依赖。新版本不保留旧清理身份映射，旧现场由原版本处理，不删除已有源码、Git refs 或材料。

维护 Agent 的协作优化由[维护指引](maintenance-guide.md)说明模型依据、平台能力边界与验证方式，[Skill 维护规范](skill-maintenance.md)负责指令审查标准，根 `AGENTS.md` 保存日常协作约定，`skills/` 保存初始化与接管测试的具体指引。目标是在已有授权内持续完成工作，减少重复确认和重复验证；不改变产品门禁、授权或工位状态契约。

TapData 的测试缺口分析、Java 影响范围和 Maven 模块测试属于项目构建与验证适配：[构建、测试与本地运行](../projects/tapdata/runbooks/build-test-and-local-run.md)说明用例判断依据、框架缺口处置、有效 Maven 模型的采集、跨仓消费关系、原生 Maven 执行清单、依赖 Jar 及报告核验的使用边界；项目脚本只准备清单和分析证据，不执行测试或改变任务阶段。项目任务 Skill 引导 Agent 在已有验收方案内自主分析，不替代共同质量检查点。

该指引的“本地工位配置输入”集中维护联调前用户需提供的数据库、工具链和秘密引用，导航至项目 TM/FE 配置模板。模板是待目标分支验证的输入，不是自动启动器或应用已运行的证明；版本对应的配置键仍以目标源码为准。

CI 用例开发与质量核对继续由上述 TapData 构建指引承载：从确认预期定义断言、在业务任务内开发、验证旧新行为并接回模块全量执行，同时说明 PR CI 的原生报告回读、运行及源码绑定与范围披露；质量检查与证据文档负责结果的流程绑定，不另设用例任务状态机。

任务级集成测试报告处理也由上述构建指引说明：原生获取报告后，项目解析器读取本地 XML 或下载的 ZIP，保留多仓多 PR 结果、执行状态和证据缺口。跨仓消费版本未核实只作披露，不新增门禁；解析器不执行网络调用、测试或任务状态写入。

已有开发成果的恢复以“继续已有分支/PR”和“从当前目标分支新建分支”两条路径组织：架构文档定义基线与状态归属，扩展使用下的任务授权指引说明本地或远端分支接管、版本规划核验和恢复命令，项目 Skill 负责引导用户选择和持续执行，不另建任务状态机。

使用者流程以确定性检查点为中心：项目目标定义保证边界，架构文档定义 Workflow、Policy 与平台原生权限的职责，使用指引说明旧 Hook 的显式迁移和绑定 run/阶段的命令，PROD-002 记录检查点与迁移验收。外部工具调用不再自动进入 AgenticOps Gate；源码仓库自身的 Git Hook 与发布治理独立保留。

| 主题 | 总纲与权威文档 | 适用内容 |
|---|---|---|
| 产品定位与架构 | [项目目标](strategy/project-goals.md)、[v1 工程架构](architecture/agenticops-v1-architecture.md)、[术语表](glossary.md) | 产品边界、分层、稳定术语、流程检查点与 Agent 原生权限责任及迁移准绳 |
| 使用与维护 | [首次使用指引](usage-guide.md)、[必需 MCP 配置](usage/mcp-setup.md)、[Agent引导安装指引](usage/agent-guided-install.md)、[任务授权指引](usage/task-authorization.md)、[扩展使用索引](usage/README.md)、[维护指引](maintenance-guide.md)、[Skill 维护规范](skill-maintenance.md) | 首次安装到接管任务、Claude Code/Codex 的必需 Jira MCP 接线、GitHub 工具的自主选择边界、由 AI Agent 在空工位完成安装与初始化、脚本加载任务、准入、受控基线与实施授权、独立源码准备与持久材料复用、更新和回退、项目工位根 `./agenticops` 薄入口、受控仓库准备、当前工位会话中的任务执行上下文、任务恢复与精确清理、Skill 分类与发现接线、证据标签，以及日常运行和维护 |
| 安全与验证 | [权限与安全边界](security/permissions.md)、[Git SSH 授权指引](security/git-ssh-access.md)、[Claude 端到端验证](testing/e2e-claude.md)、[Codex 端到端验证](testing/e2e-codex.md) | 凭证、以工位为单位的 Agent 文件系统授权、Workflow 检查点与原生 Git/GitHub 写操作边界、访问诊断和端到端验收 |
| 产品合同 | [v1 用户故事总纲](user-stories/v1/README.md) | 稳定的产品能力、保护行为和验收证据 |

文档链接权威来源而不重复维护相同规则。具体工作项、进度、阻塞和验收由 Jira 管理，不在本树新增平行执行计划。

缺陷质量协作属于“使用与维护”主题：[质量检查与证据](usage/quality-checkpoints.md)说明检查点、非阻断修复策略调优、编码后 Jira Test 关联、Test Type 跟进、用户处置、非阻断 Jira 状态同步、PR Ready 核对和回写恢复；项目验证方式以 `projects/<project>/quality.json` 为准，修复策略以 `policies/defect-repair-strategies.json` 为通用事实源并允许项目只覆盖默认选择，数据结构以 `contracts/quality-*.schema.json` 为准，不在使用文档另设任务阶段。

旧版 AgenticOps 的设计、合同和操作说明以 Git Tag `v0.7` 为准，不在 v1 现役文档树保留重复版本。
