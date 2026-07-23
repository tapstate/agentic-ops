# 项目维护者故事

## 1. 范围

本文记录 AgenticOps 项目维护者故事。项目维护者是维护 `tapstate/agentic-ops` 源头仓库、发布资产和标准规范的人，负责让 AgenticOps 的设计、契约、运行资产、CLI、测试和文档持续一致。

本文只记录故事线，不记录实施计划、checkbox、当前完成度或剩余工作。

## 2. PM-001 维护故事线、设计和计划边界

作为项目维护者，
我希望 AgenticOps 的故事线、设计和计划分层清晰，
以便后续先确认故事线，再确认设计，再开发，最后按故事线验收。

### 触发方式

```text
整理 AgenticOps 故事线。
调整 AgenticOps 设计边界。
根据设计形成后续计划。
```

### 前置条件

- 当前工作位于 `tapstate/agentic-ops` 源头仓库。
- 已读取项目规则和现有故事线、设计、计划。
- 变更不涉及 secrets、tokens、private keys 或原始敏感日志。

### 主流程

1. 项目维护者先判断变更属于故事线、设计、计划、运行资产还是实现代码。
2. 故事线文档只记录主角、目标、触发、输出、失败路径和验收口径。
3. 设计文档只记录稳定设计事实、角色责任、事实源、能力边界和门禁。
4. 计划文档记录任务拆解、checkbox、验证命令、当前状态和剩余工作。
5. 如果发现设计缺口涉及产品、流程、权限或事实源取舍，先提示用户决策。
6. 文档、契约、运行资产、测试和代码发生公开行为变化时保持同步。

### 输出

```json
{
  "ok": true,
  "operation": "maintain_document_boundary",
  "changed_layer": "storyline",
  "next_action": "review_design_impact"
}
```

### 失败处理

- 如果设计文档混入计划 checkbox，移回 `plans/`。
- 如果计划文档改变稳定能力边界，同步更新设计或明确需要用户决策。
- 如果故事线缺少验收口径，不能进入设计调整。

### 验收标准

- 故事线、设计、计划和实现状态没有混写。
- 审阅者能从故事线追溯到设计，再追溯到计划和验收命令。
- 需要用户决策的缺口被明确标出，不被写成默认实现。

### 保护行为

- 故事线不能记录阶段任务、checkbox、当前完成度或 Implementation note。
- 设计文档不能把未决产品、流程、权限或事实源取舍写成默认实现。
- 计划必须基于已确认故事线和相对稳定设计拆解。
- 公开行为变化必须同步检查故事线、设计、契约、运行资产、测试和文档入口。

### 审核问题

- 这次变更属于故事线、设计、计划、运行资产还是实现代码。
- 是否有计划内容进入故事线或设计文档。
- 是否有设计缺口需要用户决策。
- 审阅者能否从故事线追溯到对应设计和验收证据。

### 验收证据

- `docs/user-stories/agenticops-user-stories.md` 能说明故事线分类和推进门禁。
- `docs/README.md` 和 `docs/review-checklist.md` 能指向故事线、设计和计划入口。
- `git diff --check` 无 Markdown 空白错误。
- 文档检查未发现故事线中的 checkbox 或 Implementation note。

### 关联设计

- `docs/project-rules.md`
- `docs/architecture/agenticops-current-design.md`
- `docs/review-checklist.md`
- `docs/README.md`

## 3. PM-002 维护操作契约、标准流程和工作流配置

作为项目维护者，
我希望能维护 AgenticOps 的操作契约、标准流程注册处和工作流配置，
以便 AIAgent 面向稳定标准工作，而不是直接猜测 Jira、GitHub 或 Git 的底层事实。

### 触发方式

```text
新增或调整一个受控操作。
新增或调整一个任务分类和标准流程。
适配一个项目的 Jira 工作流配置。
```

### 前置条件

- 已确认对应故事线和设计边界。
- 已明确标准字段、阶段、动作、失败码和人工确认点。
- 已明确具体项目 Jira 字段、状态、`transition` 和代码仓库映射来源。

### 主流程

1. 维护者更新操作契约。
2. 维护者更新标准流程注册处。
3. 维护者更新或新增 workflow profile。
4. 维护者运行契约和配置校验。
5. 维护者补充对应测试和示例输出。
6. 维护者更新相关文档入口。

