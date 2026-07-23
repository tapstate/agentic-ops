# 完整设计实现方案

## 1. 决策背景

当前仓库已经跑通第一阶段本地 fake flow，但项目设计文档、AI 员工手册、用户故事、工作流配置和 Standard Process Registry 已经把 AgenticOps 定义为完整的研发任务接管控制体系。

2026-07-23 用户决策采用方案 B：完整设计作为当前必须实现边界，不再把真实 Jira gate、所有权绑定、profile / policy、诊断和更新能力仅视为远期文档目标。

## 2. 实现目标

AgenticOps 必须从本地 fake flow 升级为可接真实研发流程的受控 CLI 运行时。完成后，`agentic-cli` 至少应具备：

- 机器可读 操作契约能表达输入、输出、前置 gate、失败码、副作用、重试和重做规则。
- 工作流配置能校验 Jira 字段、状态、transition、任务分类、标准流程和本地源码映射。
- `takeover-task` 能执行所有权 gate、字段 gate、任务分类和标准流程选择。
- `resume-takeover` 能读取已有 run 和事件，校验 `workspace`、`issue`、`owner`、目标仓库和恢复阶段。
- `doctor`、`feedback bundle --redact`、`update check/apply`、`profile validate/update/rollback`、`policy validate/update/rollback` 具备结构化输出。
- Jira 写入、Git push、PR、merge、发布和门禁放宽必须受 policy / gate / confirmation 控制。

## 3. 非目标

本方案不引入 Web 控制台、后台 daemon、自动分配任务、自动 push、自动创建 PR、自动 merge 或自动修改公司规范。真实写操作可以被 CLI operation 支持，但必须默认受人工确认和审计控制。

## 4. 分阶段设计

### 阶段 1: Contract / Schema 基线

目标是让 `contracts/operations/*.yaml` 成为真正可验证的机器可读契约源头，而不是只包含 operation 名称的轻量清单。

实现内容：

- 扩展 Go contract model，支持 `input`、`preconditions`、`output`、`failure`、`side_effects`、`human_gate`、`retry_policy` 和 `redo_from_stage`。
- 增加 contract validation，校验每个 operation 至少包含稳定输入、输出、失败码、副作用和人工 gate 声明。
- 扩展现有 operation YAML，至少覆盖已实现命令和 takeover / resume / write evidence 的完整字段。
- 保持 `contracts/operations/` 是唯一机器可读契约源头。

### 阶段 2: Profile / Process 映射

目标是让 工作流配置和 Standard Process Registry 进入 CLI 校验路径。

实现内容：

- 定义 profile 文件结构和默认 profile 示例。
- 实现 `profile validate --workspace <name>`，校验 field mapping、task class mapping、standard process mapping、status mapping 和 transition mapping。
- 实现 `profile update` 与 `profile rollback` 的本地资产更新和恢复。
- 未知 Jira 状态、缺失字段映射或任务分类缺口必须返回稳定 gap code，不能让 AIAgent 猜测。

### 阶段 3: Jira Adapter 与 Ownership Gate

目标是让任务接管从 fake lookup 升级为设计中的受控接管。

实现内容：

- 定义 Jira adapter 接口，支持 current user、issue search、issue get、comment write 和受控字段更新。
- 保留 fake adapter 作为测试和本地 e2e 的默认实现。
- `takeover-task` 执行 assignee、`current_agent_id`、`task_class`、`process_id`、状态入口、目标仓库、验收标准和验证方式 gate。
- 接管成功写入 `run_id`、`agent_id`、`current_agent_id`、`takeover_at`、`task_class`、`process_id`、`current_stage` 和 `next_action`。
- `resume-takeover` 读取 run summary 和 events，校验恢复条件后返回 previous stage、current stage 和 next action。

### 阶段 4: Problem Resolution Commands

目标是补齐正式使用前的问题诊断、分类修复和同步能力。

实现内容：

- `doctor --workspace <name>` 输出安装、版本、profile、policy、Jira adapter、GitHub CLI、workspace 和业务仓库匹配检查结果。
- `feedback bundle --workspace <name> --run-id <run_id> --redact` 生成脱敏诊断包。
- `update check` 和 `update apply` 支持 `optional`、`recommended` 和 `required` 三种级别；required update 只阻断受影响 operation。
- `policy validate/update/rollback` 支持 push、PR、Jira comment、scope change 等 gate 配置。

### 阶段 5: Completion / Cleanup / E2E

目标是补齐执行过程所有权检查、完成清理和端到端验收。

实现内容：

- 每个读取任务、修改代码、写 evidence、推进状态或请求人工 gate 的 operation 前重新检查所有权。
- 完成或明确交接后，通过受控 operation 清理 `current_agent_id`，并记录 `current_agent_id_cleared=true`。
- 增加 `tests/e2e/problem-resolution-flow.sh`。
- 增加 profile hotfix、policy rollback、missing Jira field gate 和 release manifest e2e。

## 5. 执行顺序

必须按阶段推进。阶段 1 是后续所有工作的前置条件；阶段 2 是真实接管 gate 的前置条件；阶段 3 是任务接管一致性的核心；阶段 4 和阶段 5 完成正式使用前的问题修复和验收闭环。

每个阶段必须：

- 先写失败测试。
- 再写最小实现。
- 同步更新文档、契约、测试和运行资产。
- 运行 `go test ./...`。
- 运行相关 e2e。
- 保持 stdout 结构化 JSON、stderr 人类诊断日志。
- 不提交 secrets、tokens、private keys 或原始敏感日志。

## 6. 决策记录

- 采用完整设计作为当前必须实现边界。
- `implementation-plan-v1.md` 的 fake flow 仍作为已完成的本地基线，但不再限制后续实现范围。
- `problem-resolution-plan-v1.md`、Standard Process Registry、工作流配置、操作契约和 AI 员工手册共同定义后续实现目标。
