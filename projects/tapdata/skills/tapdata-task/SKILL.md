---
name: tapdata-task
description: 在单任务工位执行 TapData Jira 研发任务，覆盖工程接管、设计、多仓实现、开发运行环境、IT、按用户选择处理 TapTest、PR/CI 与归档清理。
metadata:
  product: agenticops
---

# TapData 单任务工位

需要按指定连接器编译、注册或编译后注册时，使用 [tapdata-connectors](../tapdata-connectors/SKILL.md)，复用当前工位源码和明确目标环境，不通过重建整套开发环境更新单个连接器。

先读工位 AGENTS、当前 Project Profile、准入规则和本 Skill。以 station.json 的 Product Root 为准，memory 只作历史线索。工位 config/source/runtime 分离配置、完整源码和唯一运行现场；正式档案在 Product Root 的 `.archive/<run-id>`，.agenticops 只保存一个当前任务与操作。

写请求固定 issue/run，advance 另带 expected-stage；生命周期与范围操作带 expected-revision 和稳定 operation-id。拒绝后先回读，不自动用新 run/revision 重放旧决定。interaction-path 分配当前证据路径，独立归档不冻结开发；任务释放后不写旧运行证据。

## 接管与恢复

接管前、恢复及每个新工作段先按[任务级协调入口](../../../../docs/architecture/single-task-station.md#执行接管当前源码)回读 Jira 任务身份与完整评论分页，使用 coordination check/prepare/complete 核验接管。报活更新自己的专用评论，交接需要时核实停写并取得人工决定；不得仅凭本地 execution claim 或旧回读继续。清理后回读协调待办并 end/complete，只结束本工位记录，不夺回已交接任务。旧安装按其合同，不手改状态迁移。

用户以 Jira key 要求继续已有任务、跨版本升级后接续，或升级前保全进展时，使用共享 [ao-task-resume](../../../../skills/shared/ao-task-resume/SKILL.md)。Jira/GitHub 保存有效设计、决定、真实历史基线与完整 Head、验证和剩余事项；缺项由 AI 核实并协助用户补齐。该技能只整理可信输入，接管、授权、质量和状态写入仍使用本 Skill 的现役入口，不解析旧 epoch 或复制旧 PASS。

需要配置、部署、更新、检查、切换或卸载工位 Docker 开发环境时，使用 [tapdata-test-env](../tapdata-test-env/SKILL.md)，正常退出先由脚本处理已支持的运行资源，返回未完成容器时再由该 Skill 接力。需要应用开发环境或启动联调时，读取 [TapData 开发指引](../../runbooks/tapdata-development.md)。TapData 代码变更以 IT为主要验证路径，使用 [tapdata-ci-test](../tapdata-ci-test/SKILL.md)按 Wiki 规范和任务源码完成覆盖判断、用例编写、测试及报告分析。Jira 关联 Test 检查始终保留；用例是否在当前会话开发由用户决定，不因发现关联 TapTest 或覆盖缺口自动进入 TapTest 开发。仅在用户选择当前会话处理 TapTest（t-layer3-test）开发或执行时，读取 [TapTest 开发指引](../../runbooks/taptest-development.md)，在已确认范围和授权内推进。已有明确选择直接复用；需要追加仓库或改变范围时按同周期方案返工处理。TapTest 不进入 Java Maven 流程，两者分别返回证据，主流程统一处理授权和质量记录。

一般架构、实现和非集成测试审查需要 Wiki 参考资料时，使用 [tapdata-wiki](../tapdata-wiki/SKILL.md)，按项目 Profile 引用阅读中央共享 Wiki，并以当前工位 source 内任务对应版本源码核验。集成测试需求、设计、编写和报告分析统一先进入 [tapdata-ci-test](../tapdata-ci-test/SKILL.md)，由它按需使用 tapdata-wiki；传递本轮已有且适用的查询结果，避免重复查询。新接管入口按[中央共享 Wiki 合同](../../../../docs/usage/station-materials.md#中央共享-wiki)自动准备并更新 Wiki；读取更新结果，不额外重复刷新。原 run 和在途操作恢复跳过；更新失败或不可用时说明缺口，继续有充分源码依据的工作。

1. 先按[执行接管](../../../../docs/architecture/single-task-station.md#执行接管当前源码)核对当前会话和已有占用，再只读核对 current-task.json 与 operation.json：仅 current=null 且操作不存在或 done 时可接新任务，revision 从当前信封读取。Jira 正在进行的任务由研发决定恢复已有 run 或使用新 run，已有明确决定不重复询问。选择恢复时核验原 run；找不到可恢复的 run 时说明缺项，不伪造恢复。选择新 run 时，已有活动任务须先核实成果保全或明确放弃，再由脚本清理任务，保留用户配置，不能直接覆盖。未完成操作按原 operation_id、expected_revision 和请求恢复，不用新 run 绕过在途操作。
2. 新任务先真实读取 Jira 类型、状态、经办人与当前用户，按 Project 准入核验；不凭标题、历史或默认仓推断。确定产品版本/主仓分支以及完整 Profile，缺失时询问该事实。
3. 新接管先按[更新选择合同](../../../../docs/usage/update-and-rollback.md#新任务前检查与更新目标设计)处理入口返回的更新选择；复用本次有效 SHA 决定，不从 Agent 内嵌套启动新 Agent。空闲工位执行 task.py takeover --issue-key <issue> --task-class <class> --version <已确认主仓分支> --profile full-application --operation-id <op-id> --expected-revision <revision> --dir <station>。用户已选择当前会话处理 TapTest 且需要测试仓时用 --optional-repository tapdata/t-layer3-test 加入；冻结后新增需求按下述同周期方案返工处理。模块明确覆盖用 --explicit-branch owner/repo=branch，不能覆盖主仓到不同产品版本。
4. 失败保留原 run/op/request 恢复；完成后 repository context 核验完整 source、ref/SHA 与基线摘要，不把远程页面或缓存当已确认基线。Git 操作只使用 context 的 source 路径，不启动嵌套 Agent。
5. 修改仓通过 repository add --repo <repo> --work-branch <branch> --base-branch <PR目标> --scope <范围> --verification <方式> --operation-id <新op-id> --expected-revision <revision> --expected-run-id <run> --issue-key <issue> --dir <station> 登记并准备工作分支；只允许冻结基线内仓库。范围变化使旧授权失效。
6. 续办原 run 不重写已有基线。新 run 续办旧分支时，用 takeover --continuation-input <json> 显式给出以仓库 ID 为键的 work_branch/baseline_sha/expected_head，均需真实 Git/PR 核验；旧报告不自动变成新验收。随后 repository add 使用 --expected-head 核对续办分支。追加当前 Profile 允许的可选仓库时，使用[同周期方案返工](../../../../docs/usage/task-authorization.md#同周期方案返工)，保留成果并重新确认受影响方案和授权；更换工程版本或超出支持范围时须归档清理后重接，不能偷改冻结清单。
7. 源码准备并核验基线后，按返回的 `takeover.next_step` 和 `sync_actions` 接续，按[四项接管标记](../../../../docs/usage/quality-checkpoints.md#四项接管标记)先读取 Jira 四项字段和当前连接登录用户，执行 jira_watermark prepare、原生整体写入、complete 回读；字段 ID 取 Profile，不按显示标题定位。既有标记先核实旧 run 处置，同一 run 保留原版本和时间；未知结果先核对原操作。
8. 四项标记 verified 后，在本轮主动处理 `takeover_comment`，不等待 Q1 或研发提醒。按[摘要发送与防重](../../../../docs/usage/quality-checkpoints.md#摘要发送与防重)取得稳定 id 和正文，prepare_write 携带写前新回读的 takeover_snapshot，原生发布后核验评论 ID、正文和作者。已有授权直接复用，已验证不重发，未知评论只回读原正文。标记失败暂停摘要；同步异常保留待办，继续无依赖研发。进入 task_intake 后按 Project status_sync 节点处理状态，源码准备不是接管完成；本轮结束汇总 `takeover.remaining` 和原因，未完成显示“接管待收尾”。

## 清理与归档

使用[清理与退出](../../../../docs/usage/task-authorization.md#清理与退出)的统一入口。先由脚本预检，Agent 解释 task/station 范围、源码保全缺口和唯一数据，复用用户有效决定后执行。任务清理保留配置，工位清理删除用户数据并保留初始接线；归档独立，不是清理前置。现有安装须先核验支持的入口和 epoch，不用新命令处理旧状态。

失败沿原 operation 恢复，先核实外部未知结果，再处理原范围的剩余事项。目录删除开始后不换新操作扩大范围。分别说明任务是否解绑、清理是否完成、执行占用是否释放；不手改状态或补造回执。程序自产合法状态无法继续时反馈产品缺陷，外部数据与环境缺项由 Agent 和研发补齐。

## 准入、设计和多仓库

新任务在 Q2 授权前执行 `task.py source-readiness --issue-key <issue> --expected-run-id <run> --dir <station>`；它刷新目标引用并核验完整工程与工作分支。若提示目标正常推进，展示冻结基线、工作 Head、最新目标 SHA，并请用户决定保留基线且在 PR 前按既有规则同步，或归档清理后重接。选择保留时执行相同命令并追加 `--confirm-digest <快照摘要> --decision-ref <真实决定来源>`。随后 grant 与 advance 会重新核对这些事实；变化时重新准备。脏源码、身份错误、分叉及远端核验失败按返回原因处理；ignored 内容不因未登记就阻塞源码准入，也不因此取得删除权限。

`feature_change` 表示已有明确范围的功能开发，不接入需求任务。通过 `checklist` 读取验收标准、目标仓库和验证方式；功能任务不要求独立风险等级，具体风险与回滚纳入实施方案统一确认；验证方式可由研发确认，不强制写入 Jira `customfield_10049`。使用项目 `quality-feature.json`，在 Q2 前用 `task.py record --key implementation_plan --input <方案.json>` 记录目标、实现变化、验收场景、风险和回滚，并记录 `scope_boundary`。每个验收场景要对应具体检查项、预期和执行方式；不填写假根因、问题版本或 `fix_plan`。下文版本规划、根因与修复线规则仅适用于 `defect_fix`。

- 用 `task.py checklist` 获取机读准入要求，不得凭聊天猜测。
- `defect_fix` 在 Q1 从 `task.py checklist --json` 或 `task.py next` 读取通用 `repair_strategy`。默认只向用户显示一行当前策略，不增加确认；Q2 按 `planning_guidance` 生成方案并披露应用结果。用户要求调整时，在 Q2 确认前使用 `task.py repair-strategy set/clear` 并绑定当前 run；策略不可用、未应用或偏离只报告 warning，不能阻塞主流程或替代根因、范围、验证和授权检查。策略正文只来自中央 Policy，不复制到本 Skill。
- 缺陷保留 Jira 初始版本；本地经用户确认的正确版本驱动当前 run，影响版本不映射分支。所有 release 修复必须按[分支修复与测试维护](../../runbooks/build-test-and-local-run.md#分支修复与测试维护)先核查 develop，再选择 develop 修复验证后交用户回补，或参考 develop 正确实现直接修复 release；未知结论先补依据。方案明确各仓基线、实施与验证顺序、业务/集成测试提交拆分和用户回补交接；跨分支不得静默改当前 run 基线或复用其它分支授权，复用现有任务、归档与接管机制分别保留各分支成果和待办。
- 缺陷在输出根因、修改范围或修复方案前，必须用已确定的 `primary_branch` 调用 TapData 分支对齐：`python3 <agenticops-root>/projects/tapdata/scripts/align_branches.py show --tapdata-root <tapdata-root> --version <primary_branch> --repository <候选任务仓库> --cache-file <station>/.agenticops/git-ref-cache-v2.json --json`。`--tapdata-root` 是包含各模块仓库的产品目录，必须含主仓但不要求其它仓库齐全。读取顶层 `outcome`、`scope`、`blockers`、`checked_at` 和全部 `rows` 的 `repository`、`local`、`target_branch`、`target_sha`、`target_status`、`reason`、`refs`；模块使用项目 `version-branch-alignments.json` 与脚本核验返回的分支，不把主仓 release 名字机械套给模块，也不在 Skill 固定分支值。`not_covered`、`absence_unverified` 或 `unresolved` 不可作为目标仓库基线；版本与分支冲突不能以接受风险放行。
- 分支对齐只证明“该仓库在本次产品修复线应使用哪个分支”，不能单独证明缺陷属于该仓库。结合 Jira 组件/标签、问题现象、堆栈或文件路径、复现结果和目标分支源码，按以下结论展示仓库与分支后才给出方案：有可回查证据唯一指向一个仓库时，输出“建议分析/修复仓库”表，列出仓库、目标分支、SHA、分支推导理由、refs 新鲜度、核验时间和锁定证据；证据指向多个仓库时，输出“问题候选”表，逐项列出上述字段和候选理由；只有版本关系或无法唯一归属时，输出完整对齐列表并请用户确认优先分析的仓库。
- 在登记目标仓库并完成必要源码分析后、签发任何 `task_execution` 授权前，必须以**一轮方案确认**完整展示并请求明确确认：① 实施方案——缺陷使用根因及证据、修改范围、修复方式、风险和回滚；功能使用目标、实现变化、范围、风险和回滚；② 验收方案——每个检查项的用例或场景、复用/新增、执行方式、预期结果、目标仓库及验证责任人；③ 后续动作及授权范围——确认可执行的编码、测试、提交、推送、PR 和 Jira 回写动作，用户限制优先。验证失败或事实变化时停止相应步骤；Jira 同步失败只记录警告。用户确认前不得记录为已确认方案、签发实施授权或开始实现；用户调整方案时展示完整有效方案及变化，复用已明确覆盖该变化的真实决定，只补问未决内容或超出原授权的范围；实际方案绑定变化后仍按现役入口撤销、重新绑定授权。确认后以同一确认来源固化 Q1/Q2、对应方案事实及 `workflow/authorization.py grant`；不得再为 Q3 的成功事实重复索取“接受首轮验证”。
- 按 `checklist` 返回的 `quality_mode` 处理缺项。缺陷 `recorded_decision` 模式一次列全缺口并继续无依赖的分析，在质量检查点由用户决定处理；其它类型仍按各自准入规则。事实不可信或基线无法确定时停止对应步骤。
- 每个目标仓库登记仓库、工作分支、基线分支、范围和验证方式。
- 接管不要求已创建或关联 Test。完成受控基线后，Agent 与用户在 Q2 确认修复方案、验收场景、预期和验证方式；如何定义、编写、创建或复用 Test 由用户与 Agent 处理，AgenticOps 只引导、记录、跟进和核对。缺陷编码完成后再通过 Jira「已链接工作项」创建或关联 Test；功能的自动化用例随功能代码开发、执行和验收，正常自动化路径不要求独立 Jira Test；IT 手动替代路径必须关联 Manual Test，已有的关联 Test 仍须核对。使用 `quality.py status/apply` 完成 Q1、Q2 的记录与确认，然后进入 implementation。关联用例需要新增或修改时，在既有方案讨论中说明缺口，由用户选择当前会话开发或交其它会话/人员处理；外部处理须明确责任和所需结果，保留未完成事实及现有验收条件，继续不依赖它的工作，不默认创建 Xray 或编写 TapTest。具体输入和恢复方法见 [质量检查与证据](../../../../docs/usage/quality-checkpoints.md)，项目标准来自当前任务选择的质量配置。
- 功能与缺陷在相关人工节点先按[统一决策包](../../../../docs/usage/quality-checkpoints.md#jira-全流程统一决策包)执行 jira_status collect，一次收集本阶段全部缺项并记录真实确认；未来结果不提前索要。缺陷处理过程中按 Project 转换配置提前采集 Tests Passed 所需属性：Q2 固化分类和根因依据，仓库确认时形成 Module 依据，Q2 将实施方案写入 Issue Analysis，Q4 将修复总结写入 Fix Details；评论只做阶段总结，验收方案确认 Tester、自动化属性和 Xray 关联，版本规划只作为选择 Fix Version 的依据。需要责任人选择的枚举、人员、Module、Fix Version ID 和测试例外不得自动猜测；无法可靠补齐时留到状态同步节点跳过并在 PR Ready 提示。
- 缺陷 Q2 前用 `task.py record --key fix_plan` 记录根因、范围、修复方式、风险与回滚。修复后用例尚未编码时把 `target_revision` 写为 `pending`；先确认稳定用例/方式，执行前用 `item` 绑定精确代码。只补充代码版本不会要求重新选择同一用例；改步骤、预期、范围或方式仍须重新确认。
- 一个检查项对应一个用例和一种方式；同检查点可有不同方式的多项。修复前不可执行须说明原因，修复后项未到检查点不算失败。`Manual` 由用户执行；`TapTest` 继续回读关联用例及 Jira 状态，仅在用户选择当前会话开发或执行时读取其指引；`Unit` 核对产品工程的单元测试和 IT。TapCE 当前不纳管，不算通过；若因此无法形成受管验收或 Jira Validator 阻塞，请用户调整 Jira 或验收方案并重新读取事实。AgenticOps 工具不承载用例开发或环境执行器；Agent 使用原生工具按授权编写和执行用例，质量接纳仍使用现有闭环。
- 新增仓库或修改分支、范围、验证方式后展示变化，复用已覆盖变化的有效决定；按现役范围修订或同周期返工入口重新核对受影响质量事实并签发授权。缺少有效决定才补充确认，未变项不重跑。
- Workflow 检查点失败时展示原因、缺失事实和停止点；先补齐所需事实，不手改状态绕过。原生工具审批由平台处理。

## 功能任务的 Jira 协作

功能开发仅接入 Story，经办人必须正确。Analyzed 可新接管；In Progress（正在进行）由研发选择恢复已有 run 或建立新 run，不自动退回 Jira。新 run 记录研发决定与交接来源，按接管与恢复步骤核验既有分支、基线和成果；重新建立本轮方案、授权和验收记录，不继承旧授权或旧 PASS。其它状态仍按项目准入拒绝，不因新 run 选择放宽。

Agent 按以下顺序主动处理，不等待研发提醒状态流转：

1. 接管时读取 issue type、状态、负责人、描述、关联工作项及可用转换和表单元数据。先利用已有信息，不为项目未定义的字段增加准入要求。
2. 分析和开发过程中持续整理确认后的实施方案、实际提交、验证结果与待办。在已授权范围内，按实时可编辑字段回填有来源的信息并回读；保留原有描述，不能用计划冒充完成结果。审批结论、人员和交付版本缺少明确决策时集中交接，不猜填。
3. 接管进入 task_intake 后执行 `jira_status.py prepare --trigger takeover --operation-id <op-id>`；Story 使用项目配置的 Development Started。`ready` 时原生执行返回的转换并回读，再 `complete`；`satisfied` 表示本次读取已达目标，不重复写入。恢复到 design_review/implementation 时补查该节点，不让 Jira 长期停在 Analyzed。
4. Q4 确认并进入 ci_validation 后，重新读取当前状态、字段和 transitions，执行 `prepare --trigger tests_passed --operation-id <op-id>`。依据 `field_plan` 提前采集，依据实际缺项补充并原生回填、回读，再重新 prepare。只有 `ready` 才执行返回的转换；混合验收中的人工决定不能由测试结果代替，仅 TapTest 的 Q4 按状态自动汇总。
5. 每次写入后回读核对字段或目标状态，使用 `interaction-path` 保存当前 issue/run、读取时间、来源和材料，并在 note 引用。已知未发起转换的预检缺项允许补齐后重检；已发起而 failed/unknown 的转换先回读，不能重新 prepare 盲目重放。隐藏 Validator 按服务端实际错误处理，元数据 required=false 不代表没有服务端校验。

方案评审时通过现有采集包确认 Story Test Design Review Result，Tests Passed 前复用有效决定并核对变化（`customfield_10413`）；PR 审查时核对 Customer Requirement Acceptance（`customfield_10414`）及 fixVersions，以实时表单为准，审批选项由实际审批人决定，不根据测试 PASS 猜填。Tests Passed 到 PR 提交的转换在实际到达时读取并依现有人工提审规则交接；合并和发布仍需独立授权。

这些材料证明外部状态，不能替代 Q1-Q4 和代码验证。同步失败只暂停对应写入，继续无依赖工作并在交接时列全 `jira_status_todos`；不手写账本消除提示。

## 实现、PR、CI 和完成

整理 Fix Details 与 PR 时按[交付文案内容](../../../../docs/usage/quality-checkpoints.md#交付文案内容)核对真实 diff、当前证据与远端原文，补全技术说明并保留人工编辑；字段建议或 confirmed 不代替内容及新鲜度核验。

修改 `tapdata-common-lib` 下代码前，先按[公共库升级](../../runbooks/common-lib-upgrade.md)判断兼容性，并落实不兼容变更的 release 分支前置条件；CI 验证同时核对 Nexus 发布及消费依赖结果。

- 创建或编辑 PR 正文时，按[PR 正文发布与回读](../../../../docs/usage/quality-checkpoints.md#pr-正文发布与回读)每仓准备真实多行 UTF-8 文件，调用 `workflow/pr_body.py preflight`；CLI 用 `--body-file`，不把正文拼入 Shell。原生写入后回读 `number,url,body` 并用 `compare` 核对目标与全文，再报告描述已核验。提示不自动替换合法转义；已存在 PR 保留人工修改，未知结果先回读原 PR，不重建。纯正文修正不触发来源分支合并或代码重验，正文问题不阻塞无依赖工作；测试总结、实际 `AgenticOps 版本 XXX` 标识与补测关联按项目 PR Ready 合同核对。

- 接管、阶段推进和质量操作返回 `sync_actions` 时，先核对已有授权，在当前轮次的安全边界主动处理；恢复使用 task next 或 external_sync status 查全部历史待办，不等待研发提醒。接管摘要不冒充 Q1，检查点使用完整有效正文，未知操作复用原记录回读，不重发。已知未发送说明原因并交接；只读待办不代表发送保证。
- completed 后只可按[有限回执恢复](../../../../docs/usage/quality-checkpoints.md#同步待办与有限回执恢复)补录已有操作；不得新建草稿或发送。退出脚本先处理可确定事项；未知外部结果保留原操作信息并交接，不重复发送，也不因同步记录缺项阻断独立清理。待办展示失败与本地操作失败分开，先回读原操作，避免重复推进。

- 提交与 PR 准备遵循[分支修复与测试维护](../../runbooks/build-test-and-local-run.md#测试与提交边界)：业务及适用普通 `test` 与 IT强制分开提交，前者可合入低版本，后者不得回补；核验合入方式保留回补边界；用户回补后回读实际 release 成果并完成独立功能验证，再创建或更新 PR。不代办跨分支合入，不以 develop 报告替换 release 证据。
- 创建或提审前端代码 PR 时，按[PR 默认审查人](../../runbooks/build-test-and-local-run.md#pr-默认审查人)应用 Feynman 默认审查安排，核对账号、请求资格及写后回读；用户明确指定的安排优先，未确认账号不猜测。
- 每个仓库分别验证并记录提交、PR 和 CI，任务级证据统一汇总。按[共同验证材料](../../../../docs/usage/quality-checkpoints.md#共同验证材料)用现有 quality.py verification 动作记录本地测试、来源同步、CI 报告与审查材料；缺失或版本失效时先补齐，不直接改阶段。失败使用 failures.py 原 problem_id 记录 start/finish，替代旧 PR 独立预算入口。
- 功能与缺陷 Q2 方案确认时都要明确 Wiki 依据的 IT级别、判定理由、范围、用例复用/新增/修改和验证安排，按[共同验证材料](../../../../docs/usage/quality-checkpoints.md#共同验证材料)记录 integration_tests，按仓库/场景/单一级别拆行，cases 逐用例区分复用、修改、新增，与现有方案一次确认。不能只列命令或让研发先交用例清单。
- 功能与缺陷在方案分析时使用 [tapdata-ci-test](../tapdata-ci-test/SKILL.md) 判断集成测试复用、新增或修改及框架可用性；实现后调用其编写、执行、报告分析和修复能力，PR CI 返回后再次调用报告分析。具体测试步骤由该技能及其 Runbook 维护；主流程负责授权、质量记录、阶段、提交推送和 CI 等待，不复制测试步骤。
- 集成测试技能、框架、Wiki 或环境不可用时，展示具体模块、场景、依据和未覆盖范围，继续无依赖工作。区分应写未写与已写未运行，按[测试编写缺口与 PR 总结](../../../../docs/usage/quality-checkpoints.md#测试编写缺口与-pr-总结)统一处理：框架不支持（包括当前全部 L3）关联 Jira Manual Test 并完成验证，按场景去重创建或复用兼容 Jira 任务给 Leon，关联手动用例与原任务并回读。复用既有 item/execute/decide 和 local.it_followups，Q4 核验手动闭环；取消每仓 GitHub Issue。临时环境或查询失败先说明恢复方式，不默认转成编写缺口。建票不替代研发决定或测试通过；未知外部结果先回读，不重复创建。
- 功能与缺陷在每轮实现或返工完成、正式验证开始前，按[PR 前同步检出来源](../../../../docs/usage/task-authorization.md#pr-前同步检出来源)核对并同步已登记 target_branch，保留冻结基线并确认具体 Merge 授权。验证期间不追赶正常推进的远端；推送同一候选、修改正文或转 Ready 不单独触发同步。Draft 可承载人工验收和 CI；按[PR 正文发布与回读](../../../../docs/usage/quality-checkpoints.md#pr-正文发布与回读)更新待验收及最终总结，分别对应当前 Head 的本地、人工和 CI 事实。已知不兼容先处理，冲突退回沿现有返工与完成检查闭环。
- 每次原子操作成功后继续下一项已授权工作；用 `task.py next --issue-key <issue-key>` 查看门禁、检查点和待回写评论。已有任务授权覆盖的编码、测试、提交、推送、Draft PR 和 Jira 回写不再逐步询问，仍遵守平台权限、外部事实回读和流程检查点。Q3 使用 `auto_checkpoint`：仅当 Q2 已选的全部修复后检查项满足各自证据合同（TapTest 使用项目配置的 Jira 状态依据）时自动记录和回写；它不是用户验收。`next` 只是只读建议，不授予新权限，也不能代替实际完成阶段工作。
- 暂停时展示 `quality.py status` 或 `task.py next` 中该检查点的 `handoff`：说明为什么停、具体用例/步骤/预期、仓库与完整提交 SHA、谁来验证、需返回的日志/报告及可选处置。需要用户启动本地环境时，先提供候选 SHA、分支/推送状态、构建与启动方式、环境前置条件、测试数据和失败日志要求；用户在其它机器或共享环境验证时，先按授权推送分支或 Draft PR。手工执行必须先给可操作 `steps`；只有日志但没有目标提交时不可猜 SHA 或把分支名导入执行证据。一次列全需要用户决定的项目；恢复后复用已确认事实，不重复问同一问题。
- 首轮本地自动测试可绑定 `git_revision` 返回的工作区指纹；手工证据及最终验收只用完整提交 SHA。提交后重新核对/执行验证并使用实际产物 SHA，不能把提交前报告改写为提交后运行。缺证据可如实记录风险或延期，不能填充 PASS。
- 有意义变更且完成第一轮针对性验证后建议 Draft PR；如验证受阻，按项目标准披露现状。Q3 是自动首轮事实检查点，使用 `execute` 导入真实报告、`jira_status` 导入 TapTest 状态，在所有已选修复后项符合合同后执行 `auto_checkpoint`；Q4 才展示验收检查项（含实际关联 Test）、当前 SHA 的执行证据与风险，使用 `decide/checkpoint` 记录用户最终验收。用户可接受风险，不得把未执行、跳过、未知或失败改成通过。
- 仅对启用 `status_sync` 的任务：Q4 完成并进入 `ci_validation` 后，立即读取 Jira issue、已链接 Test、每个 Test 的 Test Details 和可用 transitions，执行 `jira_status.py prepare --trigger tests_passed --operation-id <op-id>`。Manual/Unit 在 Q4 以同一 `case_ref`、当前用例版本和对应方式记录当前完整 SHA 的 PASS 证据及逐项 `accept`；TapTest 导入 `quality.py` 的 `jira_status` 动作，按[状态依据](../../../../docs/usage/quality-checkpoints.md#taptest-的-jira-状态依据)接纳，不要求本地 PASS 或逐项确认；Q4 总体须满足对应合同。Jira 已是 `Tests Passed` 也先核对这些事实；当前为 `In Progress` 且返回 `ready` 时同步尝试一次并写后回读。缺少 Jira 事实、关联、类型、版本或确认时，工具提示用户补充/调整后可以重新做预检；已发起的 Jira 转换不在同一节点盲目重试。
- 每个 PR 创建或更新时读取实际安装版本并显示 `AgenticOps 版本 XXX`，提审前原样纳入 `required_pr_summaries` 返回的当前总结，并回读 PR 正文及手动路径所需 Jira 关联。每个 PR 都要核对目标集成测试是否实际运行；其他 Checks 绿色、未触发、跳过、零用例或缺报告均主动展示具体缺口并请求研发明确处置，不能直接进入下一步或以建票代替确认。使用现有 verification 的原始结果和 decision 记录，确认不等于 PASS，也不解除用例验收、失败、PR Checks 和服务端条件。
- PR Ready 前必须更新每个任务仓库当前 PR Head 的 `ci.py watch` 记录，再以同一 Jira 快照运行 `pr_ready.py` 复核。Test 工作项本身无需 Done。Checks 全部明确成功或具有绑定当前 Head 及具体缺口的有效提审例外，并满足 Q1-Q4 与检查项时，只表示预检通过。按有效授权使用原生 GitHub 工具推进剩余 PR，结果未知先回读；所有目标 PR 身份、当前 Head、OPEN 及 isDraft=false 回读确认后才可称为 PR Ready。证据与具体例外使用[PR Ready 核对](../../../../docs/usage/quality-checkpoints.md#pr-ready-核对)，不重复索取已覆盖提审的决定。状态同步遗留统一提示 Engineering DRI 人工处理；`Pull Request Submitted` 不由 Agent 自动执行。
- 使用原生工具取得每个 PR 当前 Head 对应的 CI 运行和实际报告，交 tapdata-ci-test 分析。首次失败先用稳定 check_id、归因及证据 observe，复发复用原 problem_id，再按原授权与累计轮次 start 修复。定向调试后提交候选，在完整提交 SHA 上完成最终本地重验并记录结果，再推送、等待新 CI，将新报告交回技能分析；非当前变更、归因不明或轮数用尽按质量合同交研发处置。返回结果写入现有质量材料，测试总结写 Jira 讨论并回读，不改写任务描述代替报告。Q5、Q6 核对审查及交付事实。接管和 Q4 节点只尝试 Project 明确配置的单次 Jira 状态同步；线上 Validator 与附件冲突需报告确认，禁止用本地质量处置绕过。
- PR 审查按[审查返工](../../../../docs/usage/quality-checkpoints.md#pr-审查返工)完整回读普通评论及行内线程，逐项修复或说明依据；改变验收预期交研发决定。相同失败沿用原 problem_id，使用失败工具的 review 来源累计轮次，修改后重验当前代码并回读当前 PR CI；不以线程过时或已关闭替代问题核对。
- PR 合入、发布、Tag、rebase、强推和保护分支写入不被任务授权覆盖。检出来源分支合并到任务工作分支也须明确纳入方案授权，不因需要保持最新而绕过授权。
- 用 `workflow/evidence.py --issue-key <issue-key> --dir <project-station>` 汇总结果；启用质量检查时用 `quality.py` 保存草稿、用户确认及发送意图，再调用原生 Jira 工具并回读核对。外部结果不明确时先核对，不盲目重发；具体恢复步骤见质量文档。
- 每个检查点确认后尽力回写简洁人读评论，保留 draft/confirm/prepare_write/receipt/readback。已知未写入可用 receipt result=deferred 并给 reason；超时或结果不明记 unknown，先回读原操作。评论、水印、版本或状态同步失败均不阻止不依赖它的本地研发，PR 后用 evidence.py 统一输出执行过程被跳过的处理与警告。
- 未迁移能力优先使用 Agent 原生能力；没有安全路径时只暂停当前副作用步骤。

归档写入失败时，按[任务授权指引](../../../../docs/usage/task-authorization.md#清理与退出)展示指定快照、失败原因和实际清理结果，引导研发明确授权修复后重试或放弃。复用原操作和失败摘要，不自动丢弃，不以新 clean 操作绕过；回读结果不明时先核实。
