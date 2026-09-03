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
3. 新任务先读取 Jira 事实，再执行 `task.py init --issue-key TAP-xxx --task-class <defect_fix|feature_change|technical_task> --dir <project-workspace>`；初始化不会停用其它 active 任务。
4. 已存在任务必须让研发工程师选择继续现有 run，或清理后用当前 `--expected-run-id` reset；选择完成后继续流程，不把接管、activate 或 reset 的原子成功当作停点。
5. 核对负责人和状态映射后进入 `task_intake`，持续完成准入、仓库登记、本地基线准备和源码分析，直到方案确认、风险授权、事实不可信或其它真实人工决策点。

## 准入、设计和多仓库

- 用 `task.py checklist` 获取机读准入要求，不得凭聊天猜测。
- 按 `checklist` 返回的 `quality_mode` 处理缺项。缺陷 `recorded_decision` 模式一次列全缺口并继续无依赖的分析，在质量检查点由用户决定处理；其它类型仍按各自准入规则。事实不可信或基线无法确定时停止对应步骤。
- 每个目标仓库登记仓库、工作分支、基线分支、范围和验证方式。
- 登记完成后立即执行受控 `task.py repository prepare`；`auto-clone` 模式由该命令自动下载项目仓库，不要求预先签发 `task_execution` 授权。直接 clone、复用已有分支或非受控 worktree 操作不属于这条自动路径。
- prepare 成功后，在当前工作空间会话执行 `task.py repository context --issue-key <issue-key> --json --dir <project-workspace>`，核对当前 run 的 worktree、分支和 `base_sha` 后直接继续源码分析。当前会话的 Git 副作用使用 `git -C <返回的 worktree> ...`，Gate 会核对路径、仓库和工作分支均属于当前 active 任务；不得启动嵌套 Agent、切换工作空间或创建会话级“当前任务”状态。
- 只有 prepare 写入的本地任务 worktree、`base_sha` 和目录摘要才是 Git 基线。远程 GitHub 读取只能写成“远程候选参考”，不能声称“已核实基线”，不能替代本地源码核验，也不能据此推进 `design_review` 或向 Jira 写入已确认方案。
- 本地基线完成后分析代码并形成方案；研发工程师确认方案后用 `workflow/authorization.py grant` 签发任务授权。
- 缺陷接管时读取现有 Test Coverage；在方案确定的同时建议验收用例、复用／新增、预期及验证方式，交由用户选择。使用 `quality.py status/apply` 完成 Q1、Q2 的记录与确认，然后进入 implementation。具体输入和恢复方法见 [质量检查与证据](../../../../docs/usage/quality-checkpoints.md)，项目标准见 `projects/tapdata/quality.json`。
- 一个检查项对应一个用例和一种方式；同检查点可有不同方式的多项。修复前不可执行须说明原因，修复后项未到检查点不算失败。TapTest 使用目标工程实际提供的 `write-xray-test`、`write-test-script`；不可用时请用户选择其它方式。集成用例在产品模块工程，先核对代码和 CI；已有覆盖可复用，新覆盖需实现。AgenticOps 只建议、记录和核对。
- 新增仓库或修改分支、范围、验证方式后必须重新确认和授权。
- Hook 首次返回 `ask` 或 `deny` 时，立即完整展示原因、处理动作和停止点，停止当前操作及依赖步骤；不要把阻断当作正常门禁后继续，不得换 GitHub API、直接 Git 或其它工具绕过前置证据。

## 实现、PR、CI 和完成

- 每个仓库分别验证并记录提交、PR 和 CI，任务级证据统一汇总。
- 有意义变更且完成第一轮针对性验证后建议 Draft PR；如验证受阻，按项目标准披露现状。Q3 记录首轮事实及处置，Q4 核对关联用例。用 `execute` 导入可回查报告，再展示原始结果、版本及风险，使用 `decide/checkpoint` 记录真实用户决定；用户可接受风险，不得把未执行、跳过、未知或失败改成通过。
- CI 返回成功不能证明目标用例运行，须核对实际报告、目标提交和运行编号。Q5、Q6 核对审查及交付事实。本版不自动改变 Jira 状态；线上 Validator 与附件冲突需报告确认，禁止用本地质量处置绕过。
- 合并、发布、Tag、rebase、强推和保护分支写入不被任务授权覆盖。
- 用 `workflow/evidence.py --issue-key <issue-key> --dir <project-workspace>` 汇总结果；启用质量检查时用 `quality.py` 保存草稿、用户确认及发送意图，再调用原生 Jira 工具并回读核对。外部结果不明确时先核对，不盲目重发；具体恢复步骤见质量文档。
- 未迁移能力优先使用 Agent 原生能力；没有安全路径时只暂停当前副作用步骤。
- 暂停后恢复同一 run 使用 `activate`；重做使用 cleanup 后精确绑定当前 run 的 reset。只有任务 inactive、run 精确匹配且研发工程师明确确认时才执行任务级 `purge`；脏 worktree 必须保留现场，未合并分支不得强删，Jira 不受本地 purge 影响。
