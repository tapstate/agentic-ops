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
5. 核对负责人和状态映射后进入 `task_intake`，持续完成准入、仓库登记、本地基线准备和源码分析，直到方案确认、风险授权、事实不可信或其它真实人工决策点。

## 准入、设计和多仓库

- 用 `task.py checklist` 获取机读准入要求，不得凭聊天猜测。
- 缺陷的“问题版本”只取 Jira `fields.versions`（影响版本，多选），不读描述中的同名章节，也不取 `fixVersions`。保留全部版本和来源；先检查 `develop` 的对应源码/复现，存在同一缺陷就优先修复 `develop`，其余影响版本列为研发人工合并修复。确认 `develop` 不受影响后，只选择一个影响版本编码；多候选时由用户选择，不能按 Jira 数组顺序猜测。
- 仓库基线准备前，按质量文档的输入格式执行 `task.py issue-versions --input <jira-and-develop-evidence.json> --expected-run-id <run>`。工具从主仓核验所有影响版本分支及 develop SHA；分支不存在直接拒绝，网络/权限错误是“未核验”，不能视为 develop 不受影响。分析证据与当前 SHA 不同需重新分析；不要用 `record problem_version` 或手改本地状态代替。
- 若首次判断缺陷必须先准备 develop 工作树，可以先做受控只读分析，再导入初次版本规划；工具核对已准备基线与修复线及主仓 SHA 一致。只有切换修复线或修改已固化规划才需 cleanup/reset，不为完成一次必要调查强制重开任务。
- 用已确定的 `primary_branch` 调用 TapData 分支对齐；`--tapdata-root` 是包含各模块仓库的产品目录。模块使用返回的分支，不把主仓 release 名字机械套给 connectors；hazelcast 固定 `release-v5.5.0`。版本与分支冲突不能以接受风险放行。
- 按 `checklist` 返回的 `quality_mode` 处理缺项。缺陷 `recorded_decision` 模式一次列全缺口并继续无依赖的分析，在质量检查点由用户决定处理；其它类型仍按各自准入规则。事实不可信或基线无法确定时停止对应步骤。
- 每个目标仓库登记仓库、工作分支、基线分支、范围和验证方式。
- 登记完成后立即执行受控 `task.py repository prepare`；`auto-clone` 模式由该命令自动下载项目仓库，不要求预先签发 `task_execution` 授权。直接 clone、复用已有分支或非受控 worktree 操作不属于这条自动路径。
- prepare 成功后，在当前工作空间会话执行 `task.py repository context --issue-key <issue-key> --json --dir <project-workspace>`，核对当前 run 的 worktree、分支和 `base_sha` 后直接继续源码分析。当前会话的 Git 副作用使用 `git -C <返回的 worktree> ...`，Gate 会核对路径、仓库和工作分支均属于当前 active 任务；不得启动嵌套 Agent、切换工作空间或创建会话级“当前任务”状态。
- 只有 prepare 写入的本地任务 worktree、`base_sha` 和目录摘要才是 Git 基线。远程 GitHub 读取只能写成“远程候选参考”，不能声称“已核实基线”，不能替代本地源码核验，也不能据此推进 `design_review` 或向 Jira 写入已确认方案。
- 本地基线完成后分析代码并形成方案；研发工程师确认方案后用 `workflow/authorization.py grant` 签发任务授权。
- 缺陷接管时读取现有 Test Coverage；在方案确定的同时建议验收用例、复用／新增、预期及验证方式，交由用户选择。使用 `quality.py status/apply` 完成 Q1、Q2 的记录与确认，然后进入 implementation。具体输入和恢复方法见 [质量检查与证据](../../../../docs/usage/quality-checkpoints.md)，项目标准见 `projects/tapdata/quality.json`。
- Q2 前用 `task.py record --key fix_plan` 记录根因、范围、修复方式、风险与回滚。修复后用例尚未编码时把 `target_revision` 写为 `pending`；先确认稳定用例/方式，执行前用 `item` 绑定精确代码。只补充代码版本不会要求重新选择同一用例；改步骤、预期、范围或方式仍须重新确认。
- 一个检查项对应一个用例和一种方式；同检查点可有不同方式的多项。修复前不可执行须说明原因，修复后项未到检查点不算失败。TapTest 使用目标工程实际提供的 `write-xray-test`、`write-test-script`；不可用时请用户选择其它方式。集成用例在产品模块工程，先核对代码和 CI；已有覆盖可复用，新覆盖需实现。AgenticOps 只建议、记录和核对。
- 新增仓库或修改分支、范围、验证方式后必须重新确认和授权。
- Hook 首次返回 `ask` 或 `deny` 时，立即完整展示原因、处理动作和停止点，停止当前操作及依赖步骤；不要把阻断当作正常门禁后继续，不得换 GitHub API、直接 Git 或其它工具绕过前置证据。

