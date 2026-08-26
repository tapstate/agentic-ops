# developer 接管本地状态机设计

## 1. 目标与边界

本设计对应 AO-50，在 Jira 继续作为任务事实源的前提下，为 developer 工作面的统一接管操作建立可恢复、可解释、可审计的本地状态机。

本工作项只实现本地状态、Schema、事件、迁移和统一读取接口，不实现 Jira 网络调用、Comment/Status Saga 或并发锁。AO-49 必须调用这里定义的状态服务，不得自行维护第二套接管恢复状态。

## 2. 现状问题

当前正式接管完成 Jira Comment 和 Status 回读后，直接把 `progress.json.stage` 写为 `takeover_started`，并在 `journal.ndjson` 追加一条 `takeover_task` 事件。该模型存在以下缺口：

- 第一次外部写入前没有稳定接管意图，恢复时可能重新生成运行编号、接管类型或 Comment 标记。
- `progress.json` 只能表达业务阶段，无法区分 Comment 已确认、Status 未确认、外部结果不确定或本地收口失败。
- `sync.json` 没有接管操作的统一快照，CLI、`task inspect` 和 `task resume` 只能从不同文件临时推断。
- schema v1 的成功事件不能直接证明当前 Jira Comment 作者、标记、负责人和 Status 仍然一致。

## 3. 状态归属

继续保留现有五个受管文件，不新增独立任务看板或计划文件：

| 文件 | 责任 |
| --- | --- |
| `task.json` | Jira 任务与 `agentic_run_id` 的稳定身份 |
| `progress.json` | 业务阶段；接管仅在本地最终收口后进入 `takeover_started` |
| `sync.json` | 接管意图、外部写入阶段、结果确定性和恢复动作的权威快照 |
| `journal.ndjson` | 只追加的接管状态事件 |
| `decisions.ndjson` | 人工授权与风险决策，不保存网络阶段 |

现有文件的顶层 `schema_version` 保持 `"1"`，避免把 AO-50 扩大为所有任务状态的全局格式迁移。`sync.json.takeover_operation` 使用独立 `schema_version: 2`；缺少该对象的旧接管记录视为 legacy schema v1，需要受控验证后才能合成 v2 检查点。

## 4. 接管操作快照

`sync.json.takeover_operation` 是单一权威快照，至少包含：

```json
{
  "schema_version": 2,
  "operation_id": "takeover-<stable-digest>",
  "issue_key": "TAP-123",
  "agentic_run_id": "run-TAP-123-abc123",
  "agent_id": "developer-agent",
  "takeover_kind": "new_takeover",
  "authorization_digest": "<sha256>",
  "preflight_facts_sha256": "<sha256>",
  "jira_status_before": "待办",
  "jira_status_target": "正在进行",
  "transition_id": "31",
  "comment_marker": "[agentic-ops-takeover:...]",
  "comment_content_sha256": "<sha256>",
  "comment_id": null,
  "status_after": null,
  "phase": "intent_persisted",
  "result": "in_progress",
  "external_result_certainty": "not_attempted",
  "takeover_status": "in_progress",
  "human_notice": "正在执行新接管。",
  "agentic_next_action": {
    "executor": "ao_work",
    "action": "ensure_takeover_comment",
    "operation_id": "takeover_task",
    "command_argv": ["takeover", "TAP-123"],
    "command_line": "ao-work takeover TAP-123",
    "bound_arguments": {"issue_key": "TAP-123"},
    "required_inputs": [],
    "input_artifacts": [],
    "allowed_operations": ["takeover_task"]
  },
  "failure_code": null,
  "retry_safe": true,
  "recovery_action": "ensure_takeover_comment",
  "planned_at": "2026-08-21T00:00:00Z",
  "updated_at": "2026-08-21T00:00:00Z",
  "content_version": 1
}
```

约束如下：

- `operation_id`、`agentic_run_id`、`takeover_kind`、授权摘要、Comment 标记和目标 Status 在意图创建后不可变。
- 只保存授权摘要，不保存聊天原文、Token 或其它秘密。
- `takeover_kind` 只允许 `new_takeover`、`accept_existing_task`、`resume_takeover`；`blocked` 是结果，不是分类。
- `phase`、`result`、`external_result_certainty` 和下一动作必须由同一 Schema 联合校验，不能任意组合。
- 报告中的本地路径只有在 Runtime 已创建文件、完成受管路径校验并成功回读后才允许输出。

## 5. 两层状态机

### 5.1 外部写入阶段

固定阶段只有：

```text
intent_persisted
-> comment_verified
-> status_verified
-> local_finalized
```

- `intent_persisted`：稳定意图已经原子落盘，尚未确认外部写入。
- `comment_verified`：Comment ID、作者、稳定标记和内容摘要均已回读一致。
- `status_verified`：Jira Status 已可靠回读为目标值；不需要 transition 的存量任务也必须经过此检查点。
- `local_finalized`：`progress.json`、`sync.json` 和事件交叉校验完成，业务阶段可以对外呈现为 `takeover_started`。

阶段只允许单向前进。恢复可以补写已由外部事实证明的检查点，但不得回退或改变原意图。

### 5.2 操作结果

结果与业务阶段分离：

- `in_progress`：可由 Runtime 按下一动作继续。
- `uncertain`：外部响应或回读结果不确定，禁止盲目重试副作用。
- `blocked`：事实冲突、Schema 冲突或迁移证据不足，需要人工处理。
- `completed`：仅允许与 `phase=local_finalized` 同时出现。

