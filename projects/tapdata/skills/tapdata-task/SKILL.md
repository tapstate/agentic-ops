---
name: tapdata-task
description: 以受控流程执行 TapData Jira 研发任务，覆盖接管、准入、设计确认、多仓库实现、PR、CI 和证据回写。
metadata:
  product: agenticops
---

# TapData 受控任务流程

设项目工作空间为 `<project-workspace>`，任务号为 `<issue-key>`，中央产品根为工作空间 `AGENTS.md` 声明的 `<agenticops-root>`。工具目录为 `<agenticops-root>/workflow`。多个任务 active 时，所有任务命令必须带 `--issue-key <issue-key> --dir <project-workspace>`；不要在各仓库内创建独立状态。本 Skill、当前 Project Profile 和 Product Root 高于历史 memory；memory 只能提供历史线索，不得作为现役命令来源。

## 开始或恢复

1. 运行 `python3 <agenticops-root>/workflow/task.py list --dir <project-workspace>`。
2. 已注册任务用 `status --issue-key <issue-key>` 从当前阶段恢复，不重复已完成步骤。
3. 新任务先读取 Jira 事实。仅当状态精确为 `Analyzed` 且经办人为当前 Jira 用户时，才执行 `task.py init --issue-key TAP-xxx --task-class <defect_fix|feature_change|technical_task> --dir <project-workspace>`；任一条件不符或事实无法核验时拒绝接管，不得初始化本地任务状态。初始化不会停用其它 active 任务。
4. 已存在任务必须让研发工程师选择继续现有 run，或清理后用当前 `--expected-run-id` reset；选择完成后继续流程，不把接管、activate 或 reset 的原子成功当作停点。
5. 进入 `task_intake` 前，先用 Jira 原生工具读取当前 issue 并保存带 `source_ref` 的快照，执行 `jira_watermark.py prepare --issue-key <issue-key> --input <snapshot.json> --dir <project-workspace>`。若返回 `ready`，只按其 `native_request` 用明确的 Jira 编辑工具覆盖一个 `customfield_<ID>`；随后重新读取当前 issue，并执行 `complete --outcome unknown --input <readback.json>`。只有返回 `verified` 才可 advance；Jira 结果不明确时停止写入，可用新的只读快照再次 complete，禁止重发字段写入。
6. 进入 `task_intake` 后，立即从 Jira 原生工具读取当前 issue、当前用户和可用 transitions，按质量文档执行 `jira_status.py prepare --trigger takeover`；返回 `ready` 时只用其精确 transition ID 尝试一次 `In Progress` 并回读后 `complete`，其它结果记录并继续。状态同步失败不是本地门禁。
7. 持续完成准入、仓库登记、本地基线准备和源码分析，直到方案确认、风险授权、事实不可信或其它真实人工决策点。

## 准入、设计和多仓库