### 输出

```json
{
  "ok": true,
  "operation": "maintain_standard_assets",
  "validated_assets": [
    "operation_contract",
    "standard_process_registry",
    "workflow_profile"
  ],
  "next_action": "run_e2e"
}
```

### 失败处理

- 未知 Jira 状态、缺失字段映射或缺失标准流程时，返回稳定缺口。
- 名称相同但含义冲突的 `transition` 不能自动裁决，必须提示用户决策。
- 缺少测试覆盖时，不能把配置描述为可正式使用。

### 验收标准

- AIAgent 不需要直接理解 Jira 自定义字段和工作流状态。
- CLI 能校验操作契约、标准流程和 workflow profile。
- 缺失映射能输出稳定错误码、缺口说明和所需人工动作。

### 保护行为

- AIAgent 不能直接猜测 Jira 字段、状态、`transition`、目标仓库或标准流程。
- 未知 Jira 状态、缺失字段映射、缺失任务分类或缺失标准流程必须输出稳定缺口。
- 操作契约必须声明输入、输出、前置门禁、失败码、副作用和人工确认要求。
- workflow profile 必须承载具体项目 Jira / GitHub / 本地路径映射。

### 审核问题

- 新增或调整的能力是否已有明确操作契约。
- 标准流程注册处是否能解释任务分类、阶段、责任角色和完成清理。
- workflow profile 是否避免把具体项目事实写死到通用规则。
- 缺失映射时是否有稳定错误码和人工动作。

### 验收证据

- 操作契约校验输出。
- 标准流程注册处校验输出。
- workflow profile 校验输出。
- 缺失字段、未知状态或缺失映射的结构化失败输出。

### 关联设计

- `docs/contracts/operation-contract.md`
- `docs/processes/standard-process-registry.md`
- `docs/profiles/workflow-profile.md`
- `docs/forms/task-form-standard.md`
- `docs/architecture/full-design-implementation-design.md`

## 4. PM-003 发布 AgenticOps 版本和安装资产

作为项目维护者，
我希望能受控发布 `agentic-cli`、标准资产、安装脚本和版本清单，
以便研发负责人能通过稳定安装入口获得可验证版本。

### 触发方式

```sh
bash scripts/release.sh
bash scripts/test-build-release.sh
bash scripts/publish-release.sh <release_dir>
```

### 前置条件

- 设计、契约、运行资产、测试和文档已经同步。
- 发布内容不包含 secrets、tokens、private keys 或原始敏感日志。
- 发布权限、人工确认和审计要求已经满足。

### 主流程

1. 维护者构建当前平台或多平台 release 产物。
2. 维护者生成版本清单、校验和和安装资产。
3. 维护者运行本地发布安装闭环验证。
4. 维护者在人工确认后发布到 GitHub Release。
5. 维护者记录发布审计信息。

### 输出

```json
{
  "ok": true,
  "operation": "publish_release",
  "artifact": "agentic-cli",
  "next_action": "verify_install"
}
```

### 失败处理

- 构建失败时停止发布。
- 校验和不匹配时停止安装或发布。
- 权限不足时返回 `missing_permission`。
- 发布后发现版本不可用时进入回滚或重新发布流程。

### 验收标准

- release 产物、版本清单和校验和一致。
- 安装脚本能安装发布后的 `agentic-cli` 和运行资产。
- 发布动作受人工确认和审计约束。

### 保护行为

- 发布必须产出可验证的 `agentic-cli` 二进制、标准资产、版本清单和校验和。
- 安装入口必须安装到 `~/.agentic-ops`，不能把具体项目运行资料写入全局安装目录。
- 发布动作必须受权限、策略、人工确认和审计记录约束。
- 失败或不可用版本必须能进入受控回滚或重新发布流程。

### 审核问题

- release 产物、版本号、清单和校验和是否一致。
- 安装脚本是否只处理全局安装和通用运行资产。
- 发布过程是否需要人工确认，以及确认记录写在哪里。
- 发布后如何证明安装后的 `agentic-cli` 可运行。

### 验收证据

