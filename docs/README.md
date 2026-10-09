# AgenticOps 文档总纲

[任务存档与恢复](usage/task-archive-and-resume.md)是使用者处理任务中断、重建与研发环境升级的场景入口；[扩展使用总纲](usage/README.md)说明它与授权、更新、质量及材料文档的职责关系。

任务中途升级后的接续以 Jira 任务为入口，GitHub 保存远端代码、PR 和 CI 成果；目标见[项目目标](strategy/project-goals.md)，本地运行与外部成果的边界见[工位合同](architecture/single-task-station.md#从任务事实接续)。[任务授权指引](usage/task-authorization.md#从-jira-任务接续)负责记录、缺项补全和重新接管，[更新与回退](usage/update-and-rollback.md#任务处理中升级后继续)负责产品切换顺序，共享 [ao-task-resume](../skills/shared/ao-task-resume/SKILL.md) 指导 Agent 使用现役入口。各文档不维护逐 epoch 迁移规则或独立交接台账。

[质量检查与证据](usage/quality-checkpoints.md)维护阶段评论、Issue Analysis 实施方案、Fix Details 修复总结和 GitHub PR Ready 回读闭环；提审含具体 Checks 例外，仍保留真实结果与独立合并授权。新客户端的兼容边界为 epoch 27，旧工位退出与重建遵循[更新与回退](usage/update-and-rollback.md)。

TapData 工位 Docker 测试环境以多份持久配置和唯一活动现场为目标：[测试环境 Skill](../projects/tapdata/skills/tapdata-test-env/SKILL.md)负责选择配置、补齐源码构建与装配并执行生命周期操作，[测试环境指引](../projects/tapdata/runbooks/test-environment.md)负责配置接口、Launcher 装配、外部 MongoDB、同一 Compose 项目管理与验收。缺少本轮产物时先按目标分支准备构建；API Server 仅在选择该组件时要求装配。源码构建约定继续由现有构建测试指引维护；这里不建立环境池或平行任务状态。新增目录为可选内容，不改变 `.agenticops/` 状态读写，该可选环境能力保持自身兼容边界；当前 epoch 以机器兼容清单为准。

流程授权与提示词边界由[任务授权指引](usage/task-authorization.md)维护来源同步、方案决定复用和清理内分支/PR 处置的操作说明；[Skill 维护规范](skill-maintenance.md)负责按需加载与能力缺失的接力原则。通用工位模板只导航当前 Project 的规则，项目 Skill 消费配置与已核验结果，不重复维护项目分支值或增加确认步骤；实际状态和授权判定仍由现役 Workflow、Policy 与工位合同负责。

TapData 正在进行的任务由研发选择恢复已有 run 或建立新 run；项目准入配置与操作步骤由 [TapData 任务技能](../projects/tapdata/skills/tapdata-task/SKILL.md#接管与恢复)维护，复用既有归档清理和分支续办机制，不复制旧授权或验收结论。

TapData 代码变更以 CI 集成测试为主要验证路径，Jira 关联 Test 检查继续保留，用例是否在当前会话开发由用户决定。CI 集成测试优化复用现有质量链：[质量检查与证据](usage/quality-checkpoints.md)维护方案与报告的字段、缺口确认及兼容边界；[TapData 集成测试](../projects/tapdata/skills/tapdata-ci-test/SKILL.md)和[构建测试指引](../projects/tapdata/runbooks/build-test-and-local-run.md)负责应用 Wiki 分级、用例开发执行和报告分析。测试级别定义仅从中央 Wiki 获取，本文不维护副本；真实业务验收与产品回归分别记录。

TapData 分支修复与测试维护由[构建测试指引](../projects/tapdata/runbooks/build-test-and-local-run.md#分支修复与测试维护)集中定义：release 修复先核查 develop，业务与集成测试分开提交，用户回补后在 release 独立验证。任务技能负责授权与交接，集成测试技能负责 develop 用例及报告；共同质量合同继续处理具体未覆盖范围，不新增状态或通用门禁。

任务清理采用六阶段自动执行与成果验收；完整盘点、异常接管、分支处置及当前 epoch 恢复边界由[工位合同](architecture/single-task-station.md#阶段式清理)统一定义，操作入口复用任务授权指引，PROD-003 覆盖阶段失败和接力验收。

共享需求设计以 [ao-requirement](../skills/shared/ao-requirement/SKILL.md) 为入口，帮助研发理解项目现状、需求取舍及验收并形成可实施方案；维护面与业务工位共用方法，项目规则提供上下文。该技能负责设计变更同步、冲突核查与交接：每项设计内容只有一个现役权威来源，其它指引引用该来源，设计改变时同步修订。[Skill 维护规范](skill-maintenance.md)维护共享资产归属、安装、发现与清理合同，[工程架构](architecture/agenticops-v1-architecture.md)维护职责边界，[更新与回退](usage/update-and-rollback.md)维护旧安装资源补齐及恢复操作。具体需求、评审和验收记录在 Jira，不另建执行计划；历史决策记录仅辅助问题定位，不承载现役设计或作为功能开发、修复及验收依据。

共同验证材料的失效范围由[质量检查与证据](usage/quality-checkpoints.md#共同验证材料)维护，契约版本与历史重放由[标准契约](../contracts/README.md)说明；来源同步只证明所属仓的来源关系，不替代跨仓测试或方案授权。兼容边界复用工位 epoch 与既有升级流程。

TapData 项目开发采用“现有任务技能导航、两份开发指引按需阅读”： [TapData 开发指引](../projects/tapdata/runbooks/tapdata-development.md)覆盖源码定位、配置、构建、启动与加载验证；[TapTest 开发指引](../projects/tapdata/runbooks/taptest-development.md)保留用例开发、环境配置、执行与结果分析能力，仅在用户选择当前会话处理 TapTest 时阅读；用例规范按需查询 Wiki 并结合任务源码核验，不依赖业务仓的用例技能。两份指引以常用操作顺序、成功标志和少量高频陷阱帮助 Agent 减少返工，不收录完整会话历史或一次性补丁。[构建与测试](../projects/tapdata/runbooks/build-test-and-local-run.md)维护 Maven、Java 测试与配置模板细节；授权和质量判定链接现役合同，不在指引重复维护。

通用工具 Hook 执行链退役后，[工程架构](architecture/agenticops-v1-architecture.md)维护 Agent 原生执行、Workflow 检查点和显式 Gate API 的边界；[标准契约](../contracts/README.md)维护 Manifest v3 的声明式接线；[更新与回退](usage/update-and-rollback.md)负责跨 epoch 原版本退出与重建，用户故事和端到端验证只维护对应验收合同，不另设迁移执行计划。

项目开发以[项目目标](strategy/project-goals.md)判断收益和范围，以[仓库指令](../AGENTS.md)指导维护协作；[维护指引](maintenance-guide.md#4-变更归属)说明如何选择实现归属、评估新增约束并验证效果，不另设规则体系或审批流程。[工程架构](architecture/agenticops-v1-architecture.md#2-分层)同时维护 Workflow 内共享检查、授权和 CLI 编排的职责，按实际依赖拆分，不以文件行数设拆分目标。

PR 正文传输完整性属于质量证据主题：[扩展使用总纲](usage/README.md)导航至正文发布与回读操作合同，Workflow 提供只读检查，TapData Skill 指导原生发布和恢复。不新增 GitHub 客户端、工位状态或 PR Ready 门禁。

清理范围决策与 Jira 阶段同步属于任务退出及流程连续性主题：[工位合同](architecture/single-task-station.md#外部同步回执恢复)维护有限回执恢复、证据冻结和兼容语义；[扩展使用总纲](usage/README.md)导航清理清单与同步待办的操作说明。只读展示复用原计划与账本，既不扩大删除授权，也不承诺外部写入必达。

质量源码定位与证据边界由[质量检查与证据](usage/quality-checkpoints.md#源码定位与实时核验)说明：按工位规范即时定位、历史重放与当前核验分离、检查点故障隔离及对外正文扫描；[扩展使用总纲](usage/README.md)负责导航，跨代际操作复用更新与回退指引，不另设任务迁移流程。

源码工作目录指纹由[工位合同](architecture/single-task-station.md#源码指纹与兼容边界)定义质量证据、扩仓和中断恢复共用的字节边界、禁用 Git textconv 的源码核验要求及 epoch；[更新与回退](usage/update-and-rollback.md)负责跨 epoch 的原版退出、purge 和重建操作，不在线迁移旧指纹。

项目规则入口的显式项目选择及配置化准入文档生成由[维护指引](maintenance-guide.md)说明；[首次使用指引](usage-guide.md#2-创建项目工位)负责首次初始化必填项目、既有绑定复用和跨项目重建的操作边界。无工位调用必须指定项目，工位运行从已有绑定读取，避免通用入口隐式选用业务规则。

功能开发准入由[质量检查与证据](usage/quality-checkpoints.md#功能方案完整性与一次决策包)说明 Q2 聚合检查、环境依赖阶段及 Jira 同阶段采集，[TapData 构建运行指引](../projects/tapdata/runbooks/build-test-and-local-run.md#方案阶段环境预检)负责环境输入、加载验证和不猜测配置的操作依据。计划声明与实际执行证据分开，不新增环境台账或审批。

同周期方案返工由[工位合同](architecture/single-task-station.md)定义原 run、追加基线和失败恢复，[任务授权指引](usage/task-authorization.md)维护 prepare/apply/abort 操作；质量文档负责受影响证据，项目 Profile 限定可增仓集合。

质量输入格式预检和完成等待合并的使用边界由[扩展使用总纲](usage/README.md)导航至质量与任务授权指引；质量执行事件增加未执行原因的兼容边界由机器工位契约管理，工位 epoch 以机器契约为准，升级继续遵守原版退出并 purge。

配置化工位清理由[任务授权指引](usage/task-authorization.md#配置化清理入口)说明两个独立名单、确认请求及 Agent 接力；[工位合同](architecture/single-task-station.md#成果导向清理计划版本-6)维护唯一现役版本 6 的保全、Git 复位、成果验收及旧协议退出边界，[机器契约](../contracts/station-reset.schema.json)约束持久计划。配置统一声明作用域、名称模式、对象类型和动作；结构身份由中央机器合同维护，源码继续 Git 保全，执行和恢复绑定冻结规则、扫描边界及具体对象完成回执。

[维护指引](maintenance-guide.md#5-验证)负责诊断检查、提交候选按影响验收、发布隔离四项验收、耗时报告与证据 v6 使用及维护审查的有效验收摘要；[INT-001](user-stories/v1/int-001-release-governance.md)规定验收完整性、失败失效和首次信任根升级边界。运行进度与性能验收结果仍以 Jira 为准。

TapData 的按需 Wiki 阅读由 [tapdata-wiki](../projects/tapdata/skills/tapdata-wiki/SKILL.md) 说明检索与源码核验边界；项目 Profile 仅引用中央登记的共享仓库。[架构总纲](architecture/agenticops-v1-architecture.md)定义中央共享材料归属；[工位源码与材料](usage/station-materials.md)负责共享仓库准备、显式更新和故障处理，不引入任务知识快照或自动刷新。

TapData 集成测试协作由 [tapdata-ci-test](../projects/tapdata/skills/tapdata-ci-test/SKILL.md) 负责用例分析、编写、执行与报告修复；[tapdata-task](../projects/tapdata/skills/tapdata-task/SKILL.md) 负责在研发节点调用、授权、质量记录与外部跟进。集成测试技能按需使用 tapdata-wiki；[构建、测试与本地运行](../projects/tapdata/runbooks/build-test-and-local-run.md) 保留具体操作与报告工具说明。技能能力缺失不增加流程门禁，已有验收条件仍由原质量合同处理，不建立第二套测试状态。

任务退出、运行身份与编码准备由[工位合同](architecture/single-task-station.md)统一定义：其中身份章节维护 `run_id` 的职责、固定格式、秒级冲突失败语义、历史读取兼容与变更边界；重置章节负责保留配置和独立源码，将正式档案发布到 Product Root 的 `.archive/<run-id>`，按任务独占目录回收运行产物，并核验源码成果、开发基线与空闲条件。该主题覆盖目录归属、一次范围确认、中断恢复、由工位 `git_name` 和 run 生成的分支及 PR 处置、以及 epoch 兼容边界；不覆盖工位卸载或自动恢复历史任务。项目目标负责方向，工位合同负责可执行语义与验收，使用指引和项目 Skill 负责入口与操作说明，机器契约约束持久字段，避免重复维护规则。

源码池仅承担下载加速：[工位合同](architecture/single-task-station.md)定义先缓存后独立源码的准备与恢复边界，[工位源码与材料](usage/station-materials.md)说明缓存位置、复用与清理；项目目标保持工位独立性。完整应用源码集 Profile 的现役承诺止于源码准备：[术语表](glossary.md)解释名称，[工位合同](architecture/single-task-station.md#9-tapdata-源码-profile-与运行边界)定义源码与未来运行能力的界线，TapData 构建运行指引负责另行构建、启动和实证的操作依据，不把接管成功当成应用可运行。

维护时的源码测试、候选安装快照和业务工位绑定由[维护指引](maintenance-guide.md#2-初始化测试工位)说明，避免把可变源码直接绑定为业务 Product Root；接管测试 Skill 按现役 Workflow 检查点和原生权限报告停止点。旧 Hook 的同 epoch 接线迁移由[常见问题](usage/faq.md)说明，跨 epoch 仍由更新指引负责原版退出。原版本清理器自身失效时的一次性空工位恢复归入[维护指引](maintenance-guide.md#旧版空工位的一次性恢复)，只说明产品维护导出与重建边界，不作为产品升级兼容入口。

本地执行与 Jira 同步的边界由项目目标和架构定义；质量使用指引负责初始快照、本地确认、非阻断同步及 PR 后警告汇总，并说明项目配置的消费者与只读工作流参考的边界；契约负责可恢复记录格式。

当前任务的读写校验由[工位合同](architecture/single-task-station.md)说明：复用版本化状态契约、保留合法恢复状态、拒绝损坏数据且不在线修复；Gate 上下文复用只读状态入口，不另维护宽松格式。

本文是现役人读文档的结构入口。新建或调整文档时，先在本页或对应主题的子级总纲明确目标、范围、层级、职责和导航关系；再细化正文。仅当文档过长，或稳定内容被多个页面复用时，才拆分子文档。

现役工位采用单任务模型：source 保存完整独立工程，config 保存持久配置，runtime 是唯一运行现场，.agenticops 只绑定一个 current 与 operation；正式档案独立保存在 Product Root 的 `.archive/`，不随工位清理删除。[项目目标](strategy/project-goals.md)负责方向，[工程架构](architecture/agenticops-v1-architecture.md)负责分层，[工位合同](architecture/single-task-station.md)负责身份、四操作、恢复及可复用验收边界；机器基线见 [engineering-baseline](../contracts/engineering-baseline.schema.json)。功能存在不等于真实 TapData 应用已验证运行，执行证据与发布结论仍在 Jira。

生成与清理先在同版本形成闭环：初始化、任务处理、归档释放或清理、工位 purge、再次初始化。跨版本升级是第二层编排：升级器只比较唯一的 `station_state_epoch`；值变化时必须确认 Product Root 的工位登记为空，原版本负责把全部任务通过 release 或 clean 正式归档并 purge，目标版本只生成新工位，不在线迁移任务或解释旧 Runtime 状态。登记缺失、损坏或无法读取时停止切换。[更新与回退](usage/update-and-rollback.md)维护这个使用顺序。

TapData 活动仓库以 repositories.json 为准；docs/docs-en 已解除，t-layer3-test 保留为可选验证依赖。新版本不保留旧清理身份映射，旧现场由原版本处理，不删除已有源码、Git refs 或材料。

项目 Skill 提供使用入口与行动顺序：[TapData 任务引导](../projects/tapdata/skills/tapdata-task/SKILL.md)负责任务协作，[分支对齐](../projects/tapdata/skills/tapdata-align-branches/SKILL.md)负责工位分析与独立开发目录的项目操作；现役工位代际及支持范围由[机器兼容清单](../contracts/station-state-compatibility.json)定义，[更新与回退](usage/update-and-rollback.md)负责原版本退出、purge 与目标版本重建。维护 Agent 的协作优化由[维护指引](maintenance-guide.md)说明模型依据、平台能力边界与验证方式，[Skill 维护规范](skill-maintenance.md)负责指令审查标准，根 `AGENTS.md` 保存日常协作约定，`skills/` 保存初始化、接管测试和[变更审查](../skills/ao-review-change/SKILL.md)的具体指引。变更审查 Skill 只整理现有 Story Gate 材料并指导检查，验收、批准和授权仍由原有机制负责。目标是在已有授权内持续完成工作，减少重复确认和重复验证；不改变产品门禁、授权或工位状态契约。

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
| 安全与验证 | [权限与安全边界](security/permissions.md)、[Git SSH 授权指引](security/git-ssh-access.md)、[Claude 端到端验证](testing/e2e-claude.md)、[Codex 端到端验证](testing/e2e-codex.md) | 凭证、以工位为单位的 Agent 文件系统授权、Workflow 检查点与原生 Git/GitHub 写操作边界、访问诊断和端到端验收；[维护验证](maintenance-guide.md#5-验证)负责源码规则测试、真实交互测试与固定验收的分工 |
| 产品合同 | [v1 用户故事总纲](user-stories/v1/README.md) | 稳定的产品能力、保护行为和验收证据 |

文档链接权威来源而不重复维护相同规则。具体工作项、进度、阻塞和验收由 Jira 管理，不在本树新增平行执行计划。

缺陷质量协作属于“使用与维护”主题：[质量检查与证据](usage/quality-checkpoints.md)说明检查点、非阻断修复策略调优、编码后 Jira Test 关联、Test Type 跟进、用户处置、非阻断 Jira 状态同步、PR Ready 核对和回写恢复；项目验证方式以 `projects/<project>/quality.json` 为准，修复策略以 `policies/defect-repair-strategies.json` 为通用事实源并允许项目只覆盖默认选择，数据结构以 `contracts/quality-*.schema.json` 为准，不在使用文档另设任务阶段。

旧版 AgenticOps 的设计、合同和操作说明以 Git Tag `v0.7` 为准，不在 v1 现役文档树保留重复版本。
安装目录包含本树的人读文档，供项目 Skill 和 Runbook 的相对链接直接读取；安装、更新与回退共享文档范围，不复制文档到工位。缺失资源补齐的操作由[更新与回退](usage/update-and-rollback.md)维护，维护工具仍不进入安装目录。

## 空闲工位源码生命周期

开发分支归位、受管基线回收及空闲刷新由[工位合同](architecture/single-task-station.md)定义，操作入口见[工位源码与材料](usage/station-materials.md)，退出仍使用既有清理与归档流程。

目录长期归属与清理操作身份快照的职责、epoch 26 配置与扫描边界冻结、跨会话重新盘点及完成回执保护由[工位合同](architecture/single-task-station.md)维护；原版本异常恢复仅属于受控维护，不作为新版在线迁移能力。

工位绑定的统一读取、格式校验与 epoch 复核由[工位合同](architecture/single-task-station.md)定义；Bootstrap 与项目工具复用 Workflow 的只读访问模块。升级指引只负责版本切换顺序，不维护逐工具 schema 支持列表。

正式验收的持续执行、慢检查诊断和用户优化决策由[维护指引](maintenance-guide.md#5-验证)维护；检查耗时仅触发诊断提示，取消与失败处理仍由验收器负责。

macOS `.DS_Store` 的清理边界由[工位合同](architecture/single-task-station.md#成果导向清理计划版本-6)维护：中央清理名单声明元数据名称，Workflow 在已授权清理内处理普通未跟踪文件；旧计划的成果保全决定继续有效，不遍历保留目录或 Git 内部目录。
