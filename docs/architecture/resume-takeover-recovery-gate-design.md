# `resume-takeover` 完整恢复门禁设计

本文定义 `resume-takeover` 如何从已有 `agentic_run_id` 恢复可信任务上下文，重新校验 Jira 所有权、目标仓库和标准流程阶段，并在恢复阻塞时形成受控的 Jira 反馈闭环。`resume-takeover` 本身只执行读取和本地审计；Jira 评论由独立的 `add-task-comment` 操作按策略门禁和人工确认执行。

本设计只完善恢复门禁，不实现通用工作流引擎，不让恢复操作直接执行 Jira 写入，也不改变 Git、GitHub、拉取请求、合并或发布边界。

## 1. 背景

恢复设计必须持续满足以下要求：

- 真实 Jira 模式重新读取卡片并复核 `assignee` 和 `agentic_id`。
- 恢复结果带回并校验接管时确定的 `target_repo`。
- 历史事件阶段同时经过操作契约和 Standard Process Registry 校验。
- `resume-takeover` 与 `write-evidence` 使用统一的运行上下文读取逻辑。
- 恢复成功保留真正的恢复点，不把旧版 `takeover_resumed` 当作新的业务阶段。
- 任务级恢复阻塞在本地可信时生成可由独立 Jira 原子写操作提交的阻塞反馈材料。

恢复能力必须继续遵守事实源边界：Jira 是任务、负责人、状态和任务证据的事实源，本地事件只保存执行连续性和审计信息，不能覆盖 Jira 当前事实。

## 2. 目标与非目标

### 2.1 目标

- 从同一个 `agentic_run_id` 恢复可信且完整的接管上下文。
- 真实 Jira 模式下，在恢复前重新检查卡片、当前用户、代理绑定和目标仓库。
- 分别校验操作阶段和标准流程阶段，避免混用两套阶段命名空间。
- 恢复成功只通过门禁，不擅自推进业务流程。
- 为稳定失败场景提供明确错误码、中文人工处理说明和本地审计。
- 对可信的任务级阻塞生成 Jira 评论材料，并通过现有原子写操作完成受控回写。
- 统一 `resume-takeover`、`write-evidence` 和后续同类操作使用的运行上下文读取逻辑。

### 2.2 非目标

- 不实现覆盖所有操作的通用工作流恢复引擎。
- 不让 `resume-takeover` 直接或静默写入 Jira。
- 不自动重新绑定丢失的 `agentic_id`。
- 不允许同一个 `agentic_run_id` 静默切换目标仓库、任务分类或标准流程。
- 不在本次设计中建立新的通用 Jira 评论幂等系统。
- 不执行真实 Jira 写入、Git 推送、拉取请求创建、合并或发布。

## 3. 阶段语义

AgenticOps 当前存在两类阶段，恢复时必须分别处理。

### 3.1 操作阶段

操作阶段来自本地事件，例如：

- `takeover_started`
- `blocked`
- `evidence_written`
- `completed`

`resume-takeover.yaml` 的 `allowed_stages` 负责声明哪些操作阶段允许执行恢复。`takeover_resumed` 是旧版恢复审计标记，不是新的业务阶段；读取旧事件时应忽略该标记对恢复点的推进作用。

### 3.2 标准流程阶段

标准流程阶段来自 Jira 当前状态经过项目 profile 的 `status_mapping` 映射，例如：

- `waiting_takeover`
- `implementation`
- `completed`

映射结果必须存在于历史 `process_id` 对应的 Standard Process Registry。操作阶段与标准流程阶段不得直接比较。

### 3.3 恢复成功语义

恢复成功只表示门禁通过：

- 复用原有 `agentic_run_id`。
- `previous_stage` 返回最近有效操作阶段。
- `current_stage` 保持该阶段。
- `agentic_next_action` 保持该阶段原有动作。
- 额外返回当前 `standard_process_stage`。
- 写入一条 `resume_takeover` 本地审计事件，但不生成 `takeover_resumed` 业务阶段。

## 4. 组件边界

### 4.1 `RunContextReader`

新增独立的运行上下文读取组件，负责：

- 读取同一个 `agentic_run_id` 的事件。
- 从首个成功的 `takeover_task` 事件建立接管基准。
- 恢复 `workspace`、`issue_key`、`agent_id`、`agentic_id`、`task_class`、`process_id` 和 `target_repo`。
- 检测后续事件中的非空身份字段是否与接管基准冲突。
- 从会改变任务运行状态的事件中找到最近有效操作阶段和 `agentic_next_action`。
- 识别终态、待人工确认状态和旧版 `takeover_resumed` 审计事件。

恢复点按事件写入顺序选择。第一阶段认定会改变任务运行状态的操作为 `takeover_task`、`resume_takeover`、`write_evidence`、`write_pr_evidence`、`prepare_pr` 和 `release_agent`；`add_task_comment`、`update_task_form`、`update_task_description_sections` 等通用 Jira 原子写操作只记录操作审计，不覆盖任务恢复点。失败的 `resume_takeover` 尝试和旧版成功事件中的 `takeover_resumed` 也只用于审计，不覆盖之前的恢复点。这样既能保留任务真实进度，也允许在 Jira 事实修复后重新执行恢复门禁。

