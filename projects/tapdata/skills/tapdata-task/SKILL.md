---
name: tapdata-task
description: 在单任务工位执行 TapData Jira 研发任务，覆盖完整工程接管、准入、设计确认、多仓实现、PR/CI、归档释放与精确清理。
metadata:
  product: agenticops
---

# TapData 单任务工位

先读工位 AGENTS、当前 Project Profile、准入规则和本 Skill。以 station.json 的 Product Root 为准，memory 只作历史线索。工位 config/source/runtime 分离配置、完整源码和唯一运行现场；正式档案在 Product Root 的 `.archive/<run-id>`，.agenticops 只保存一个当前任务与操作。

写请求固定 issue/run，advance 另带 expected-stage；生命周期与范围操作带 expected-revision 和稳定 operation-id。拒绝后先回读，不自动用新 run/revision 重放旧决定。interaction-path 分配当前证据路径，归档后不继续写开发证据。

## 接管与恢复

一般架构、实现和非集成测试审查需要 Wiki 参考资料时，使用 [tapdata-wiki](../tapdata-wiki/SKILL.md)，按项目 Profile 引用阅读中央共享 Wiki，并以当前工位 source 内任务对应版本源码核验。集成测试需求、设计、编写和报告分析统一先进入 [tapdata-ci-test](../tapdata-ci-test/SKILL.md)，由它按需使用 tapdata-wiki；传递本轮已有且适用的查询结果，避免重复查询。Wiki 由研发显式准备和更新，不增加任务开始刷新步骤；不可用时继续有充分源码依据的工作。

1. 先只读核对 current-task.json 与 operation.json：仅 current=null 且操作不存在或 done 时可接新任务，revision 从当前信封读取。已有任务恢复同一 run；未完成操作按原 operation_id、expected_revision 和请求恢复，不另建任务覆盖。
2. 新任务先真实读取 Jira 类型、状态、经办人与当前用户，按 Project 准入核验；不凭标题、历史或默认仓推断。确定产品版本/主仓分支以及完整 Profile，缺失时询问该事实。
3. 空闲工位执行 task.py takeover --issue-key <issue> --task-class <class> --version <已确认主仓分支> --profile full-application --operation-id <op-id> --expected-revision <revision> --dir <station>。可选 t-layer3-test 必须在冻结前用 --optional-repository tapdata/t-layer3-test 加入。模块明确覆盖用 --explicit-branch owner/repo=branch，不能覆盖主仓到不同产品版本。
4. 失败保留原 run/op/request 恢复；完成后 repository context 核验完整 source、ref/SHA 与基线摘要，不把远程页面或缓存当已确认基线。Git 操作只使用 context 的 source 路径，不启动嵌套 Agent。
5. 修改仓通过 repository add --repo <repo> --work-branch <branch> --base-branch <PR目标> --scope <范围> --verification <方式> --operation-id <新op-id> --expected-revision <revision> --expected-run-id <run> --issue-key <issue> --dir <station> 登记并准备工作分支；只允许冻结基线内仓库。范围变化使旧授权失效。
6. 续办原 run 不改基线。新 run 续办旧分支时，用 takeover --continuation-input <json> 显式给出以仓库 ID 为键的 work_branch/baseline_sha/expected_head，均需真实 Git/PR 核验；旧报告不自动变成新验收。随后 repository add 使用 --expected-head 核对续办分支。更换工程版本或扩大完整集合须归档清理后重接，不能偷改冻结清单。
7. snapshot 固化 Jira 初始事实，jira_watermark 按现役 prepare/complete 尽力回写并回读。进入 task_intake 后，按 Project status_sync 节点执行精确 Jira 同步；未知结果先回读，不阻塞无依赖本地准备。接管成功不是停点，继续到真实方案、权限或事实决策点。

## 归档、释放和重置工位