- `bash scripts/test-build-release.sh`
- `bash tests/e2e/local-release-install-flow.sh`
- release directory 中的版本清单和校验和。
- 发布或安装审计记录。

### 关联设计

- `docs/runtime/versioning.md`
- `docs/runtime/cli-runtime.md`
- `docs/runtime/problem-resolution-and-update.md`
- `scripts/release.sh`
- `scripts/init.sh`
- `scripts/publish-release.sh`

## 5. PM-004 诊断问题并选择修复载体

作为项目维护者，
我希望能按问题类型选择正确修复载体，
以便避免把所有问题都升级为二进制修复或临时人工绕过。

### 触发方式

```sh
agentic-cli doctor --workspace <name>
agentic-cli feedback bundle --workspace <name> --run-id <run_id> --redact
agentic-cli update check
agentic-cli profile validate --workspace <name>
agentic-cli policy validate --workspace <name>
```

### 前置条件

- 已有失败码、事件日志、诊断包或复现步骤。
- 诊断数据已经脱敏。
- 已明确问题影响的是 CLI 逻辑、工作流配置、任务字段、策略门禁还是发布资产。

### 主流程

1. 维护者收集脱敏诊断包。
2. 维护者按失败码和问题分类定位修复载体。
3. CLI 逻辑错误进入版本修复。
4. Jira 流程状态不适配进入 workflow profile 更新。
5. Jira 卡片属性缺失进入补卡模板和阻断说明。
6. 关键步骤门禁调整进入 policy 更新。
7. 发布或安装问题进入 update、release 或 rollback 流程。

### 输出

```json
{
  "ok": true,
  "operation": "classify_problem",
  "problem_type": "workflow_profile_mismatch",
  "repair_carrier": "profile_update",
  "next_action": "prepare_profile_change"
}
```

### 失败处理

- 诊断包疑似包含敏感内容时停止分析并要求脱敏。
- 问题分类不明确时，先补充事实，不直接改设计或代码。
- 涉及权限、事实源或自动化程度改变时，提示用户决策。

### 验收标准

- 问题能被归入明确修复载体。
- 诊断输出不包含敏感原始内容。
- 修复路径能说明是否需要版本发布、资产热更新、补卡或人工决策。

### 保护行为

- 不把所有问题默认升级为二进制修复。
- 诊断包不得包含 secrets、tokens、private keys、原始 Jira 描述、原始敏感日志或敏感代码片段。
- 问题分类不明确时必须先补事实，不能直接修改设计、契约或代码。
- 涉及权限、事实源或自动化程度变化时必须提示用户决策。

### 审核问题

- 当前问题属于 CLI 逻辑、workflow profile、Jira 卡片属性、policy、release/update 中哪一类。
- 诊断数据是否已脱敏。
- 修复载体是否能解释为什么不是其它路径。
- 修复后是否有对应回归入口。

### 验收证据

- `agentic-cli doctor --workspace <name>` 的结构化输出。
- `agentic-cli feedback bundle --workspace <name> --run-id <run_id> --redact` 生成的脱敏包。
- `bash tests/e2e/problem-resolution-flow.sh`
- 失败码、问题分类和建议修复载体的输出记录。

### 关联设计

- `docs/runtime/problem-resolution-and-update.md`
- `assets/runbooks/problem-resolution.md`
- `docs/workflows/feedback-loop.md`
- `docs/templates/evidence-templates.md`

## 6. PM-005 处理反馈并形成改进建议

作为项目维护者，
我希望能从任务执行记录中分析重复失败、阻塞点和人工确认点，
以便把有效经验沉淀为 AgenticOps 改进建议。

### 触发方式

```sh
agentic-cli feedback report --workspace <name> --date 2026-07-23
agentic-cli feedback analyze --workspace <name> --date 2026-07-23
agentic-cli feedback propose --workspace <name> --date 2026-07-23
```

### 前置条件

- 工作空间已有任务级事件日志、证据或审计记录。
- 反馈数据已经脱敏。
- 改进建议不会未经人工确认直接修改源头仓库。

### 主流程

1. 维护者生成反馈报告。
2. 维护者聚合失败码、阻塞原因和人工确认点。
3. 维护者形成 observation。
4. 维护者把可行动改进转成 proposal。
5. 用户确认后，proposal 才能进入设计、计划或实现变更。