该组件只依赖事件模型，不读取 Jira，不决定业务门禁。`resume-takeover` 和 `write-evidence` 统一使用该组件，删除重复的上下文恢复判断。

### 4.2 `ResumeGate`

新增纯恢复门禁组件，输入包括：

- 历史运行上下文。
- 当前 Jira 快照和当前 Jira 用户。
- Jira adapter 模式。
- 当前项目 profile。
- `resume-takeover` 操作契约。
- Standard Process Registry。
- 当前 AIAgent 的 `agent_id`。

输出包括：

- 是否允许恢复。
- 稳定失败码。
- 中文说明和人工处理动作。
- 当前标准流程阶段。
- 是否需要形成 Jira 阻塞反馈。

该组件不读写文件，不调用 Jira，不写事件，便于独立单元测试。

### 4.3 CLI handler

`runResumeTakeover` 只负责：

1. 解析 `workspace` 和 `agentic_run_id`。
2. 使用 `RunContextReader` 恢复历史上下文。
3. 加载 profile、操作契约和 Standard Process Registry。
4. 通过当前 Jira adapter 读取卡片和当前用户。
5. 调用 `ResumeGate`。
6. 记录成功或失败本地事件。
7. 必要时生成安全的 Jira 阻塞评论文件。
8. 输出结构化 JSON。

## 5. 校验流程

恢复门禁按以下顺序执行，首次失败立即停止：

1. 校验 `agentic_run_id` 是否存在。
2. 校验运行上下文是否完整且 `workspace` 一致。
3. 校验同一 run 中不可变身份字段没有冲突。
4. 检查最近事件是否为终态或仍处于人工确认点。
5. 使用操作契约校验最近操作阶段是否允许恢复。
6. 读取 Jira 卡片和当前 Jira 用户。
7. 校验 Jira 卡片编号与历史 `issue_key` 一致。
8. 在真实 Jira 模式下校验当前 `assignee`。
9. 在真实 Jira 模式下校验 `agentic_id`。
10. 从当前 Jira/profile 解析 `target_repo`，并与接管基准比较。
11. 校验历史 `process_id` 存在于 Standard Process Registry。
12. 校验历史 `task_class` 属于该流程。
13. 将 Jira 状态映射为标准流程阶段。
14. 校验该阶段存在于标准流程且不是终态。

fake adapter 用于自动化验证，并参与卡片、目标仓库和标准流程阶段解析；只有真实 Jira 模式执行严格的远端代理绑定检查。

## 6. 所有权与目标仓库

真实 Jira 模式必须使用当前卡片事实执行以下判断：

- `assignee` 不再等于当前 Jira 用户时，返回 `assignee_changed`。
- `agentic_id` 为空时，返回 `agent_binding_lost`，不得根据本地记录自动抢回绑定。
- `agentic_id` 不等于当前 AIAgent 时，返回 `agent_ownership_conflict`。
- 当前无法解析目标仓库时，返回 `target_repo_missing`。
- 当前目标仓库与接管基准不同时，返回 `target_repo_changed`。

旧接管事件没有 `target_repo` 时，允许使用当前 Jira 字段或 profile 仓库映射的确定性结果补齐；恢复成功事件必须写入补齐后的 `target_repo`，供后续操作校验。历史事件已经有 `target_repo` 时必须执行一致性比较，不得用当前映射覆盖历史值。

目标仓库变化后不得让同一个 `agentic_run_id` 静默进入其它仓库。研发工程师确认范围变化后，应结束或释放旧接管，再按新事实重新接管。

## 7. 失败码

保留已有错误码：

- `missing_agentic_run_id`
- `run_not_found`
- `workspace_mismatch`
- `issue_mismatch`
- `local_state_mismatch`
- `event_read_failed`
- `jira_adapter_config_failed`
- `issue_not_found`
- `assignee_changed`
- `agent_ownership_conflict`

新增错误码：

- `agent_binding_lost`
- `target_repo_missing`
- `target_repo_changed`
- `resume_stage_not_allowed`
- `standard_process_not_found`
- `task_class_process_mismatch`
- `lifecycle_mapping_gap`
- `invalid_process_stage`
- `terminal_run`
- `human_gate_pending`
- `jira_issue_read_failed`
- `jira_current_user_failed`
- `operation_contract_not_found`
- `operation_contract_load_failed`
- `event_write_failed`
- `feedback_write_failed`

所有失败都必须：

- 不创建新 `agentic_run_id`。
- 不改变 Jira、代理绑定、目标仓库或流程。
- 输出中文 `required_human_action`。
- 在本地上下文可信时记录同一 `agentic_run_id` 的失败审计事件。

## 8. Jira 阻塞反馈闭环

### 8.1 两步原子流程

`resume-takeover` 保持只读。确认 `agentic_run_id`、`issue_key` 和工作空间可信后，如果发生任务级阻塞，生成：

```text
.agentic-ops/tasks/<ISSUE-KEY>/runs/<agentic_run_id>/resume-blocked-<code>.md
```

文件包含：