先向用户展示 `cleanup_scope` 的清理/复位、保全、保留、未知四类实际对象及允许的决定，再请求确认；不要只问“是否放弃变更”。保留源码仓不等于保留当前工作区修改。部分清单或阻塞不授权执行；恢复展示原范围、完成回执与变化，结束展示实际结果及剩余材料。用户要求处理保留项时遵守独立处置边界，不扩大普通清理授权。

用户要求清理时，新操作先调用 `workflow/station-clean.py --dir <station>` 只读预检；两层名单和确认请求格式见[配置化清理入口](../../../../docs/usage/task-authorization.md#配置化清理入口)。它复用现有 clean/release 生命周期；未完成任务先确认是否放弃，拒绝则停止，已有确认仍覆盖当前范围时复用。保留 config/source/archive 和命名分支、PR；源码构建残留由 Agent 使用项目原生工具清理，产品不执行 Maven/npm/pnpm，也不以通用删除兜底。discard 源码仍需额外确认精确快照；完成任务同时绑定 terminal proof 和 candidate_digest，不能把 incomplete 档案后置改为 completed。

使用原生能力停止已登记写入者并回读后，按预检计划确认并执行 station-clean.py；Workflow 在同一操作内归档、通过独立源码复位模块核验成果并检出开发 SHA、回收已登记目录、撤销授权和解绑。源码归位使用 detached checkout，命名分支和提交保留，不 rebase、不猜测当前版本、不联网追新。下次接管重新核验开发基线。无有效基线、未知资源或已登记外部写入结果未知时，处理具体阻塞，不手改状态或换工具绕过。

所有可迁出的日志、依赖安装、插件、报告和应用现场集中写 runtime。源码内 target/node_modules 生产前，使用 station_resources.py 登记 kind=directory、精确 path、producer，由 Workflow 创建并登记根身份后再执行构建；不得等目录非空后再按名称收编。已有空目录采用需 adopt_empty=true。受管目录内部无需逐文件登记。进程仍记录 PID、启动时间、cwd、executable；实际测试数据记录隔离身份及回读。

默认归档保存实际源码修改和必要新文件并做重建核验；超限/敏感材料无法归档时，登记 source-disposition，明确 archive/export/discard 和当前快照，不把摘要当作源码备份。export 使用 workflow/station_export.py 创建工位外私有成果并核验回读，参数见[任务授权指引](../../../../docs/usage/task-authorization.md#重置工位)，discard 必须精确确认。源码链接、冲突索引、submodule 等不支持状态先明确处理，不能擅自丢弃。

运行资源 external 与可选 Git 对象区分：分支 resource_type=git-branch，PR resource_type=pull-request，默认 retain，不因未删除阻止重置。用户要求删除分支/关闭 PR 时，在本地重置完成后独立展示 ID、SHA/状态及保护回读，经确认后先用 workflow/station_disposition.py 向原档案追加 intent，再使用原生工具执行，再追加 readback。unknown 只回读原操作，不重发；不修改 Jira，不改档案正文，不重新占用已释放工位。

中断恢复同一 issue/run/revision/operation-id 和原请求；已有 schema 3 操作仍走原 task.py 入口，不转换为新计划。目录身份或源码处置变化时执行 cleanup-amend，绑定原摘要和 plan revision，补充确认差异；运行目录内部新增生成物在原目录授权范围内，不逐文件重做摘要。已完成目录回执后重新产生内容必须停止并明确补充处置，不能沿旧回执删除。确认请求保存在工位外系统临时目录，避免污染活动材料。当前工位代际及目标产品支持范围以[机器兼容清单](../../../../contracts/station-state-compatibility.json)为准。工位与目标产品不兼容时，先使用匹配的原产品版本结束任务并归档释放或清理，再显式执行 station purge；确认解绑成功后才切换产品并按目标版本重新初始化，顺序见[更新与回退](../../../../docs/usage/update-and-rollback.md)。不得使用目标版本解析或迁移旧任务，也不得通过 repair 跨代际采用。

## 准入、设计和多仓库

新任务在 Q2 授权前执行 `task.py source-readiness --issue-key <issue> --expected-run-id <run> --dir <station>`；它刷新目标引用并核验完整工程与工作分支。若提示目标正常推进，展示冻结基线、工作 Head、最新目标 SHA，并请用户决定保留基线且在 PR 前按既有规则同步，或归档清理后重接。选择保留时执行相同命令并追加 `--confirm-digest <快照摘要> --decision-ref <真实决定来源>`。随后 grant 与 advance 会重新核对这些事实；变化时重新准备。脏源码、身份错误、未知 ignored 产物、分叉及远端核验失败按返回原因处理，不能自动丢弃改动或重写冻结基线。

`feature_change` 表示已有明确范围的功能开发，不接入需求任务。通过 `checklist` 读取验收标准、目标仓库和验证方式；功能任务不要求独立风险等级，具体风险与回滚纳入实施方案统一确认；验证方式可由研发确认，不强制写入 Jira `customfield_10049`。使用项目 `quality-feature.json`，在 Q2 前用 `task.py record --key implementation_plan --input <方案.json>` 记录目标、实现变化、验收场景、风险和回滚，并记录 `scope_boundary`。每个验收场景要对应具体检查项、预期和执行方式；不填写假根因、问题版本或 `fix_plan`。下文版本规划、根因与修复线规则仅适用于 `defect_fix`。

- 用 `task.py checklist` 获取机读准入要求，不得凭聊天猜测。
- `defect_fix` 在 Q1 从 `task.py checklist --json` 或 `task.py next` 读取通用 `repair_strategy`。默认只向用户显示一行当前策略，不增加确认；Q2 按 `planning_guidance` 生成方案并披露应用结果。用户要求调整时，在 Q2 确认前使用 `task.py repair-strategy set/clear` 并绑定当前 run；策略不可用、未应用或偏离只报告 warning，不能阻塞主流程或替代根因、范围、验证和授权检查。策略正文只来自中央 Policy，不复制到本 Skill。
- 缺陷保留 Jira 初始版本；本地经用户确认的正确版本驱动当前 run。影响版本不映射分支，先核验 develop 的同一缺陷；否则独立确认真实实施分支。其它版本的后续合并与验证记录为待办。
- 缺陷在输出根因、修改范围或修复方案前，必须用已确定的 `primary_branch` 调用 TapData 分支对齐：`python3 <agenticops-root>/projects/tapdata/scripts/align_branches.py show --tapdata-root <tapdata-root> --version <primary_branch> --repository <候选任务仓库> --json`。`--tapdata-root` 是包含各模块仓库的产品目录，必须含主仓但不要求其它仓库齐全。读取顶层 `outcome`、`scope`、`blockers`、`checked_at` 和全部 `rows` 的 `repository`、`local`、`target_branch`、`target_sha`、`target_status`、`reason`、`refs`；模块使用返回的分支，不把主仓 release 名字机械套给 connectors；hazelcast 固定 `release-v5.5.0`。`not_covered`、`absence_unverified` 或 `unresolved` 不可作为目标仓库基线；版本与分支冲突不能以接受风险放行。
- 分支对齐只证明“该仓库在本次产品修复线应使用哪个分支”，不能单独证明缺陷属于该仓库。结合 Jira 组件/标签、问题现象、堆栈或文件路径、复现结果和目标分支源码，按以下结论展示仓库与分支后才给出方案：有可回查证据唯一指向一个仓库时，输出“建议分析/修复仓库”表，列出仓库、目标分支、SHA、分支推导理由、refs 新鲜度、核验时间和锁定证据；证据指向多个仓库时，输出“问题候选”表，逐项列出上述字段和候选理由；只有版本关系或无法唯一归属时，输出完整对齐列表并请用户确认优先分析的仓库。
- 在登记目标仓库并完成必要源码分析后、签发任何 `task_execution` 授权前，必须以**一轮方案确认**完整展示并请求明确确认：① 实施方案——缺陷使用根因及证据、修改范围、修复方式、风险和回滚；功能使用目标、实现变化、范围、风险和回滚；② 验收方案——每个检查项的用例或场景、复用/新增、执行方式、预期结果、目标仓库及验证责任人；③ 后续动作及授权范围——确认可执行的编码、测试、提交、推送、PR 和 Jira 回写动作，用户限制优先。验证失败或事实变化时停止相应步骤；Jira 同步失败只记录警告。用户确认前不得记录为已确认方案、签发实施授权或开始实现；用户调整方案时重新完整展示并确认。确认后以同一确认来源固化 Q1/Q2、对应方案事实及 `workflow/authorization.py grant`；不得再为 Q3 的成功事实重复索取“接受首轮验证”。
- 按 `checklist` 返回的 `quality_mode` 处理缺项。缺陷 `recorded_decision` 模式一次列全缺口并继续无依赖的分析，在质量检查点由用户决定处理；其它类型仍按各自准入规则。事实不可信或基线无法确定时停止对应步骤。
- 每个目标仓库登记仓库、工作分支、基线分支、范围和验证方式。
- 本地基线完成后分析代码并形成方案；研发工程师确认方案后用 `workflow/authorization.py grant` 签发任务授权。
- 接管不要求已创建或关联 Test。完成受控基线后，Agent 与用户在 Q2 确认修复方案、验收场景、预期和验证方式；如何定义、编写、创建或复用 Test 由用户与 Agent 处理，AgenticOps 只引导、记录、跟进和核对。缺陷编码完成后再通过 Jira「已链接工作项」创建或关联 Test；功能的 CI 用例随功能代码开发、执行和验收，不要求独立 Jira Test，已有的关联 Test 仍须核对。使用 `quality.py status/apply` 完成 Q1、Q2 的记录与确认，然后进入 implementation。具体输入和恢复方法见 [质量检查与证据](../../../../docs/usage/quality-checkpoints.md)，项目标准来自当前任务选择的质量配置。
- 功能与缺陷在相关人工节点先按[统一决策包](../../../../docs/usage/quality-checkpoints.md#jira-全流程统一决策包)执行 jira_status collect，一次收集本阶段全部缺项并记录真实确认；未来结果不提前索要。缺陷处理过程中按 Project 转换配置提前采集 Tests Passed 所需属性：Q2 固化分类和根因依据，仓库确认时形成 Module 依据，Q2/Q4 评论分别形成 Issue Analysis/Fix Details 依据，验收方案确认 Tester、自动化属性和 Xray 关联，版本规划只作为选择 Fix Version 的依据。需要责任人选择的枚举、人员、Module、Fix Version ID 和测试例外不得自动猜测；无法可靠补齐时留到状态同步节点跳过并在 PR Ready 提示。
- 缺陷 Q2 前用 `task.py record --key fix_plan` 记录根因、范围、修复方式、风险与回滚。修复后用例尚未编码时把 `target_revision` 写为 `pending`；先确认稳定用例/方式，执行前用 `item` 绑定精确代码。只补充代码版本不会要求重新选择同一用例；改步骤、预期、范围或方式仍须重新确认。
- 一个检查项对应一个用例和一种方式；同检查点可有不同方式的多项。修复前不可执行须说明原因，修复后项未到检查点不算失败。`Manual` 由用户执行；`TapTest` 使用目标工程实际提供的 `write-xray-test`、`write-test-script`；`Unit` 核对产品工程的单元测试和 CI 集成测试。TapCE 当前不纳管，不算通过；若因此无法形成受管验收或 Jira Validator 阻塞，请用户调整 Jira 或验收方案并重新读取事实。AgenticOps 工具不承载用例开发或环境执行器；Agent 使用原生工具按授权编写和执行 CI 用例，Jira Test 与 TapTest 使用现有闭环。
- 新增仓库或修改分支、范围、验证方式后必须重新确认和授权。
- Workflow 检查点失败时展示原因、缺失事实和停止点；先补齐所需事实，不手改状态绕过。原生工具审批由平台处理。

## 功能任务的 Jira 协作

功能开发仅接入 Story。新接管仍要求 Analyzed 且负责人正确；正在进行的任务只恢复已有 run，没有 run 时先明确交接方案，不自动退回 Jira 或放宽准入。

Agent 按以下顺序主动处理，不等待研发提醒状态流转：

1. 接管时读取 issue type、状态、负责人、描述、关联工作项及可用转换和表单元数据。先利用已有信息，不为项目未定义的字段增加准入要求。
2. 分析和开发过程中持续整理确认后的实施方案、实际提交、验证结果与待办。在已授权范围内，按实时可编辑字段回填有来源的信息并回读；保留原有描述，不能用计划冒充完成结果。审批结论、人员和交付版本缺少明确决策时集中交接，不猜填。
3. 接管进入 task_intake 后执行 `jira_status.py prepare --trigger takeover --operation-id <op-id>`；Story 使用项目配置的 Development Started。`ready` 时原生执行返回的转换并回读，再 `complete`；`satisfied` 表示本次读取已达目标，不重复写入。恢复到 design_review/implementation 时补查该节点，不让 Jira 长期停在 Analyzed。
4. Q4 确认并进入 ci_validation 后，重新读取当前状态、字段和 transitions，执行 `prepare --trigger tests_passed --operation-id <op-id>`。依据 `field_plan` 提前采集，依据实际缺项补充并原生回填、回读，再重新 prepare。只有 `ready` 才执行返回的转换；混合验收中的人工决定不能由测试结果代替，仅 TapTest 的 Q4 按状态自动汇总。
5. 每次写入后回读核对字段或目标状态，使用 `interaction-path` 保存当前 issue/run、读取时间、来源和材料，并在 note 引用。已知未发起转换的预检缺项允许补齐后重检；已发起而 failed/unknown 的转换先回读，不能重新 prepare 盲目重放。隐藏 Validator 按服务端实际错误处理，元数据 required=false 不代表没有服务端校验。

Tests Passed 前核对 Story Test Design Review Result（`customfield_10413`）；PR 审查时核对 Customer Requirement Acceptance（`customfield_10414`）及 fixVersions，以实时表单为准，审批选项由实际审批人决定，不根据测试 PASS 猜填。Tests Passed 到 PR 提交的转换在实际到达时读取并依现有人工提审规则交接；合并和发布仍需独立授权。

这些材料证明外部状态，不能替代 Q1-Q4 和代码验证。同步失败只暂停对应写入，继续无依赖工作并在交接时列全 `jira_status_todos`；不手写账本消除提示。

## 实现、PR、CI 和完成

- 创建或编辑 PR 正文时，按[PR 正文发布与回读](../../../../docs/usage/quality-checkpoints.md#pr-正文发布与回读)每仓准备真实多行 UTF-8 文件，调用 `workflow/pr_body.py preflight`；CLI 用 `--body-file`，不把正文拼入 Shell。原生写入后回读 `number,url,body` 并用 `compare` 核对目标与全文，再报告描述已核验。提示不自动替换合法转义；已存在 PR 保留人工修改，未知结果先回读原 PR，不重建。纯正文修正不触发来源分支合并或代码重验，正文问题不阻塞无依赖工作，不改变 PR Ready 门禁。

- 接管、阶段推进和质量操作返回 `sync_actions` 时，先核对已有授权，在当前轮次的安全边界主动处理；恢复使用 task next 或 external_sync status 查全部历史待办，不等待研发提醒。接管摘要不冒充 Q1，检查点使用完整有效正文，未知操作复用原记录回读，不重发。已知未发送说明原因并交接；只读待办不代表发送保证。
- completed 后只可按[有限回执恢复](../../../../docs/usage/quality-checkpoints.md#同步待办与有限回执恢复)补录已有操作；不得新建草稿或发送。退出前先核清未知结果，再生成确认范围；清理确认或归档草稿已绑定时交维护侧，不改账本或摘要绕过。待办展示失败与本地操作失败分开，先回读原操作，避免重复推进。

- 每个仓库分别验证并记录提交、PR 和 CI，任务级证据统一汇总。按[共同验证材料](../../../../docs/usage/quality-checkpoints.md#共同验证材料)用现有 quality.py verification 动作记录本地测试、来源同步、CI 报告与审查材料；缺失或版本失效时先补齐，不直接改阶段。失败使用 failures.py 原 problem_id 记录 start/finish，替代旧 PR 独立预算入口。
- 功能与缺陷在方案分析时使用 [tapdata-ci-test](../tapdata-ci-test/SKILL.md) 判断集成测试复用、新增或修改及框架可用性；实现后调用其编写、执行、报告分析和修复能力，PR CI 返回后再次调用报告分析。具体测试步骤由该技能及其 Runbook 维护；主流程负责授权、质量记录、阶段、提交推送和 CI 等待，不复制测试步骤。
- 集成测试技能、框架、Wiki 或环境不可用时，展示具体模块、场景、依据和未覆盖范围，继续无依赖工作。确认存在当前授权内无法补齐且值得跟进的框架接入或覆盖能力缺口时，主动询问是否新增关联任务；临时查询失败、未提供本地环境等先说明恢复方式，不默认转成业务任务。创建前查询已有跟进事项；研发同意后按明确范围创建、关联并回读，结果未知先核对原操作，不重复创建。复用决定前核对当前事实、目标项目、任务类型、负责人和关联对象；这些内容或范围、风险实质变化时补充确认，普通文字整理不重复询问。是否创建任务与是否接受本次验证缺口分别确认；不能以拒绝创建、等待答复或已建任务代替质量处置。辅助能力缺失不自动新增门禁，但已有应测范围、选定验收项、PR Checks 和 failures 记录仍按原合同处理，不漏报失败或缩减范围。
- 功能 PR 创建或更新前，先核对每个任务仓库的检出来源 `base_branch`，读取其最新远端 SHA；在方案授权内将该分支合并到任务工作分支，保留原冻结基线记录。冲突不明时暂停。合并产生代码变化后重验受影响内容并更新提交、检查项与 CI 证据；未变化也须核对报告仍适用，不能把旧报告改写为新 Head。此操作不授权 PR 合入或写保护分支。
- 每次原子操作成功后继续下一项已授权工作；用 `task.py next --issue-key <issue-key>` 查看门禁、检查点和待回写评论。已有任务授权覆盖的编码、测试、提交、推送、Draft PR 和 Jira 回写不再逐步询问，仍遵守平台权限、外部事实回读和流程检查点。Q3 使用 `auto_checkpoint`：仅当 Q2 已选的全部修复后检查项满足各自证据合同（TapTest 使用项目配置的 Jira 状态依据）时自动记录和回写；它不是用户验收。`next` 只是只读建议，不授予新权限，也不能代替实际完成阶段工作。
- 暂停时展示 `quality.py status` 或 `task.py next` 中该检查点的 `handoff`：说明为什么停、具体用例/步骤/预期、仓库与完整提交 SHA、谁来验证、需返回的日志/报告及可选处置。需要用户启动本地环境时，先提供候选 SHA、分支/推送状态、构建与启动方式、环境前置条件、测试数据和失败日志要求；用户在其它机器或共享环境验证时，先按授权推送分支或 Draft PR。手工执行必须先给可操作 `steps`；只有日志但没有目标提交时不可猜 SHA 或把分支名导入执行证据。一次列全需要用户决定的项目；恢复后复用已确认事实，不重复问同一问题。
- 首轮本地自动测试可绑定 `git_revision` 返回的工作区指纹；手工证据及最终验收只用完整提交 SHA。提交后重新核对/执行验证并使用实际产物 SHA，不能把提交前报告改写为提交后运行。缺证据可如实记录风险或延期，不能填充 PASS。
- 有意义变更且完成第一轮针对性验证后建议 Draft PR；如验证受阻，按项目标准披露现状。Q3 是自动首轮事实检查点，使用 `execute` 导入真实报告、`jira_status` 导入 TapTest 状态，在所有已选修复后项符合合同后执行 `auto_checkpoint`；Q4 才展示验收检查项（含实际关联 Test）、当前 SHA 的执行证据与风险，使用 `decide/checkpoint` 记录用户最终验收。用户可接受风险，不得把未执行、跳过、未知或失败改成通过。
- 仅对启用 `status_sync` 的任务：Q4 完成并进入 `ci_validation` 后，立即读取 Jira issue、已链接 Test、每个 Test 的 Test Details 和可用 transitions，执行 `jira_status.py prepare --trigger tests_passed --operation-id <op-id>`。Manual/Unit 在 Q4 以同一 `case_ref`、当前用例版本和对应方式记录当前完整 SHA 的 PASS 证据及逐项 `accept`；TapTest 导入 `quality.py` 的 `jira_status` 动作，按[状态依据](../../../../docs/usage/quality-checkpoints.md#taptest-的-jira-状态依据)接纳，不要求本地 PASS 或逐项确认；Q4 总体须满足对应合同。Jira 已是 `Tests Passed` 也先核对这些事实；当前为 `In Progress` 且返回 `ready` 时同步尝试一次并写后回读。缺少 Jira 事实、关联、类型、版本或确认时，工具提示用户补充/调整后可以重新做预检；已发起的 Jira 转换不在同一节点盲目重试。
- PR Ready 前必须更新每个任务仓库当前 PR Head 的 `ci.py watch` 记录，再以同一 Jira 快照运行 `pr_ready.py` 复核。Test 工作项本身无需 Done。所有 PR Checks 明确成功且绑定当前 Head、Q1-Q4 及检查项满足要求后才可称为 PR Ready。状态同步遗留统一提示 Engineering DRI 人工处理；`Pull Request Submitted` 不由 Agent 自动执行。
- 使用原生工具取得每个 PR 当前 Head 对应的 CI 运行和实际报告，交 tapdata-ci-test 分析。首次失败先用稳定 check_id、归因及证据 observe，复发复用原 problem_id，再按原授权与累计轮次 start 修复。定向调试后提交候选，在完整提交 SHA 上完成最终本地重验并记录结果，再推送、等待新 CI，将新报告交回技能分析；非当前变更、归因不明或轮数用尽按质量合同交研发处置。返回结果写入现有质量材料，测试总结写 Jira 讨论并回读，不改写任务描述代替报告。Q5、Q6 核对审查及交付事实。接管和 Q4 节点只尝试 Project 明确配置的单次 Jira 状态同步；线上 Validator 与附件冲突需报告确认，禁止用本地质量处置绕过。
- PR 审查按[审查返工](../../../../docs/usage/quality-checkpoints.md#pr-审查返工)完整回读普通评论及行内线程，逐项修复或说明依据；改变验收预期交研发决定。相同失败沿用原 problem_id，使用失败工具的 review 来源累计轮次，修改后重验当前代码并回读当前 PR CI；不以线程过时或已关闭替代问题核对。
- PR 合入、发布、Tag、rebase、强推和保护分支写入不被任务授权覆盖。检出来源分支合并到任务工作分支也须明确纳入方案授权，不因需要保持最新而绕过授权。
- 用 `workflow/evidence.py --issue-key <issue-key> --dir <project-station>` 汇总结果；启用质量检查时用 `quality.py` 保存草稿、用户确认及发送意图，再调用原生 Jira 工具并回读核对。外部结果不明确时先核对，不盲目重发；具体恢复步骤见质量文档。
- 每个检查点确认后尽力回写简洁人读评论，保留 draft/confirm/prepare_write/receipt/readback。已知未写入可用 receipt result=deferred 并给 reason；超时或结果不明记 unknown，先回读原操作。评论、水印、版本或状态同步失败均不阻止不依赖它的本地研发，PR 后用 evidence.py 统一输出执行过程被跳过的处理与警告。
- 未迁移能力优先使用 Agent 原生能力；没有安全路径时只暂停当前副作用步骤。