### 输出

```json
{
  "ok": true,
  "operation": "feedback_propose",
  "proposals": 3,
  "next_action": "owner_review"
}
```

### 失败处理

- 缺少事件日志时提示检查工作空间配置。
- 发现敏感内容时停止生成报告。
- 重复失败只能形成 proposal，不能自动修改公司规范。

### 验收标准

- 能按工作空间、时间范围、失败码或任务类型生成反馈报告。
- 报告包含成功、失败、阻塞、人工确认点和重复问题。
- 改进建议经过人工确认后才进入 AgenticOps 源头仓库。

### 保护行为

- 反馈报告是按需分析工具，不替代任务级审计记录。
- 重复失败只能形成 proposal，不能自动修改 AgenticOps 源头规则。
- 报告和建议不得包含 secrets 或敏感原始内容。
- proposal 进入设计、计划或实现前必须经过人工确认。

### 审核问题

- 报告输入来自哪些事件日志、证据或任务审计记录。
- 输出是否区分 observation、proposal 和 accepted change。
- 是否把“按需分析”误写成每个任务完成后的强制日报。
- 改进建议是否明确影响故事线、设计、契约、配置、策略或代码。

### 验收证据

- `agentic-cli feedback report --workspace <name> --date <date>` 输出。
- `agentic-cli feedback analyze --workspace <name> --date <date>` 输出。
- `agentic-cli feedback propose --workspace <name> --date <date>` 输出。
- 人工确认 proposal 的记录。

### 关联设计

- `docs/workflows/feedback-loop.md`
- `docs/runtime/problem-resolution-and-update.md`
- `docs/templates/evidence-templates.md`
- `docs/project-rules.md`

## 7. PM-006 治理发布权限、回滚和兼容性

作为项目维护者，
我希望 AgenticOps 的发布、回滚和兼容性有明确治理边界，
以便研发负责人能安全升级，AIAgent 不会在不兼容资产上继续执行高风险操作。

### 触发方式

```sh
agentic-cli update check
agentic-cli update apply
agentic-cli update rollback
```

### 前置条件

- 已确认 latest-only 支持策略。
- 已有版本清单、校验和和本地当前版本记录。
- 发布权限、回滚权限和审计记录要求明确。

### 主流程

1. CLI 检查当前版本和远程版本。
2. CLI 判断更新严重程度和受影响操作。
3. 必要更新只阻断受影响操作。
4. 应用更新前校验产物。
5. 更新失败时回滚到上一个可用版本。
6. 维护者记录发布、更新或回滚审计信息。

### 输出

```json
{
  "ok": true,
  "operation": "update_apply",
  "previous_version": "RES-v0.1.3-a68372d",
  "current_version": "RES-v0.1.4-b7c29e1",
  "next_action": "run_preflight"
}
```

### 失败处理

- 版本清单不可达时提示网络或权限问题。
- 产物校验失败时拒绝切换。
- 更新后 `preflight` 失败时进入 rollback。
- 跨版本兼容最低承诺不明确时，必须提示用户决策。

### 验收标准

- 更新、发布和回滚都有结构化审计记录。
- 不兼容版本不会继续执行受影响的高风险操作。
- latest-only 支持策略不会被误读为维护旧版本补丁线。

### 保护行为

- AgenticOps 使用 latest-only 支持策略，不维护旧版本补丁线。
- 更新前必须校验版本清单和产物校验和。
- 必要更新只能阻断受影响操作，不能无差别阻断所有工作。
- 更新失败或新版本不可用时必须能回滚到上一个可用版本。

### 审核问题

- 当前版本和目标版本如何识别。
- 哪些操作会被必要更新阻断，阻断理由是什么。
- 回滚需要哪些本地记录和审计信息。
- 跨版本兼容最低承诺是否已经由用户决策。

### 验收证据

- `agentic-cli update check` 输出。
- `agentic-cli update apply` 输出。
- `agentic-cli update rollback` 输出。
- 发布、更新或回滚的结构化审计记录。

### 关联设计

- `docs/runtime/versioning.md`
- `docs/runtime/problem-resolution-and-update.md`
- `docs/architecture/full-design-implementation-design.md`
- `docs/development-phase-rules.md`