- 稳定反馈编号。
- `agentic_run_id`、Jira 卡片和工作空间。
- 失败码和中文说明。
- 接管时与当前值的安全摘要。
- 需要研发工程师处理的动作。

文件不得包含源码、token、凭据、原始敏感日志或个人本机路径。

CLI 失败输出增加：

```json
{
  "jira_feedback_required": true,
  "jira_feedback_write_allowed": true,
  "jira_feedback_file": ".agentic-ops/tasks/<ISSUE-KEY>/runs/<agentic_run_id>/resume-blocked-<code>.md",
  "jira_feedback_category": "blocked",
  "agentic_next_action": "add_task_comment"
}
```

AIAgent 随后调用现有原子操作：

```sh
agentic-cli add-task-comment <issue-key> \
  --workspace <workspace> \
  --category blocked \
  --content-file <file> \
  --run-id <run-id> \
  --confirm-real-jira-write
```

真实 Jira 写入继续经过所有权、操作契约、策略和显式确认门禁。评论成功后，以 `add_task_comment` 的完成审计事件作为 Jira 反馈闭环证据。

### 8.2 可写与不可写边界

以下阻塞在当前 assignee 仍匹配时设置 `jira_feedback_write_allowed: true`，可以进入受控评论写入：

- `agent_binding_lost`
- `target_repo_missing`
- `target_repo_changed`
- `resume_stage_not_allowed`
- `standard_process_not_found`
- `task_class_process_mismatch`
- `lifecycle_mapping_gap`
- `invalid_process_stage`
- `human_gate_pending`

以下情况说明当前 AIAgent 已失去任务写入资格，只生成评论材料，设置 `jira_feedback_write_allowed: false` 和 `agentic_next_action: ask_owner_to_add_task_comment`，由研发工程师或当前负责人处理：

- `assignee_changed`
- `agent_ownership_conflict`

以下本地或连接问题不生成 Jira 反馈：

- `missing_agentic_run_id`
- `run_not_found`
- `workspace_mismatch`
- `issue_mismatch`
- `terminal_run`
- 不可信或损坏的本地事件。
- Jira 无法连接或无法确认目标卡片。

评论包含稳定反馈编号。写入前应使用 `inspect-task` 检查 Jira 是否已经存在同一反馈，避免重复评论。远端写入成功但本地完成审计失败时，沿用现有 `retry_safe: false` 规则，必须先检查 Jira，不得盲目重试。

## 9. 输出契约

成功输出至少包含：

- `workspace`
- `agentic_run_id`
- `issue_key`
- `agent_id`
- `agentic_id`
- `task_class`
- `process_id`
- `target_repo`
- `previous_stage`
- `current_stage`
- `standard_process_stage`
- `agentic_next_action`

失败输出除通用字段外，在上下文可信时应尽可能包含：

- `workspace`
- `agentic_run_id`
- `issue_key`
- `task_class`
- `process_id`
- `target_repo`
- `current_stage`
- `standard_process_stage`
- `jira_feedback_required`
- `jira_feedback_write_allowed`
- `jira_feedback_file`
- `jira_feedback_category`

## 10. 验证重点

恢复能力的验证必须覆盖以下行为，不限定具体测试文件或实施步骤：

### 10.1 运行上下文恢复

- 从成功接管事件恢复完整上下文。
- 检测 workspace、issue、agent、任务分类、流程和仓库冲突。
- 保留最近有效阶段和 `agentic_next_action`。
- 通用 Jira 原子写事件不覆盖任务恢复点。
- 忽略旧版 `takeover_resumed` 对业务阶段的推进。
- 识别终态和待人工确认状态。

### 10.2 恢复门禁

- 所有事实匹配时允许恢复。
- assignee 已变化。
- 代理绑定丢失。
- 代理绑定冲突。
- 目标仓库缺失或变化。
- 操作阶段不允许恢复。
- 标准流程不存在。
- 任务分类不属于流程。
- Jira 状态无法映射。
- 映射阶段不属于流程。
- 流程已经终止。
- 最近状态等待人工确认。

### 10.3 CLI 行为

- fake 模式成功恢复并保留阶段和下一步。
- real 模式使用 recording client 完成只读复核。
- 成功输出包含 `target_repo` 和 `standard_process_stage`。
- 任务级阻塞生成安全的 Jira 评论文件。
- 失去 assignee 或代理所有权时禁止返回可直接写 Jira 的动作。
- 不可信本地错误不生成 Jira 评论文件。
- `resume-takeover` 不调用 Jira 写接口。
- 原子 `add-task-comment` 可以携带同一 `agentic_run_id` 写入生成的阻塞评论。

### 10.4 资源与流程一致性

- `resume-takeover` 的失败码和输出字段与操作契约一致。
- 两步 Jira 反馈流程与用户故事、AI 员工手册和独立原子写操作一致。
- fake 与 recording client 验证不调用真实 Jira 写接口。

具体验证入口、实现文件和阶段状态不属于架构设计事实源，由当前 `plans/` 计划和源码测试维护。任何实现仍必须遵守本设计的恢复只读边界，并在 Jira 写入时调用受控的独立原子操作。
