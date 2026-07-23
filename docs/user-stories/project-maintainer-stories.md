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
