# DE-004 任务接管、接纳与恢复留痕

> **现役故事合同。** 执行前仍以 `ao-work capability list|show` 为准；当前 `takeover_task` 已由 `ao-work task takeover` 实现。

作为研发工程师，
我希望只说“接管某任务”就让 AIAgent 自动判断是新接管、接纳存量任务还是恢复既有运行，
以便 AI 员工在完成门禁后开始读取上下文、制定计划、开发、验证并回写证据。

### 触发方式

```sh
ao-work task takeover TAP-123 --authorization-reference <AUTHORIZATION_REFERENCE>
```

或自然语言：

```text
接管 TAP-123。
```

自然语言入口不得要求用户选择额外子命令；AIAgent 必须调用同一个 `task takeover` 原子能力。未提供任务编号时，该命令只读列出候选，由研发工程师确认目标后再带编号执行。

现役另有一个低参数的接管前本地准备入口：

```sh
ao-work task start TAP-123
```

它从当前工作空间、Project Profile 和 Jira 卡片读取 Issue ID、Project、经办人、状态、标题、描述与任务类型，生成或恢复本地 `agentic_run_id`。之后由现役 `ao-work task intake assess|confirm` 校验带来源的 Jira、Profile、源码与 Runtime 补全信息，并展示完整准入摘要供用户确认，不能只给一个 ID；来源或 HEAD 变化会使旧确认失效。准入确认后由 `ao-work task solution classify|confirm` 按固定优先级分流：L1 直接进入下一门禁、L2 确认后进入下一门禁、L3 修改设计并重新评估、L4 停止升级。必要信息未补齐时只允许改变输入后重试一次。以上准备命令不写 Jira、不改变状态；正式接管必须调用 `ao-work task takeover`。

现役命令为：

```sh
ao-work task intake assess --issue-key TAP-123 --agentic-run-id <RUN> --input-file <准入分析.json>
ao-work task intake confirm --issue-key TAP-123 --agentic-run-id <RUN> --confirm-intake-digest <DIGEST> --confirmed-by <NAME> --authorization-reference user-confirmation:TAP-123:<RUN>:<DIGEST>
ao-work task solution classify --issue-key TAP-123 --agentic-run-id <RUN> --input-file <方案.json>
ao-work task solution confirm --issue-key TAP-123 --agentic-run-id <RUN> --confirm-solution-digest <DIGEST> --confirmed-by <NAME> --authorization-reference user-confirmation:TAP-123:<RUN>:<DIGEST>
```

从正式接管到 PR 审查由同一 `task_owner` 持续负责。`agentic_next_action.executor` 只表示当前步骤由 Runtime、当前 AI、人、reviewer 或项目工具执行，不代表 Jira 经办人或任务所有权变更。当前 `ownership_effect` 只允许 `none`；`task_transfer` 保留为 `capability_gap`，如需转派必须停止并由人决定，详细合同后续单独设计。

### 前置条件

- AIAgent 能力已初始化。
- Jira 卡片已进入迭代。
- 当前 Jira 用户和卡片负责人匹配。
- AIAgent 已通过 `inspect-task` 读取 Jira 事实和项目资产。
- AIAgent 已按项目准入资产确认卡片满足接管要求。
- 正式接管必须验证当前工作空间 Jira 账户是卡片经办人，并提供稳定授权引用。

### 目标主流程

1. AIAgent 调用已实现的 `inspect-task`，按项目资产判断准入；能力缺失时返回 `capability_gap`。
2. 准入不足时，AIAgent 结合 Jira 和代码形成补卡建议，写入 Jira 后结束本次接管；补卡确认后更新 Description 并再次结束。
3. 后续执行重新调用已实现的 `inspect-task`。准入通过后，AIAgent 调用已实现的 `takeover_task`；任一能力未实现都必须停止并报告 `capability_gap`。
4. CLI 校验 `Assignee`、状态映射、transition 与授权引用，生成或复用本地 `agentic_run_id`。
5. CLI 自动分类 `new_takeover`、`accept_existing_task` 或 `resume_takeover`，先写并回读受管中文接管 Comment；后两类必须明文包含“不是新接管”。
6. 状态不在执行阶段时，CLI 再执行项目映射的 transition 并回读确认。developer 工作面不读写 Agentic Jira Custom Field。
7. AIAgent 读取目标仓库上下文，形成版本化修复计划并写入 Jira Comment。
8. 研发工程师确认计划，AIAgent 把确认结果写入 Jira Comment。
9. 确认结果写入后，AIAgent 才能在允许范围内修改代码并运行验证。
10. AIAgent 写入最终证据 Comment，并按分支规则停在提交或 PR 审查门禁。

