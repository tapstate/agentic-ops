# DE-004 任务接管、接纳与恢复留痕

> **现役故事合同。** 用户只表达“接管 <KEY>”，正式入口为 `ao-work takeover <KEY>`。Runtime 自动绑定当前 run 的内部授权摘要并判断新接管、接纳存量或恢复，不要求用户选择模式或确认内部标识。

作为研发工程师，
我希望只说“接管某任务”就让 AIAgent 完成受控接管并开始分析，
以便任务立即进入可见、可审计的处理中状态，再只在设计审查、代码审查或风险决策时暂停。

## 触发方式

自然语言：

```text
接管 TAP-123。
```

当前 Runtime 原子入口：

```sh
ao-work takeover TAP-123
```

`INTERNAL_REFERENCE` 由 AIAgent 绑定用户明确的接管指令，研发工程师不查看、复制或确认该内部参数。未提供任务编号时，Runtime 只读列出候选，由研发工程师选择后再执行正式接管。

## 角色与事实源

- Jira `Assignee`：当前任务负责人。
- Jira `Status`：团队可见阶段。
- Jira 受管 Comment：接管、恢复和后续执行轨迹。
- 本地 task state：运行编号、恢复点和幂等状态，不替代 Jira。
- 安装身份 `agent_id`：AIAgent 稳定身份，不替代 Jira 负责人。

developer 不创建、映射、探测或读写 Agentic Jira Custom Field。

本地 task state 将业务 `progress.stage` 与接管写入阶段分离。`sync.json.takeover_operation` 是稳定接管意图、Comment/Status 回读、结果确定性和恢复动作的权威快照；`task inspect`、`task resume` 与正式接管必须使用同一个恢复读取器。

## 接管前置条件

- issue 属于当前业务工作空间允许的项目。
- 当前 Jira 用户等于任务 `Assignee`。
- Jira Status 和必要 transition 能由 Project Profile 严格映射。
- 当前工作空间存在可用 Agent 身份。
- 本地 run 和已回读受管 Comment 不存在已知身份或运行冲突。
- 研发工程师已经明确表达“接管 <KEY>”。

`task_class`、`process_id`、`target_repo`、`target_branch` 和 `verification_method` 在接管后由信息分析阶段按 Jira、Project Profile、源码和 Runtime 证据补全；它们不再阻止初始接管，但缺失或冲突会阻止进入实现。

## 主流程

1. AIAgent 根据用户指令调用统一接管原子能力。
2. Runtime 校验项目、负责人、状态/transition、Agent 身份和本地恢复事实。
3. Runtime 自动分类：
   - `new_takeover`
   - `accept_existing_task`
   - `resume_takeover`
4. Runtime 先写并回读结构化中文接管 Comment；非新接管同时在人可见输出与 Comment 中明文提示“不是新接管”。
5. 如 Jira 尚未在目标执行状态，Runtime 执行严格映射的 transition 并回读 Status。
6. Runtime 写入或复用本地 run，输出 `takeover_status=completed`、运行编号、Comment ID、Status 前后值和结构化下一动作。
7. AIAgent 连续执行信息分析、带来源补全和方案分级，不设置准入摘要确认或通用方案摘要确认。
8. 事实完整后展示可查阅的设计、范围、验证方式和逐项风险，进入设计审查。
9. 设计确认后在授权范围内连续实现、验证和整理证据。
10. 功能/修复/任务分支停在 PR 当前 Head 审查；`develop` 等其它允许分支停在未推送本地 commit 审查。

## 模式语义

| 结果 | 判定 | 用户提示与审计 |
| --- | --- | --- |
| `new_takeover` | 没有可恢复本地运行，Jira 尚未进入目标执行状态 | 明文“新接管”，写 Comment，必要时流转 Status |
| `accept_existing_task` | 没有可恢复本地运行，Jira 已在执行状态且无可见冲突 | 明文“不是新接管”，写 Comment，不重复流转 |
| `resume_takeover` | 当前工作空间存在同任务、同运行的可验证恢复点 | 明文“不是新接管”，复用 run 并写恢复 Comment |
| `blocked` | 所有权、身份、状态映射、运行或外部事实冲突 | 失败结果，不得作为 `takeover_kind`，停止并给出人工动作 |

## 本地写入阶段

```text
intent_persisted
-> comment_verified
-> status_verified
-> local_finalized
```

操作结果独立使用 `in_progress`、`uncertain`、`blocked`、`completed`。Comment 已确认但 Status 未确认、外部响应不确定或 Jira 已完成但本地落盘失败，都不得使用新的业务 stage 表达，也不得返回 `takeover_status=completed`。只有 `local_finalized/completed` 且快照、`progress.json` 和事件交叉一致时，业务阶段才进入 `takeover_started`。

