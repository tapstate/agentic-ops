---
name: tapdata-task
description: 在单任务工位执行 TapData Jira 研发任务，覆盖完整工程接管、准入、设计确认、多仓实现、PR/CI、归档释放与精确清理。
metadata:
  product: agenticops
---

# TapData 单任务工位

先读工作空间 AGENTS、当前 Project Profile、准入规则和本 Skill。以 workspace.json 的 Product Root 为准，memory 只作历史线索。config/source/runtime/archive 分离配置、完整源码、唯一运行现场和正式档案；.agenticops 只保存一个当前任务与操作。

写请求固定 issue/run，advance 另带 expected-stage；生命周期与范围操作带 expected-revision 和稳定 operation-id。拒绝后先回读，不自动用新 run/revision 重放旧决定。interaction-path 分配当前证据路径，归档后不继续写开发证据。

## 接管与恢复

1. 先只读核对 current-task.json 与 operation.json：仅 current=null 且操作不存在或 done 时可接新任务，revision 从当前信封读取。已有任务恢复同一 run；未完成操作按原 operation_id、expected_revision 和请求恢复，不另建任务覆盖。
2. 新任务先真实读取 Jira 类型、状态、经办人与当前用户，按 Project 准入核验；不凭标题、历史或默认仓推断。确定产品版本/主仓分支以及完整 Profile，缺失时询问该事实。
3. 空闲工位执行 task.py takeover --issue-key <issue> --task-class <class> --version <已确认主仓分支> --profile full-application --operation-id <op-id> --expected-revision <revision> --dir <workspace>。可选 t-layer3-test 必须在冻结前用 --optional-repository tapdata/t-layer3-test 加入。模块明确覆盖用 --explicit-branch owner/repo=branch，不能覆盖主仓到不同产品版本。
4. 失败保留原 run/op/request 恢复；完成后 repository context 核验完整 source、ref/SHA 与基线摘要，不把远程页面或缓存当已确认基线。Git 操作只使用 context 的 source 路径，不启动嵌套 Agent。
5. 修改仓通过 repository add --repo <repo> --work-branch <branch> --base-branch <PR目标> --scope <范围> --verification <方式> --operation-id <新op-id> --expected-revision <revision> --expected-run-id <run> --issue-key <issue> --dir <workspace> 登记并准备工作分支；只允许冻结基线内仓库。范围变化使旧授权失效。
6. 续办原 run 不改基线。新 run 续办旧分支时，用 takeover --continuation-input <json> 显式给出以仓库 ID 为键的 work_branch/baseline_sha/expected_head，均需真实 Git/PR 核验；旧报告不自动变成新验收。随后 repository add 使用 --expected-head 核对续办分支。更换工程版本或扩大完整集合须归档清理后重接，不能偷改冻结清单。
7. snapshot 固化 Jira 初始事实，jira_watermark 按现役 prepare/complete 尽力回写并回读。进入 task_intake 后，按 Project status_sync 节点执行精确 Jira 同步；未知结果先回读，不阻塞无依赖本地准备。接管成功不是停点，继续到真实方案、权限或事实决策点。

## 归档、释放和清理

运行前后用 `python3 <agenticops-root>/workflow/station_resources.py --dir <workspace> --issue-key <issue> --expected-run-id <run> --input <资源数组.json>` 登记实际资源。数组每项包含 kind 与 producer；file 记录工位相对 path，process 记录 pid/started_at/cwd/executable，external 记录精确 id/status/readback_ref。原生工具负责停止或外部清理，Workflow 核对登记身份，不扫描或误停其它进程。

外部资源已停止写入且有回读依据时可标记 quiesced，允许先 archive；release/clean 最终解绑前必须有 cleaned 的回读。归档后通常仅允许同一已登记 external 的 status=cleaned 与 readback_ref 更新，补充清理事实而不重写档案。归档退出、完成待释放或当前未完成归档/清理若发现迟到产物，可用资源登记命令加 --expected-operation-id 精确登记本工位产物来源，再重新确认清单；登记本身不授予删除权限，不恢复开发。未知归属或清理结果不明时保留占用。

archive 可将未完成任务正式归档为 incomplete，但仍占用且停止开发。release 要求交付及项目验收核对与研发明确释放。clean 用于不再继续的未完成任务：先有效档案，再精确授权清理，成功后才解绑；不关闭 PR 或修改 Jira。