### 目标输出

```json
{
  "ok": true,
  "operation": "takeover_task",
  "workspace": "tapstate",
  "issue_key": "TAP-123",
  "agentic_run_id": "TAP-123-takeover-20260721103012-a8f3",
  "takeover_kind": "new_takeover",
  "takeover_comment_id": "12345",
  "takeover_comment_verified": true,
  "task_type": "task_takeover",
  "current_stage": "takeover_started",
  "target_repo": "tapstate/example-repo",
  "agentic_next_action": {
    "executor": "ai",
    "action": "analyze_and_complete_task_information",
    "required_inputs": ["issue", "workspace_defaults", "agentic_run_id", "intake_gate", "solution_gate"],
    "allowed_operations": ["report_write"],
    "requires_authorization": false,
    "stop_workflow": false,
    "ownership_effect": "none",
    "retry_gate": {"allowed": false}
  }
}
```

### 失败处理

- 负责人不匹配时，停止，不写开发证据。
- 项目准入信息不足时，不调用 `takeover-task`，先完成代码分析和 Jira 补卡闭环。
- 权限不足时，返回 `missing_permission`。
- 风险边界不清时，要求人工确认。
- 每个环节只按 Runtime 根据实际结果返回的结构化下一动作推进；只在 `retry_gate.allowed=true` 时可回读、改变输入后重试一次，耗尽后停止。

### 验收标准

- 单次任务接管只处理一个 Jira 卡片。
- 接管成功和失败都有结构化记录。
- 每次接管都有唯一 `agentic_run_id`。
- AIAgent 未经确认不得推送或创建拉取请求。
- 所有操作都写入结构化事件日志。
- 写入 Jira 的接管成功、失败、阻塞和补卡说明必须使用中文。

### 保护行为

- 单次接管只能处理一个 Jira 卡片。
- CLI 接管必须检查负责人、状态映射、transition、授权引用和真实 Jira 写入权限；项目准入由 AIAgent 按项目资产判断。
- 修复计划和研发工程师确认结果未写入 Jira 前，不得修改代码。
- 接管成功必须生成唯一 `agentic_run_id` 并写入结构化事件日志。
- 接管失败必须写结构化失败记录，不能继续开发。
- 未经研发工程师独立确认或授予仍有效的工作项级连续执行授权，AIAgent 不得推送或创建拉取请求。确认版本化设计或修复计划后，授权范围内可以连续推进到拉取请求审查；合并、发布、范围变化和授权失效仍需新的人工确认。

### 审核问题

- 当前 Jira 卡片是否已进入迭代并由当前研发工程师负责。
- 卡片是否已由 AIAgent 按项目资产完成准入检查。
- 准入分析、补卡确认、修复计划和计划确认是否分别写入 Jira。
- `target_repo` 是来自字段映射还是 workflow profile。
- 接管失败时是否清楚提示 required human action。
- 接管后是否停在正确的下一步，而不是绕过人工门禁。

### 验收证据

- `ao-work task start <KEY>` 的 Jira 只读解析、负责人/完成状态阻断和本地 run 幂等恢复测试。
- `ao-work capability show takeover_task` 与 `ao-work task takeover <KEY>` 的正式接管输出。
- `agentic_run_id` 对应的事件日志。
- Jira 中文接管成功、失败、阻塞或补卡说明。
- `./maintainer/bin/ao-maint integration run-offline <issue> --manifest <path>` 的离线烟测结果；它不证明正式接管。
- 真实 Jira 卡片端到端演示记录。

### 关联设计

- `docs/contracts/operation-contract.md`
- `docs/processes/standard-process-registry.md`
- `docs/templates/evidence-templates.md`
- `docs/examples/end-to-end-demo.md`
- `docs/forms/task-form-standard.md`