- 用 `task.py checklist` 获取机读准入要求，不得凭聊天猜测。
- 缺陷的“问题版本”只取 Jira `fields.versions`（影响版本，多选），不读描述中的同名章节，也不取 `fixVersions`。保留全部版本和来源；先检查 `develop` 的对应源码/复现，存在同一缺陷就优先修复 `develop`，其余影响版本列为研发人工合并修复。确认 `develop` 不受影响后，只选择一个影响版本编码；多候选时由用户选择，不能按 Jira 数组顺序猜测。
- 仓库基线准备前，按质量文档的输入格式执行 `task.py issue-versions --input <jira-and-develop-evidence.json> --expected-run-id <run>`。工具从主仓核验所有影响版本分支及 develop SHA，并在 `branch_references` 逐项列出版本、分支、远端 SHA 与来源，供用户引用并回写 Jira 分支确认；不得因某仓尚无问题归属证据而省略这些版本关系。该列表不自动登记仓库或成为任务基线，仍须 `repository add`、`repository prepare` 固化 `base_sha`。分支不存在直接拒绝，网络/权限错误是“未核验”，不能视为 develop 不受影响。分析证据与当前 SHA 不同需重新分析；不要用 `record problem_version` 或手改本地状态代替。
- 若首次判断缺陷必须先准备 develop 工作树，可以先做受控只读分析，再导入初次版本规划；工具核对已准备基线与修复线及主仓 SHA 一致。只有切换修复线或修改已固化规划才需 cleanup/reset，不为完成一次必要调查强制重开任务。
- 缺陷在输出根因、修改范围或修复方案前，必须用已确定的 `primary_branch` 调用 TapData 分支对齐：`python3 <agenticops-root>/projects/tapdata/scripts/align_branches.py show --tapdata-root <tapdata-root> --version <primary_branch> --repository <候选任务仓库> --json`。`--tapdata-root` 是包含各模块仓库的产品目录，必须含主仓但不要求其它仓库齐全。读取顶层 `outcome`、`scope`、`blockers`、`checked_at` 和全部 `rows` 的 `repository`、`local`、`target_branch`、`target_sha`、`target_status`、`reason`、`refs`；模块使用返回的分支，不把主仓 release 名字机械套给 connectors；hazelcast 固定 `release-v5.5.0`。`not_covered`、`absence_unverified` 或 `unresolved` 不可作为目标仓库基线；版本与分支冲突不能以接受风险放行。
- 分支对齐只证明“该仓库在本次产品修复线应使用哪个分支”，不能单独证明缺陷属于该仓库。结合 Jira 组件/标签、问题现象、堆栈或文件路径、复现结果和目标分支源码，按以下结论展示仓库与分支后才给出方案：有可回查证据唯一指向一个仓库时，输出“建议分析/修复仓库”表，列出仓库、目标分支、SHA、分支推导理由、refs 新鲜度、核验时间和锁定证据；证据指向多个仓库时，输出“问题候选”表，逐项列出上述字段和候选理由；只有版本关系或无法唯一归属时，输出完整对齐列表并请用户确认优先分析的仓库。
- 完整对齐列表必须保留所有仓库，并分为“问题候选”、“版本关联但无问题证据”和“不参与或无法解析”三组；`not_covered`、`unchanged`、`unresolved`、`absence_unverified`、`verified_missing` 及缓存引用均须如实标注，不能静默排除或把缓存 SHA 称为已确认基线。`repository prepare` 固化的 `base_sha` 才是实施基线。
- 以上推荐和列表只服务只读分析，不等于自动登记任务仓库、创建 worktree 或开始修改。仍须由用户确认目标仓库；随后按现有 `repository add`、`repository prepare`、设计确认和授权流程继续。分支对齐失败、目标分支未解析或远端事实无法核验时，说明失败原因、已知事实和所需的 Source Pool/权限/分支条件，停止依赖该事实的基线准备，不输出猜测性推荐。
- 在登记目标仓库并完成必要源码分析后、签发任何 `task_execution` 授权前，必须以**一轮方案确认**完整展示并请求明确确认：① 修复方案——根因及证据、修改仓库/分支与范围、修复方式、风险和回滚；② 验收方案——每个检查项的用例或场景、复用/新增、执行方式、预期结果、目标仓库及验证责任人；③ 后续自动动作——在全部检查项于最终完整 SHA 得到预期结果时，自动回写 Q3 事实评论、推送并创建 Draft PR，失败、事实变化或外部回读不明确时停止。用户确认前不得记录为已确认方案、签发实施授权、修改代码或执行实施性测试；用户要求调整任一方案时，更新后重新完整展示并确认。确认后以同一确认来源固化 Q1/Q2、`fix_plan`、相应 Jira 评论授权和 `workflow/authorization.py grant`；不得再为 Q3 的成功事实重复索取“接受首轮验证”。
- 按 `checklist` 返回的 `quality_mode` 处理缺项。缺陷 `recorded_decision` 模式一次列全缺口并继续无依赖的分析，在质量检查点由用户决定处理；其它类型仍按各自准入规则。事实不可信或基线无法确定时停止对应步骤。
- 每个目标仓库登记仓库、工作分支、基线分支、范围和验证方式。
- 登记完成后立即执行受控 `task.py repository prepare`；`auto-clone` 模式由该命令自动下载项目仓库，不要求预先签发 `task_execution` 授权。直接 clone、复用已有分支或非受控 worktree 操作不属于这条自动路径。
- prepare 成功后，在当前工作空间会话执行 `task.py repository context --issue-key <issue-key> --json --dir <project-workspace>`，核对当前 run 的 worktree、分支和 `base_sha` 后直接继续源码分析。当前会话的 Git 副作用使用 `git -C <返回的 worktree> ...`，Gate 会核对路径、仓库和工作分支均属于当前 active 任务；不得启动嵌套 Agent、切换工作空间或创建会话级“当前任务”状态。
- 只有 prepare 写入的本地任务 worktree、`base_sha` 和目录摘要才是 Git 基线。远程 GitHub 读取只能写成“远程候选参考”，不能声称“已核实基线”，不能替代本地源码核验，也不能据此推进 `design_review` 或向 Jira 写入已确认方案。
- 本地基线完成后分析代码并形成方案；研发工程师确认方案后用 `workflow/authorization.py grant` 签发任务授权。
- 接管不要求已创建或关联 Test。完成受控基线后，Agent 与用户在 Q2 确认修复方案、验收场景、预期和验证方式；如何定义、编写、创建或复用 Test 由用户与 Agent 处理，AgenticOps 只引导、记录、跟进和核对。编码完成后再通过 Jira「已链接工作项」创建或关联 Test。使用 `quality.py status/apply` 完成 Q1、Q2 的记录与确认，然后进入 implementation。具体输入和恢复方法见 [质量检查与证据](../../../../docs/usage/quality-checkpoints.md)，项目标准见 `projects/tapdata/quality.json`。
- 处理过程中按 Project `status_sync.field_mappings` 提前采集 Tests Passed 所需属性：Q2 固化分类和根因依据，仓库确认时形成 Module 依据，Q2/Q4 评论分别形成 Issue Analysis/Fix Details 依据，验收方案确认 Tester、自动化属性和 Xray 关联，版本规划只作为选择 Fix Version 的依据。需要责任人选择的枚举、人员、Module、Fix Version ID 和测试例外不得自动猜测；无法可靠补齐时留到状态同步节点跳过并在 PR Ready 提示。
- Q2 前用 `task.py record --key fix_plan` 记录根因、范围、修复方式、风险与回滚。修复后用例尚未编码时把 `target_revision` 写为 `pending`；先确认稳定用例/方式，执行前用 `item` 绑定精确代码。只补充代码版本不会要求重新选择同一用例；改步骤、预期、范围或方式仍须重新确认。
- 一个检查项对应一个用例和一种方式；同检查点可有不同方式的多项。修复前不可执行须说明原因，修复后项未到检查点不算失败。`Manual` 由用户执行；`TapTest` 使用目标工程实际提供的 `write-xray-test`、`write-test-script`；`Unit` 核对产品工程的单元测试和 CI 集成测试。TapCE 当前不纳管，不算通过；若因此无法形成受管验收或 Jira Validator 阻塞，请用户调整 Jira 或验收方案并重新读取事实。AgenticOps 不创建 Test、不编写用例、不执行环境，只建议、记录和核对。
- 新增仓库或修改分支、范围、验证方式后必须重新确认和授权。
- Hook 首次返回 `ask` 或 `deny` 时，立即完整展示原因、处理动作和停止点，停止当前操作及依赖步骤；不要把阻断当作正常门禁后继续，不得换 GitHub API、直接 Git 或其它工具绕过前置证据。