已有 incomplete 档案只能 clean，不能在归档后补成 completed 再 release。清理现场发生变化时，先重新运行 cleanup-plan，展示新增或变化对象并取得新 confirmed_digest；使用 task.py cleanup-amend，固定当前 issue/run/revision/operation-id、--expected-plan-digest 和 --expected-plan-revision，--input 提供新 confirmed_digest。原操作保存追加确认版本，随后用原生命周期请求恢复；不覆盖正式档案或旧回执。重新生成相同内容的产物也需要新确认版本，不能借旧删除回执放行。未完成 archive 在正式档案尚未发布时也可用该命令重新确认草稿清单，但不执行删除；该命令只恢复原操作，不新增任务生命周期。

先按 Project 资源清单停止并核对写入者；cleanup-plan --issue-key <issue> --expected-run-id <run> --dir <workspace> 返回处置计划及摘要。向研发展示具体对象、文件/内容指纹、保留 refs/config、候选摘要和缺失证据；确认不能只说“清理一下”。请求 JSON 包含真实 summary/reason；release/clean 另包含确认计划的 confirmed_digest，release 包含已确认 candidate_digest。然后调用 task.py <archive|release|clean> --issue-key <issue> --expected-run-id <run> --expected-revision <revision> --operation-id <op-id> --input <json> --dir <workspace>。失败恢复同一请求与操作，不覆盖 operation。

档案只保存脱敏总结、已验证事实、成果、未完成事项、证据缺口和退出原因；不保存完整敏感会话或秘密，不把 incomplete 改成 completed。再次接管前核验 current=null、op done、runtime 无旧材料和活动授权已撤销。工位级 purge 只在任务退出后用于解除绑定，不能代替 archive/clean。

当前 engineering-profiles.json 只定义仓库集合与 Profile 修订；完整应用工具链、actions 和健康检查配方尚须按实际目标分支与环境验证，不能把完整源码准备成功称为 TapData 启动或任务验收通过。缺数据库、凭据或运行入口时报告具体缺口，不填造默认值。

## 准入、设计和多仓库

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
- 缺陷处理过程中按 Project `status_sync.field_mappings` 提前采集 Tests Passed 所需属性：Q2 固化分类和根因依据，仓库确认时形成 Module 依据，Q2/Q4 评论分别形成 Issue Analysis/Fix Details 依据，验收方案确认 Tester、自动化属性和 Xray 关联，版本规划只作为选择 Fix Version 的依据。需要责任人选择的枚举、人员、Module、Fix Version ID 和测试例外不得自动猜测；无法可靠补齐时留到状态同步节点跳过并在 PR Ready 提示。
- 缺陷 Q2 前用 `task.py record --key fix_plan` 记录根因、范围、修复方式、风险与回滚。修复后用例尚未编码时把 `target_revision` 写为 `pending`；先确认稳定用例/方式，执行前用 `item` 绑定精确代码。只补充代码版本不会要求重新选择同一用例；改步骤、预期、范围或方式仍须重新确认。
- 一个检查项对应一个用例和一种方式；同检查点可有不同方式的多项。修复前不可执行须说明原因，修复后项未到检查点不算失败。`Manual` 由用户执行；`TapTest` 使用目标工程实际提供的 `write-xray-test`、`write-test-script`；`Unit` 核对产品工程的单元测试和 CI 集成测试。TapCE 当前不纳管，不算通过；若因此无法形成受管验收或 Jira Validator 阻塞，请用户调整 Jira 或验收方案并重新读取事实。AgenticOps 工具不承载用例开发或环境执行器；Agent 使用原生工具按授权编写和执行 CI 用例，Jira Test 与 TapTest 使用现有闭环。
- 新增仓库或修改分支、范围、验证方式后必须重新确认和授权。
- Workflow 检查点失败时展示原因、缺失事实和停止点；先补齐所需事实，不手改状态绕过。原生工具审批由平台处理。

## 功能任务的 Jira 协作

功能开发仅接入 Story。新接管仍要求 Analyzed 且负责人正确；正在进行的任务只恢复已有 run，没有 run 时先明确交接方案，不自动退回 Jira 或放宽准入。

Agent 按以下顺序主动处理，不等待研发提醒状态流转：