## 实现、PR、CI 和完成

- 每个仓库分别验证并记录提交、PR 和 CI，任务级证据统一汇总。
- 每次原子操作成功后继续下一项已授权工作；用 `task.py next --issue-key <issue-key>` 查看门禁、检查点和待回写评论。已有任务授权覆盖的编码、测试、提交、推送、Draft PR 和 Jira 回写不再逐步询问，仍执行各自门禁。`next` 只是只读建议，不授予新权限，也不能代替实际完成阶段工作。
- 暂停时展示 `quality.py status` 或 `task.py next` 中该检查点的 `handoff`：说明为什么停、具体用例/步骤/预期、仓库与完整提交 SHA、谁来验证、需返回的日志/报告及可选处置。手工执行必须先给可操作 `steps`；只有日志但没有目标提交时不可猜 SHA 或把分支名导入执行证据。一次列全需要用户决定的项目；恢复后复用已确认事实，不重复问同一问题。
- 首轮本地自动测试可绑定 `git_revision` 返回的工作区指纹；手工证据及最终验收只用完整提交 SHA。提交后重新核对/执行验证并使用实际产物 SHA，不能把提交前报告改写为提交后运行。缺证据可如实记录风险或延期，不能填充 PASS。
- 有意义变更且完成第一轮针对性验证后建议 Draft PR；如验证受阻，按项目标准披露现状。Q3 记录首轮事实及处置，Q4 核对关联用例。用 `execute` 导入可回查报告，再展示原始结果、版本及风险，使用 `decide/checkpoint` 记录真实用户决定；用户可接受风险，不得把未执行、跳过、未知或失败改成通过。
- CI 返回成功不能证明目标用例运行，须核对实际报告、目标提交和运行编号。Q5、Q6 核对审查及交付事实。本版不自动改变 Jira 状态；线上 Validator 与附件冲突需报告确认，禁止用本地质量处置绕过。
- 合并、发布、Tag、rebase、强推和保护分支写入不被任务授权覆盖。
- 用 `workflow/evidence.py --issue-key <issue-key> --dir <project-workspace>` 汇总结果；启用质量检查时用 `quality.py` 保存草稿、用户确认及发送意图，再调用原生 Jira 工具并回读核对。外部结果不明确时先核对，不盲目重发；具体恢复步骤见质量文档。
- 每个检查点确认后立即回写 Jira，不等 Q6。使用该点的 `publication_body`，`draft` 同时指定 `checkpoint`，随后 `confirm → prepare_write → 原生发送 → receipt/readback`。方案/验收展示时一并说明确认内容将回写 Jira；已有授权或同一回复明确覆盖该内容回写时，引用真实来源完成账本，不额外逐条追问。不明外部写入先回读；只能暂停依赖该写入的步骤，并告诉用户哪些准备工作仍可继续。
- 未迁移能力优先使用 Agent 原生能力；没有安全路径时只暂停当前副作用步骤。
- 暂停后恢复同一 run 使用 `activate`；重做使用 cleanup 后精确绑定当前 run 的 reset。只有任务 inactive、run 精确匹配且研发工程师明确确认时才执行任务级 `purge`；脏 worktree 必须保留现场，未合并分支不得强删，Jira 不受本地 purge 影响。