## 实现、PR、CI 和完成

- 每个仓库分别验证并记录提交、PR 和 CI，任务级证据统一汇总。
- 每次原子操作成功后继续下一项已授权工作；用 `task.py next --issue-key <issue-key>` 查看门禁、检查点和待回写评论。已有任务授权覆盖的编码、测试、提交、推送、Draft PR 和 Jira 回写不再逐步询问，仍执行各自门禁。Q3 使用 `auto_checkpoint`：仅当 Q2 已选的全部修复后检查项都在最终完整 SHA 得到预期结果时自动记录和回写；它不是用户验收。`next` 只是只读建议，不授予新权限，也不能代替实际完成阶段工作。
- 暂停时展示 `quality.py status` 或 `task.py next` 中该检查点的 `handoff`：说明为什么停、具体用例/步骤/预期、仓库与完整提交 SHA、谁来验证、需返回的日志/报告及可选处置。需要用户启动本地环境时，先提供候选 SHA、分支/推送状态、构建与启动方式、环境前置条件、测试数据和失败日志要求；用户在其它机器或共享环境验证时，先按授权推送分支或 Draft PR。手工执行必须先给可操作 `steps`；只有日志但没有目标提交时不可猜 SHA 或把分支名导入执行证据。一次列全需要用户决定的项目；恢复后复用已确认事实，不重复问同一问题。
- 首轮本地自动测试可绑定 `git_revision` 返回的工作区指纹；手工证据及最终验收只用完整提交 SHA。提交后重新核对/执行验证并使用实际产物 SHA，不能把提交前报告改写为提交后运行。缺证据可如实记录风险或延期，不能填充 PASS。
- 有意义变更且完成第一轮针对性验证后建议 Draft PR；如验证受阻，按项目标准披露现状。Q3 是自动首轮事实检查点，使用 `execute` 导入可回查报告并在所有已选修复后项符合预期时执行 `auto_checkpoint`；Q4 才展示关联 Test、当前 SHA 的执行证据与风险，使用 `decide/checkpoint` 记录用户最终验收。用户可接受风险，不得把未执行、跳过、未知或失败改成通过。
- Q4 完成并进入 `ci_validation` 后，立即读取 Jira issue、已链接 Test、每个 Test 的 Test Details 和可用 transitions，执行 `jira_status.py prepare --trigger tests_passed`。每个受管 Test 都必须在 Q4 以同一 `case_ref`、当前 Jira 用例版本和对应方式记录当前完整 SHA 的 PASS 证据，并由用户逐项 `accept` 确认；Q4 总体也必须是 `accept`。Jira 已是 `Tests Passed` 也先核对这些事实；当前为 `In Progress` 且返回 `ready` 时同步尝试一次并写后回读。缺少 Jira 事实、关联、类型、版本或确认时，工具提示用户补充/调整后可以重新做预检；已发起的 Jira 转换不在同一节点盲目重试。
- PR Ready 前必须更新每个任务仓库当前 PR Head 的 `ci.py watch` 记录，再以同一 Jira 快照运行 `pr_ready.py` 复核。Test 工作项本身无需 Done。所有 PR Checks 明确成功且绑定当前 Head、Q1-Q4 及检查项满足要求后才可称为 PR Ready。状态同步遗留统一提示 Engineering DRI 人工处理；`Pull Request Submitted` 不由 Agent 自动执行。
- CI 返回成功不能证明目标用例运行，须核对实际报告、目标提交和运行编号。Q5、Q6 核对审查及交付事实。接管和 Q4 节点只尝试 Project 明确配置的单次 Jira 状态同步；线上 Validator 与附件冲突需报告确认，禁止用本地质量处置绕过。
- 合并、发布、Tag、rebase、强推和保护分支写入不被任务授权覆盖。
- 用 `workflow/evidence.py --issue-key <issue-key> --dir <project-workspace>` 汇总结果；启用质量检查时用 `quality.py` 保存草稿、用户确认及发送意图，再调用原生 Jira 工具并回读核对。外部结果不明确时先核对，不盲目重发；具体恢复步骤见质量文档。
- 每个检查点确认或自动记录后立即回写 Jira，不等 Q6。使用该点的 `publication_body`，`draft` 同时指定 `checkpoint`，随后 `confirm → prepare_write → 原生发送 → receipt/readback`。方案展示时一并说明 Q1/Q2 和合格 Q3 事实将回写 Jira；已有授权或同一回复明确覆盖该内容回写时，引用真实来源完成账本，不额外逐条追问。Q4 仍须以当前 SHA 的 Test 证据取得用户最终验收。不明外部写入先回读；只能暂停依赖该写入的步骤，并告诉用户哪些准备工作仍可继续。
- 未迁移能力优先使用 Agent 原生能力；没有安全路径时只暂停当前副作用步骤。
- 暂停后恢复同一 run 使用 `activate`；重做使用 cleanup 后精确绑定当前 run 的 reset。只有任务 inactive、run 精确匹配且研发工程师明确确认时才执行任务级 `purge`；脏 worktree 必须保留现场，未合并分支不得强删，Jira 不受本地 purge 影响。