1. 接管时读取 issue type、状态、负责人、描述、关联工作项及可用转换和表单元数据。先利用已有信息，不为项目未定义的字段增加准入要求。
2. 分析和开发过程中持续整理确认后的实施方案、实际提交、验证结果与待办。在已授权范围内，按实时可编辑字段回填有来源的信息并回读；保留原有描述，不能用计划冒充完成结果。审批结论、人员和交付版本缺少明确决策时集中交接，不猜填。
3. 接管进入 task_intake 后执行 `jira_status.py prepare --trigger takeover`；Story 使用项目配置的 Development Started。`ready` 时原生执行返回的转换并回读，再 `complete`；`satisfied` 表示本次读取已达目标，不重复写入。恢复到 design_review/implementation 时补查该节点，不让 Jira 长期停在 Analyzed。
4. Q4 确认并进入 ci_validation 后，重新读取当前状态、字段和 transitions，执行 `prepare --trigger tests_passed`。依据 `field_plan` 提前采集，依据实际缺项补充并原生回填、回读，再重新 prepare。只有 `ready` 才执行返回的转换；测试结果不代替人工 Q4 验收。
5. 每次写入后回读核对字段或目标状态，使用 `interaction-path` 保存当前 issue/run、读取时间、来源和材料，并在 note 引用。已知未发起转换的预检缺项允许补齐后重检；已发起而 failed/unknown 的转换先回读，不能重新 prepare 盲目重放。隐藏 Validator 按服务端实际错误处理，元数据 required=false 不代表没有服务端校验。

Tests Passed 前核对 Story Test Design Review Result（`customfield_10413`）；PR 审查时核对 Customer Requirement Acceptance（`customfield_10414`）及 fixVersions，以实时表单为准，审批选项由实际审批人决定，不根据测试 PASS 猜填。Tests Passed 到 PR 提交的转换在实际到达时读取并依现有人工提审规则交接；合并和发布仍需独立授权。

这些材料证明外部状态，不能替代 Q1-Q4 和代码验证。同步失败只暂停对应写入，继续无依赖工作并在交接时列全 `jira_status_todos`；不手写账本消除提示。

## 实现、PR、CI 和完成

