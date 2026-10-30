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

整理 Fix Details 与 PR 时按[交付文案内容](../../../../../docs/usage/quality-checkpoints.md#交付文案内容)核对真实 diff、当前证据与远端原文，补全技术说明并保留人工编辑；字段建议或 confirmed 不代替内容及新鲜度核验。

修改 `tapdata-common-lib` 下代码前，先按[公共库升级](../../../runbooks/common-lib-upgrade.md)判断兼容性，并落实不兼容变更的 release 分支前置条件；CI 验证同时核对 Nexus 发布及消费依赖结果。

- 创建或编辑 PR 正文时，按[PR 正文发布与回读](../../../../../docs/usage/quality-checkpoints.md#pr-正文发布与回读)每仓准备真实多行 UTF-8 文件，调用 `workflow/pr_body.py preflight`；CLI 用 `--body-file`，不把正文拼入 Shell。原生写入后回读 `number,url,body` 并用 `compare` 核对目标与全文，再报告描述已核验。提示不自动替换合法转义；已存在 PR 保留人工修改，未知结果先回读原 PR，不重建。纯正文修正不触发来源分支合并或代码重验，正文问题不阻塞无依赖工作；测试总结、实际 `AgenticOps 版本 XXX` 标识与补测关联按项目 PR Ready 合同核对。

- 接管、阶段推进和质量操作返回 `sync_actions` 时，先核对已有授权，在当前轮次的安全边界主动处理；恢复使用 task next 或 external_sync status 查全部历史待办，不等待研发提醒。接管摘要不冒充 Q1，检查点使用完整有效正文，未知操作复用原记录回读，不重发。已知未发送说明原因并交接；只读待办不代表发送保证。
- completed 后只可按[有限回执恢复](../../../../../docs/usage/quality-checkpoints.md#同步待办与有限回执恢复)补录已有操作；不得新建草稿或发送。退出脚本先处理可确定事项；未知外部结果保留原操作信息并交接，不重复发送，对明确未发送或仅同步记录缺项，不阻断无依赖清理；外部结果未知仍先回读原操作并停止受影响清理及归档。待办展示失败与本地操作失败分开，先回读原操作，避免重复推进。

- 提交与 PR 准备遵循[分支修复与测试维护](../../../runbooks/build-test-and-local-run.md#测试与提交边界)：业务及适用普通 `test` 与 IT强制分开提交，前者可合入低版本，后者不得回补；核验合入方式保留回补边界；用户回补后回读实际 release 成果并完成独立功能验证，再创建或更新 PR。不代办跨分支合入，不以 develop 报告替换 release 证据。
- 创建或提审前端代码 PR 时，按[PR 默认审查人](../../../runbooks/build-test-and-local-run.md#pr-默认审查人)应用 Feynman 默认审查安排，核对账号、请求资格及写后回读；用户明确指定的安排优先，未确认账号不猜测。
- 每个仓库分别验证并记录提交、PR 和 CI，任务级证据统一汇总。按[共同验证材料](../../../../../docs/usage/quality-checkpoints.md#共同验证材料)用现有 quality.py verification 动作记录本地测试、来源同步、CI 报告与审查材料；缺失或版本失效时先补齐，不直接改阶段。失败使用 failures.py 原 problem_id 记录 start/finish，替代旧 PR 独立预算入口。
- 功能与缺陷 Q2 方案确认时都要明确 Wiki 依据的 IT级别、判定理由、范围、用例复用/新增/修改和验证安排，按[共同验证材料](../../../../../docs/usage/quality-checkpoints.md#共同验证材料)记录 integration_tests，按仓库/场景/单一级别拆行，cases 逐用例区分复用、修改、新增，与现有方案一次确认。不能只列命令或让研发先交用例清单。
- 功能与缺陷在方案分析时使用 [tapdata-ci-test](../../tapdata-ci-test/SKILL.md) 判断集成测试复用、新增或修改及框架可用性；实现后调用其编写、执行、报告分析和修复能力，PR CI 返回后再次调用报告分析。具体测试步骤由该技能及其 Runbook 维护；主流程负责授权、质量记录、阶段、提交推送和 CI 等待，不复制测试步骤。
- 集成测试技能、框架、Wiki 或环境不可用时，展示具体模块、场景、依据和未覆盖范围，继续无依赖工作。区分应写未写与已写未运行，按[测试编写缺口与 PR 总结](../../../../../docs/usage/quality-checkpoints.md#测试编写缺口与-pr-总结)统一处理：框架不支持（包括当前全部 L3）关联 Jira Manual Test 并完成验证，按场景去重创建或复用兼容 Jira 任务给 Leon，关联手动用例与原任务并回读。复用既有 item/execute/decide 和 local.it_followups，Q4 核验手动闭环；取消每仓 GitHub Issue。临时环境或查询失败先说明恢复方式，不默认转成编写缺口。建票不替代研发决定或测试通过；未知外部结果先回读，不重复创建。
- 功能与缺陷在每轮实现或返工完成、正式验证开始前，按[PR 前同步检出来源](../../../../../docs/usage/task-authorization.md#pr-前同步检出来源)核对并同步已登记 target_branch，保留冻结基线并确认具体 Merge 授权。验证期间不追赶正常推进的远端；推送同一候选、修改正文或转 Ready 不单独触发同步。Draft 可承载人工验收和 CI；按[PR 正文发布与回读](../../../../../docs/usage/quality-checkpoints.md#pr-正文发布与回读)更新待验收及最终总结，分别对应当前 Head 的本地、人工和 CI 事实。已知不兼容先处理，冲突退回沿现有返工与完成检查闭环。
- 每次原子操作成功后继续下一项已授权工作；用 `task.py next --issue-key <issue-key>` 查看门禁、检查点和待回写评论。已有任务授权覆盖的编码、测试、提交、推送、Draft PR 和 Jira 回写不再逐步询问，仍遵守平台权限、外部事实回读和流程检查点。Q3 使用 `auto_checkpoint`：仅当 Q2 已选的全部修复后检查项满足各自证据合同（TapTest 使用项目配置的 Jira 状态依据）时自动记录和回写；它不是用户验收。`next` 只是只读建议，不授予新权限，也不能代替实际完成阶段工作。
- 暂停时展示 `quality.py status` 或 `task.py next` 中该检查点的 `handoff`：说明为什么停、具体用例/步骤/预期、仓库与完整提交 SHA、谁来验证、需返回的日志/报告及可选处置。需要用户启动本地环境时，先提供候选 SHA、分支/推送状态、构建与启动方式、环境前置条件、测试数据和失败日志要求；用户在其它机器或共享环境验证时，先按授权推送分支或 Draft PR。手工执行必须先给可操作 `steps`；只有日志但没有目标提交时不可猜 SHA 或把分支名导入执行证据。一次列全需要用户决定的项目；恢复后复用已确认事实，不重复问同一问题。
- 首轮本地自动测试可绑定 `git_revision` 返回的工作区指纹；手工证据及最终验收只用完整提交 SHA。提交后重新核对/执行验证并使用实际产物 SHA，不能把提交前报告改写为提交后运行。缺证据可如实记录风险或延期，不能填充 PASS。
- 有意义变更且完成第一轮针对性验证后建议 Draft PR；如验证受阻，按项目标准披露现状。Q3 是自动首轮事实检查点，使用 `execute` 导入真实报告、`jira_status` 导入 TapTest 状态，在所有已选修复后项符合合同后执行 `auto_checkpoint`；Q4 才展示验收检查项（含实际关联 Test）、当前 SHA 的执行证据与风险，使用 `decide/checkpoint` 记录用户最终验收。用户可接受风险，不得把未执行、跳过、未知或失败改成通过。
- 仅对启用 `status_sync` 的任务：Q4 完成并进入 `ci_validation` 后，立即读取 Jira issue、已链接 Test、每个 Test 的 Test Details 和可用 transitions，执行 `jira_status.py prepare --trigger tests_passed --operation-id <op-id>`。Manual/Unit 在 Q4 以同一 `case_ref`、当前用例版本和对应方式记录当前完整 SHA 的 PASS 证据及逐项 `accept`；TapTest 导入 `quality.py` 的 `jira_status` 动作，按[状态依据](../../../../../docs/usage/quality-checkpoints.md#taptest-的-jira-状态依据)接纳，不要求本地 PASS 或逐项确认；Q4 总体须满足对应合同。Jira 已是 `Tests Passed` 也先核对这些事实；当前为 `In Progress` 且返回 `ready` 时同步尝试一次并写后回读。缺少 Jira 事实、关联、类型、版本或确认时，工具提示用户补充/调整后可以重新做预检；已发起的 Jira 转换不在同一节点盲目重试。
- 每个 PR 创建或更新时读取实际安装版本并显示 `AgenticOps 版本 XXX`，提审前按[PR 内容模板](../../../../../docs/usage/quality-checkpoints.md#pr-内容模板与兼容发布)纳入 `required_pr_summaries` 返回的当前覆盖区块和尾部元信息，普通关联与版本置尾一次，不重复正常 CI 流水，并回读 PR 正文及手动路径所需 Jira 关联。每个 PR 都要核对目标集成测试是否实际运行；其他 Checks 绿色、未触发、跳过、零用例或缺报告均主动展示具体缺口并请求研发明确处置，不能直接进入下一步或以建票代替确认。使用现有 verification 的原始结果和 decision 记录，确认不等于 PASS，也不解除用例验收、失败、PR Checks 和服务端条件。
- PR Ready 前必须更新每个任务仓库当前 PR Head 的 `ci.py watch` 记录，再以同一 Jira 快照运行 `pr_ready.py` 复核。Test 工作项本身无需 Done。Checks 全部明确成功或具有绑定当前 Head 及具体缺口的有效提审例外，并满足 Q1-Q4 与检查项时，只表示预检通过。按有效授权使用原生 GitHub 工具推进剩余 PR，结果未知先回读；所有目标 PR 身份、当前 Head、OPEN 及 isDraft=false 回读确认后才可称为 PR Ready。证据与具体例外使用[PR Ready 核对](../../../../../docs/usage/quality-checkpoints.md#pr-ready-核对)，不重复索取已覆盖提审的决定。状态同步遗留统一提示 Engineering DRI 人工处理；`Pull Request Submitted` 不由 Agent 自动执行。
- 使用原生工具取得每个 PR 当前 Head 对应的 CI 运行和实际报告，交 tapdata-ci-test 分析。首次失败先用稳定 check_id、归因及证据 observe，复发复用原 problem_id，再按原授权与累计轮次 start 修复。定向调试后提交候选，在完整提交 SHA 上完成最终本地重验并记录结果，再推送、等待新 CI，将新报告交回技能分析；非当前变更、归因不明或轮数用尽按质量合同交研发处置。返回结果写入现有质量材料，测试总结写 Jira 讨论并回读，不改写任务描述代替报告。Q5、Q6 核对审查及交付事实。接管和 Q4 节点只尝试 Project 明确配置的单次 Jira 状态同步；线上 Validator 与附件冲突需报告确认，禁止用本地质量处置绕过。
- PR 审查按[审查返工](../../../../../docs/usage/quality-checkpoints.md#pr-审查返工)完整回读普通评论及行内线程，逐项修复或说明依据；改变验收预期交研发决定。相同失败沿用原 problem_id，使用失败工具的 review 来源累计轮次，修改后重验当前代码并回读当前 PR CI；不以线程过时或已关闭替代问题核对。
- PR 合入、发布、Tag、rebase、强推和保护分支写入不被任务授权覆盖。检出来源分支合并到任务工作分支也须明确纳入方案授权，不因需要保持最新而绕过授权。
- 用 `workflow/evidence.py --issue-key <issue-key> --dir <project-station>` 汇总结果；启用质量检查时用 `quality.py` 保存草稿、用户确认及发送意图，再调用原生 Jira 工具并回读核对。外部结果不明确时先核对，不盲目重发；具体恢复步骤见质量文档。
- 每个检查点确认后尽力回写简洁人读评论，保留 draft/confirm/prepare_write/receipt/readback。已知未写入可用 receipt result=deferred 并给 reason；超时或结果不明记 unknown，先回读原操作。评论、水印、版本或状态同步失败均不阻止不依赖它的本地研发，PR 后用 evidence.py 统一输出执行过程被跳过的处理与警告。
- 未迁移能力优先使用 Agent 原生能力；没有安全路径时只暂停当前副作用步骤。

归档写入失败时，按[任务授权指引](../../../../../docs/usage/task-authorization.md#清理与退出)展示指定快照、失败原因和实际清理结果，引导研发明确授权修复后重试或放弃。复用原操作和失败摘要，不自动丢弃，不以新 clean 操作绕过；回读结果不明时先核实。