## 成功输出

```json
{
  "ok": true,
  "operation": "task_takeover",
  "status": "completed",
  "takeover_status": "completed",
  "workspace": "tapstate",
  "issue_key": "TAP-123",
  "agentic_run_id": "run-TAP-123-example",
  "agent_id": "developer-agent",
  "takeover_kind": "new_takeover",
  "human_notice": "已完成新接管。",
  "takeover_comment_id": "12345",
  "takeover_comment_verified": true,
  "takeover_phase": "local_finalized",
  "takeover_result": "completed",
  "external_result_certainty": "verified",
  "retry_safe": true,
  "recovery_action": "none",
  "intake_source": {
    "context_digest": "<sha256>",
    "source_context_path": "<workspace-managed-path>"
  },
  "jira_status_before": "待办",
  "jira_status_after": "正在进行",
  "current_stage": "takeover_started",
  "agentic_next_action": {
    "executor": "ai",
    "action": "analyze_task",
    "required_inputs": [],
    "allowed_operations": ["report_write"],
    "requires_authorization": false,
    "stop_workflow": false,
    "ownership_effect": "none",
    "retry_gate": {"allowed": false}
  }
}
```

## 人工门禁

- **设计审查**：完整设计、范围、验证和风险已经形成。
- **代码审查**：绑定 PR 当前 Head 或未推送本地 commit。
- **风险决策**：所有权、范围、仓库、分支、验证、外部事实或写入结果不明确。
- 合并、发布、Git Tag、强推、历史改写和直接修改保护分支继续单独确认。

普通信息分析、证据化补全、方案分级、实现和验证在有效授权范围内连续推进，不重复请求确认。用户只审查可查阅事实，不确认 `impact_id`、digest、plan ID 或授权参数。

## 失败处理

- 负责人不匹配、项目越界或 Agent 身份冲突时停止，不写开发证据。
- Status/transition 未严格映射时在任何接管 Comment 写入前阻断。
- 非新接管存在外来 Agent、运行或受管 Comment 冲突时进入风险决策，不能自动覆盖。
- Jira 写入或回读结果不确定时停止并优先回读；AO-50 的本地状态机保存确定性和恢复动作，AO-49 负责把 Jira Saga 接入该状态机。
- 每个环节只按 Runtime 的结构化下一动作推进；未允许重试或重试耗尽时停止转人工。

### 验收标准

- 用户只需表达“接管 <KEY>”，不选择 new/resume/adopt 子命令。
- 成功 `takeover_kind` 只有三种，`blocked` 只作为失败结果。
- 非新接管在终端输出、结构化字段和 Jira Comment 中均明文提示“不是新接管”。
- 接管成功后 Jira Comment、必要 Status transition 和本地 run 均已回读或验证。
- Comment 已写/Status 未写、外部结果不确定和 Jira 已完成/本地未完成均能输出确定的本地 phase、结果、`retry_safe` 与恢复动作，不误报成功。
- legacy schema v1 只有在 Comment 作者/标记、运行编号、负责人和 Status 全部验证一致后才能迁移，失败不覆盖原状态。
- 接管后信息分析自动推进，只在设计审查、代码审查和风险决策暂停。
- developer 接管不依赖 Agentic Jira Custom Field。
- 未经设计授权不得修改代码；未经代码审查不得推送 `develop` 或继续受保护动作。

### 保护行为

- 单次正式接管只处理一个 Jira 卡片。
- 所有权和状态映射不能为演示或自动化便利而放宽。
- 相同恢复事实必须复用已有 `agentic_run_id`。
- 人工确认必须绑定可查阅设计、commit 或 PR 事实；内部摘要不能替代审查对象。
- 当前不声称具备跨工作空间并发锁；出现真实并发需求后专题设计。

### 验收证据

- `ao-work capability show takeover_task` 与当前 Runtime 接管输出。
- 三种成功模式、阻断和无编号候选的 Fake Jira 测试。
- 同一 `agentic_run_id` 的本地事件与受管 Jira Comment 回读。
- 真实 Jira 卡片的 Comment、Status、Assignee 和本地状态证据。
- 故事门禁固定验收和代码审查引用。

## 关联设计

- `docs/architecture/developer-task-takeover-comment-design.md`
- `docs/architecture/developer-takeover-local-state-machine.md`
- `docs/contracts/operation-contract.md`
- `docs/processes/standard-process-registry.md`
- `docs/forms/task-form-standard.md`
- `docs/architecture/resume-takeover-recovery-gate-design.md`