- 每个仓库分别验证并记录提交、PR 和 CI，任务级证据统一汇总。按[共同验证材料](../../../../docs/usage/quality-checkpoints.md#共同验证材料)用现有 quality.py verification 动作记录本地测试、来源同步、CI 报告与审查材料；缺失或版本失效时先补齐，不直接改阶段。失败使用 failures.py 原 problem_id 记录 start/finish，替代旧 PR 独立预算入口。
- CI 用例按[开发与质量核对](../../runbooks/build-test-and-local-run.md#ci-用例开发与质量核对)在当前业务任务中完成：确认预期驱动断言，优先证明旧代码目标断言失败、新代码通过，再执行模块全量验证；无法进行旧版对照时记录限制并验证受控反例。
- 功能与缺陷均按[用例缺口判断](../../runbooks/build-test-and-local-run.md#用例缺口判断)由 Agent 结合最新差异、已确认验收、实际断言和框架判断 CI 用例复用、新增或修改，在当前任务内处理；不要求研发先列用例，不创建强制独立 CI 用例任务。覆盖报告缺失如实说明，框架不支持时由研发决定是否创建关联任务或其它处置，保留原因及未覆盖范围。断言不以实现结果反推；修改验收含义或超出授权时先交研发决策。
- 功能 PR 创建或更新前，先核对每个任务仓库的检出来源 `base_branch`，读取其最新远端 SHA；在方案授权内将该分支合并到任务工作分支，保留原冻结基线记录。冲突不明时暂停。合并产生代码变化后重验受影响内容并更新提交、检查项与 CI 证据；未变化也须核对报告仍适用，不能把旧报告改写为新 Head。此操作不授权 PR 合入或写保护分支。
- 每次原子操作成功后继续下一项已授权工作；用 `task.py next --issue-key <issue-key>` 查看门禁、检查点和待回写评论。已有任务授权覆盖的编码、测试、提交、推送、Draft PR 和 Jira 回写不再逐步询问，仍遵守平台权限、外部事实回读和流程检查点。Q3 使用 `auto_checkpoint`：仅当 Q2 已选的全部修复后检查项都在最终完整 SHA 得到预期结果时自动记录和回写；它不是用户验收。`next` 只是只读建议，不授予新权限，也不能代替实际完成阶段工作。
- 暂停时展示 `quality.py status` 或 `task.py next` 中该检查点的 `handoff`：说明为什么停、具体用例/步骤/预期、仓库与完整提交 SHA、谁来验证、需返回的日志/报告及可选处置。需要用户启动本地环境时，先提供候选 SHA、分支/推送状态、构建与启动方式、环境前置条件、测试数据和失败日志要求；用户在其它机器或共享环境验证时，先按授权推送分支或 Draft PR。手工执行必须先给可操作 `steps`；只有日志但没有目标提交时不可猜 SHA 或把分支名导入执行证据。一次列全需要用户决定的项目；恢复后复用已确认事实，不重复问同一问题。
- 首轮本地自动测试可绑定 `git_revision` 返回的工作区指纹；手工证据及最终验收只用完整提交 SHA。提交后重新核对/执行验证并使用实际产物 SHA，不能把提交前报告改写为提交后运行。缺证据可如实记录风险或延期，不能填充 PASS。
- 有意义变更且完成第一轮针对性验证后建议 Draft PR；如验证受阻，按项目标准披露现状。Q3 是自动首轮事实检查点，使用 `execute` 导入可回查报告并在所有已选修复后项符合预期时执行 `auto_checkpoint`；Q4 才展示验收检查项（含实际关联 Test）、当前 SHA 的执行证据与风险，使用 `decide/checkpoint` 记录用户最终验收。用户可接受风险，不得把未执行、跳过、未知或失败改成通过。
- 仅对启用 `status_sync` 的任务：Q4 完成并进入 `ci_validation` 后，立即读取 Jira issue、已链接 Test、每个 Test 的 Test Details 和可用 transitions，执行 `jira_status.py prepare --trigger tests_passed`。每个受管 Test 都必须在 Q4 以同一 `case_ref`、当前 Jira 用例版本和对应方式记录当前完整 SHA 的 PASS 证据，并由用户逐项 `accept` 确认；Q4 总体也必须是 `accept`。Jira 已是 `Tests Passed` 也先核对这些事实；当前为 `In Progress` 且返回 `ready` 时同步尝试一次并写后回读。缺少 Jira 事实、关联、类型、版本或确认时，工具提示用户补充/调整后可以重新做预检；已发起的 Jira 转换不在同一节点盲目重试。
- PR Ready 前必须更新每个任务仓库当前 PR Head 的 `ci.py watch` 记录，再以同一 Jira 快照运行 `pr_ready.py` 复核。Test 工作项本身无需 Done。所有 PR Checks 明确成功且绑定当前 Head、Q1-Q4 及检查项满足要求后才可称为 PR Ready。状态同步遗留统一提示 Engineering DRI 人工处理；`Pull Request Submitted` 不由 Agent 自动执行。
- 按[PR CI 报告回读](../../runbooks/build-test-and-local-run.md#pr-ci-报告回读)使用原生工具读取运行、attempt、实际报告与源码/Jar 版本；CI 返回成功不能证明目标用例运行。对照应测范围记录实测与缺口，失败先归因；非当前变更或不明原因由研发决策。测试总结写 Jira 讨论并回读，不改写任务描述代替报告。Q5、Q6 核对审查及交付事实。接管和 Q4 节点只尝试 Project 明确配置的单次 Jira 状态同步；线上 Validator 与附件冲突需报告确认，禁止用本地质量处置绕过。
- PR 审查按[审查返工](../../../../docs/usage/quality-checkpoints.md#pr-审查返工)完整回读普通评论及行内线程，逐项修复或说明依据；改变验收预期交研发决定。相同失败沿用原 problem_id，使用失败工具的 review 来源累计轮次，修改后重验当前代码并回读当前 PR CI；不以线程过时或已关闭替代问题核对。
- PR 合入、发布、Tag、rebase、强推和保护分支写入不被任务授权覆盖。检出来源分支合并到任务工作分支也须明确纳入方案授权，不因需要保持最新而绕过授权。
- 用 `workflow/evidence.py --issue-key <issue-key> --dir <project-workspace>` 汇总结果；启用质量检查时用 `quality.py` 保存草稿、用户确认及发送意图，再调用原生 Jira 工具并回读核对。外部结果不明确时先核对，不盲目重发；具体恢复步骤见质量文档。
- 每个检查点确认后尽力回写简洁人读评论，保留 draft/confirm/prepare_write/receipt/readback。已知未写入可用 receipt result=deferred 并给 reason；超时或结果不明记 unknown，先回读原操作。评论、水印、版本或状态同步失败均不阻止不依赖它的本地研发，PR 后用 evidence.py 统一输出执行过程被跳过的处理与警告。
- 未迁移能力优先使用 Agent 原生能力；没有安全路径时只暂停当前副作用步骤。