`progress.json.stage` 不得用来表达 `uncertain` 或接管写入阶段。任一部分完成状态都不能返回 `takeover_status=completed`。

## 6. 事件与状态转换

状态服务只允许写入以下事件：

| 事件 | 前置条件 | 结果 |
| --- | --- | --- |
| `takeover_intent_created` | 当前没有意图，或相同意图幂等重放 | 进入 `intent_persisted` |
| `takeover_comment_verified` | 当前至少为 `intent_persisted`，Comment 证据完整 | 进入 `comment_verified` |
| `takeover_status_verified` | 当前至少为 `comment_verified`，Status 证据完整 | 进入 `status_verified` |
| `takeover_recovered` | 原意图不变，外部回读证明可补检查点 | 更新到被证明的阶段，并记录恢复来源 |
| `takeover_completed` | Comment、Status、业务阶段和快照一致 | 进入 `local_finalized/completed` |
| `takeover_blocked` | 事实冲突、Schema 无效或迁移失败 | 阶段保持不变，结果进入 `blocked` |

每个事件必须包含 `operation_id`、`issue_key`、`agentic_run_id`、事件前后阶段、结果、失败码、`retry_safe`、证据摘要和时间。事件写入前后都执行 Schema 校验。

本地最终收口在任务锁内执行。由于多个文件不能形成操作系统级跨文件事务，读取接口必须把 `sync.json.takeover_operation`、`progress.json` 和最近事件作为一个逻辑事务交叉校验：中断形成的部分本地写入只返回可恢复状态，不得报告成功；恢复完成后追加 `takeover_recovered`，再以 `takeover_completed` 收口。

## 7. 统一服务接口

在 `ao_work.task_state` 内提供唯一接管状态服务，AO-49、正式 takeover、`task inspect` 和 `task resume` 共同使用：

- `persist_takeover_intent(...)`：校验并持久化稳定意图；相同意图幂等，不同意图失败关闭。
- `verify_takeover_comment(...)`：保存已回读的 Comment ID、作者验证和内容摘要。
- `verify_takeover_status(...)`：保存 Status 前后值、transition 证据和确定性。
- `mark_takeover_uncertain(...)`：保存不确定外部结果、失败码、`retry_safe=false` 和恢复动作。
- `block_takeover(...)`：保存冲突事实，不改业务阶段。
- `finalize_takeover(...)`：仅在 Comment 和 Status 都验证后收口，并推进 `progress.stage`。
- `read_takeover_recovery(...)`：纯读取并联合校验快照、业务阶段和事件，所有消费者返回相同恢复事实。
- `migrate_legacy_takeover(...)`：受控合成 legacy v1 检查点。

所有方法都在 `TaskStore` 的任务级锁和受管路径校验内运行，不允许调用方直接拼接或覆盖状态文件。

## 8. legacy schema v1 迁移

旧状态没有 `sync.json.takeover_operation` 时不得仅凭 `progress.stage=takeover_started` 自动升级。迁移输入必须包含 AO-49 已可靠回读的以下证据：

- `issue_key` 与 `agentic_run_id` 和 `task.json` 一致。
- 最近的旧 `takeover_task` 成功事件属于同一运行。
- 受管 Comment 的 ID、作者、稳定标记和内容摘要一致。
- Jira 当前 Assignee 等于工作空间绑定账户。
- Jira 当前 Status 等于原目标 Status，且映射仍有效。

全部一致时，在不改变运行编号、模式、授权摘要和 Comment 标记的前提下，合成 `phase=local_finalized/result=completed` 的 v2 快照，并追加 `takeover_recovered` 迁移事件。任一证据缺失或冲突时返回稳定失败码 `takeover_legacy_state_unverified`，不写 `sync.json`、`progress.json` 或 journal，保留原始状态供人工核对。

`task inspect` 只读输出 legacy 状态和 `migration_required=true`；`task resume` 使用同一读取器结合 Jira 回读给出恢复事实，但保持只读；正式接管由 AO-49 在外部证据齐全后调用迁移方法。

## 9. 输出与错误

成功、进行中、不确定和阻塞输出统一包含：

- `takeover_status`
- `takeover_kind`
- `human_notice`
- 结构化 `agentic_next_action`
- `phase` 与 `result`
- `failure_code` 与 `retry_safe`
- `external_result_certainty`
- `takeover_comment_id`
- `jira_status_before`、`jira_status_target`、`jira_status_after`
- `recovery_action`

失败码至少覆盖意图冲突、非法阶段转换、Comment 证据冲突、Status 证据冲突、外部结果不确定、本地收口失败、legacy 迁移证据不足和跨文件状态不一致。

非新接管的 `human_notice` 继续明文包含“不是新接管”。内部 `operation_id`、授权摘要和 Comment 稳定标记仅用于 Runtime 与审计，不要求用户复制或确认。

## 10. 验证范围

AO-50 的测试至少覆盖：

- 三种接管类型及无编号选择、风险等待的 Schema 校验。
- 四个阶段的合法前进、非法跳转、幂等重放和意图冲突。
- Comment 已写/Status 未写、外部结果不确定、Jira 已完成/本地未完成的恢复事实。
- 只有 `local_finalized/completed` 才对外呈现业务 `takeover_started`。
- legacy v1 迁移成功、证据不足失败关闭以及失败不改原文件。
- `task inspect`、`task resume` 和正式接管共用读取器并得到一致的本地恢复结论。
- 所有输出路径均来自已创建且通过安全路径校验的文件。

AO-49 在此基础上补 Jira 请求丢失、Comment/Status 回读和最终一致性 E2E；AO-48 再把同一服务暴露为顶层公开入口。
