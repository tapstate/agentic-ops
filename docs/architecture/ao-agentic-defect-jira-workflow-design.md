# AO Agentic 缺陷表单与工作流设计

> **试验边界：** AO 专用 `Agentic 缺陷` 工作流保留为 maintainer 试验资产，不随 developer-only 安装成为默认流程。日常业务研发以 Tapdata 为主；未经专题确认，不得把本设计写入 Tapdata Profile 或通用 Runtime。

## 1. 目的

本文定义 Jira AO 空间中 `Agentic 缺陷` 表单、专用工作类型和任务接管工作流。目标是让 Jira 成为任务状态、接管授权、当前代理绑定、决策等待和完成审计的事实源，并为研发模式与无人值守模式提供同一套可恢复流程。

本文只定义 Jira 配置和 AgenticOps 映射边界，不实现常驻调度服务、飞书收发、CLI 新命令或跨项目通用工作流。

## 2. 已确认现状

- AO 是团队管理的 Jira 业务空间。
- `Agentic 缺陷` 表单已经存在，表单编号为 `68`。
- 表单当前创建 `Task`，提交项带标签 `business-form-68`。
- 表单当前只包含摘要、描述和附件。
- AO 当前工作类型包括 `Workstream`、`Task`、`故障` 和 `Sub-task`。
- AO 当前项目内工作流由 `Task`、`故障` 和 `Sub-task` 共享，状态只有 `To Do`、`In Progress` 和 `Done`。
- Jira 全局活动和非活动工作流中均不存在 `Agentic 工作流`。
- AO 不使用全局工作流方案；创建一个全局同名工作流不能直接绑定 AO。

## 3. 方案选择

### 3.1 采用方案：项目内专用工作类型和独立工作流

在 AO 中创建项目专用工作类型 `Agentic 缺陷`，让同名表单创建该工作类型，再从现有共享工作流拆分出只作用于该工作类型的项目内工作流。

该方案的优点：

- 不影响普通 `Task`、`故障` 和 `Sub-task`。
- 表单来源、任务类型和工作流边界一致。
- 团队管理空间可由空间管理员独立维护。
- 后续 AgenticOps profile 可以按工作类型和稳定字段映射，不依赖表单标签猜测任务类别。

项目内工作流的逻辑名称为 `Agentic 工作流`。如果 Jira 保存界面允许设置显示名称，则使用该名称；如果团队管理空间只显示 Jira 自动生成的项目内名称，则以“只绑定 `Agentic 缺陷` 工作类型”和 profile 标识作为权威识别条件，不创建无法绑定 AO 的无效全局工作流。

### 3.2 不采用：直接修改现有共享工作流

该方案会同时改变 `Task`、`故障` 和 `Sub-task`，扩大影响范围，并使普通任务出现 AgenticOps 专用状态，因此不采用。

### 3.3 不采用：创建全局 `Agentic 工作流`

AO 是团队管理空间，不使用全局工作流方案。全局工作流即使创建成功也不能证明 AO 已使用，因此不采用。

## 4. 表单与字段

### 4.1 表单提交字段

`Agentic 缺陷` 表单创建 `Agentic 缺陷` 工作类型，保留以下用户输入：

| 逻辑字段 | Jira 显示名 | 类型 | 要求 |
| --- | --- | --- | --- |
| `summary` | 摘要 | 系统短文本 | 必填 |
| `description` | 描述 | 系统段落 | 必填 |
| `attachments` | 附件 | 系统附件 | 选填 |
| `execution_mode` | 执行模式 | 单选 | 必填，取值为 `研发模式`、`无人值守模式` |

表单说明必须使用中文，明确描述问题、预期结果、影响范围和已知限制。表单不要求提交人填写 AIAgent 运行标识、分支、审计引用或内部锁字段。

### 4.2 运行字段

以下字段属于 `Agentic 缺陷` 工作类型，但不放在公开表单的主要输入区：

| 逻辑字段 | Jira 显示名 | 类型 | 写入者与用途 |
| --- | --- | --- | --- |
| `agentic_id` | Agentic ID | 短文本 | AgenticOps；当前锁持有者，未接管或锁已释放时为空 |
| `agentic_run_id` | Agentic Run ID | 短文本 | AgenticOps；每次接管生成的唯一运行标识 |
| `agentic_takeover_at` | Agentic Takeover Time | 时间戳 | AgenticOps；本次接管成功时间 |
| `agentic_next_action` | Agentic Next Action | 短文本 | AgenticOps；与操作契约一致的下一步动作 |
| `agentic_completion_evidence` | Agentic Completion Evidence | 段落 | AgenticOps；完成、阻塞、交接或恢复结果及证据摘要 |
| `agentic_heartbeat_at` | Agentic Heartbeat Time | 时间戳 | AgenticOps；当前锁持有者最近一次成功心跳时间 |
| `task_branch` | 任务分支 | 短文本 | AgenticOps；Git 分支引用，不替代 Git 事实源 |
| `decision_request` | 待决策事项 | 段落 | AgenticOps；等待用户决策的结构化问题 |
| `decision_deadline` | 决策截止时间 | 时间戳 | AgenticOps；无人值守模式十分钟截止时间 |
| `decision_result` | 决策结果 | 段落 | AgenticOps；用户回复或超时结论 |

AgenticOps 逻辑字段统一采用以 `agentic_` 开头的英文 snake_case 协议名，不保留 `current_agent_id`、`run_id` 等旧别名；Jira 默认字段名采用以 `Agentic` 开头的英文 Title Case 展示名，既保留命名空间，又与 Jira 基本属性风格一致。其它 Jira 人可见字段使用中文。AgenticOps profile 使用逻辑字段名映射具体 Jira field id，Jira field id 才是运行时稳定绑定，显示名不能作为唯一身份。

六个运行字段以英文产品展示名作为 Jira 默认名称，并配置简体中文翻译。Jira 按用户语言显示翻译；未配置的语言回退到英文默认名：

| 默认名称 | 简体中文显示名 |
| --- | --- |
| `Agentic ID` | 当前 AIAgent |
| `Agentic Run ID` | 运行 ID |
| `Agentic Takeover Time` | 接管时间 |
| `Agentic Next Action` | 下一步动作 |
| `Agentic Completion Evidence` | 完成证据 |
| `Agentic Heartbeat Time` | 最近心跳时间 |

自动化、JQL 和 AgenticOps profile 不使用默认名或翻译名定位字段，只使用配置记录中的 Jira field id。

不创建 `agentic_previous_run_id` 或 `last_audit_reference`。前一轮运行通过 Jira 变更历史、`agentic_completion_evidence` 中的 `agentic_run_id` 和审计评论关联；幂等操作标记写入 Jira 审计评论和本地事件，不额外创建字段。Jira 状态是任务阶段事实源，AgenticOps 内部 `current_stage` 通过 profile 映射状态，不在 Jira 再保存重复阶段字段。

### 4.3 授权事实

不增加与状态重复的 `takeover_authorized` 布尔字段。`待接管` 状态本身表示当前事实版本已获准接管；`待重新分配` 表示自动接管资格已经消耗，必须由用户执行 `重新分配` 转换后才能再次进入 `待接管`。

这样可避免“状态允许但授权字段禁止”或相反的双事实冲突。

## 5. 工作流

### 5.1 状态

| 状态 | Jira 状态类别 | 语义 |
| --- | --- | --- |
| `待接管` | 待办 | 表单新建或用户明确重新分配，允许一次自动接管 |
| `执行中` | 进行中 | 已被一个 AIAgent 接管并执行 |
| `等待决策` | 进行中 | 无人值守模式已发出决策请求，等待用户回复 |
| `待重新分配` | 待办 | 本次接管已经结束，不允许自动再次接管 |
| `已完成` | 完成 | 完成审计已写入，任务处理结束 |

### 5.2 转换

| 转换 | 来源 | 目标 | 使用者与约束 |
| --- | --- | --- | --- |
| `创建` | 开始 | `待接管` | 表单创建后的初始状态 |
| `接管任务` | `待接管` | `执行中` | AgenticOps；只允许候选任务执行 |
| `请求决策` | `执行中` | `等待决策` | AgenticOps；写入决策事项与截止时间 |
| `继续执行` | `等待决策` | `执行中` | AgenticOps；确认用户回复后执行 |
| `决策超时` | `等待决策` | `待重新分配` | AgenticOps；先确认退出审计，再结束接管 |
| `结束接管` | `执行中` | `待重新分配` | AgenticOps；用于阻塞、人工退出或不可继续场景 |
| `重新分配` | `待重新分配` | `待接管` | 仅用户明确操作；产生新的接管授权 |
| `完成任务` | `执行中` | `已完成` | AgenticOps；完成审计确认后执行 |
| `重新打开` | `已完成` | `待重新分配` | 仅用户明确操作；重新打开不直接授权自动接管 |

不创建从任意状态进入 `待接管`、`执行中` 或 `已完成` 的全局转换。所有恢复路径必须经过明确状态和审计门禁。

维护 Runtime 的 Jira 工作流映射必须按 `project_key + issue_type` 选择：同一 AO 项目中的普通 `Task` 可继续使用项目默认映射，`Agentic 缺陷` 必须使用本节的专属映射（`待接管` → `接管任务` → `执行中`）。不得把两类状态或 transition 混入同一无类型映射，否则同名或相近状态会被错误自动化。

## 6. 接管与会话规则

### 6.1 候选任务

无人值守调度只选择同时满足以下条件的任务：

- Jira 空间为 AO。
- 工作类型为 `Agentic 缺陷`。
- 状态为 `待接管`。
- `agentic_id` 为空。
- `execution_mode` 为 `无人值守模式`。

研发模式由用户在当前任务中明确发起接管，但仍必须满足工作类型、状态和代理绑定门禁。

### 6.2 一次授权只接管一次

接管成功后立即离开 `待接管`。无论任务完成、阻塞、决策超时还是人工退出，都不得自动回到 `待接管`。只有用户执行 `重新分配` 后，任务才重新成为候选。

这条规则替代本地生成式标记、自动评论和“事实版本指纹”猜测，防止无人值守调度无限接管同一任务。

`接管任务` 必须通过一次 Jira 转换请求原子完成：来源状态只能是 `待接管`，目标状态是 `执行中`，同一请求写入 `agentic_id`、`agentic_run_id`、`agentic_takeover_at`、`agentic_next_action` 和 `agentic_heartbeat_at`，并清空上一轮 `agentic_completion_evidence`。并发代理即使读取到同一候选任务，也只有第一个转换能成功；后续代理必须把转换失败视为未获得锁，不得继续执行。

`agentic_heartbeat_at` 用于识别失联锁，但超时只触发恢复审计，不允许自动抢占。不能使用 Jira 系统 `updated` 字段代替心跳，因为任何任务编辑都会更新该字段。恢复流程必须把任务送到 `待重新分配`，仍需用户执行 `重新分配` 才能产生新的接管授权。

### 6.3 工作区与执行会话

- 同一项目 AI 工作空间同一时间只允许一个活动任务。
- 每次接管创建新的 `agentic_run_id`、独立 AIAgent 执行上下文和 Git worktree。
- 同一个 AIAgent 执行会话不处理多个 Jira 任务。
- 再次接管时覆盖 `agentic_run_id`；上一轮通过 Jira 变更历史、审计评论和 `agentic_completion_evidence` 中的运行标识关联。
- Git 分支可以保留；worktree 只有在干净或改动已经按规则安全进入分支后才能删除。
- 新 AIAgent 不回放旧聊天上下文，只从 Jira 当前事实、Git 分支、最近审计和结构化交接恢复。

## 7. 完成、退出与网络恢复

### 7.1 三个原子操作

一个“完成任务”步骤由三个职责独立的原子操作组成：

1. `write-evidence`：生成并校验本地完成证据，不写 Jira，不释放锁。
2. `submit-task-audit`：把完成、阻塞或交接摘要写入 Jira `agentic_completion_evidence`，在审计评论中记录稳定操作标记，更新 `agentic_next_action` 和 `agentic_heartbeat_at` 并执行目标状态转换，不释放锁。
3. `release-agent`：最后清理 Jira `agentic_id` 和项目工作空间租约，同时更新 `agentic_next_action` 和 `agentic_heartbeat_at`。

`release-agent` 必须最后执行。Jira 审计未确认时不得释放代理绑定、项目租约或 worktree，也不得接管下一任务。

### 7.2 可恢复阶段

运行状态至少包含：

```text
evidence_ready
-> audit_submit_pending
-> audit_submitted
-> release_pending
-> completed
```

Jira 审计使用稳定操作标记和证据哈希。写入结果因网络中断而不确定时：

1. 保留 `agentic_id`、项目租约和 worktree。
2. 网络恢复后先重新读取 Jira，查找相同操作标记。
3. 标记存在则继续下一阶段；标记不存在才重试写入。
4. 不盲目重复评论、状态转换或锁释放。

### 7.3 研发模式

接管后在当前任务中引导研发工程师完成工作。只有完成证据回写、任务审计确认和锁释放后，才退出任务接管。

### 7.4 无人值守模式

接管后按标准流程执行。需要决策时进入 `等待决策` 并发送飞书通知；用户十分钟内回复则进入 `继续执行`，超时则提交退出审计、进入 `待重新分配`、释放锁，再由调度器选择其他候选任务。

飞书只是通知和回复入口，Jira 状态、字段和审计仍是任务事实源。

## 8. 配置与运行时边界

Jira 项目配置负责：

- 工作类型、表单字段、状态、转换和项目内工作流绑定。
- 通过状态表达接管授权和退出结果。
- 保存当前代理绑定、运行引用、决策信息和审计证据。

AgenticOps profile 负责：

- Jira field id、状态和 transition id/name 映射。
- 标准阶段与 AO 私有状态的对应关系。
- 任务候选 JQL、工作空间和仓库映射。
- 明确缺口时阻断，不按显示名猜测。

试验 adapter 通过 developer 受控原子操作负责：

- 所有权门禁、操作契约、显式真实 Jira 写确认和审计。
- 三个原子操作、幂等恢复和错误码。
- worktree、项目租约和 AIAgent 执行上下文生命周期。

## 9. 配置顺序

1. 创建 AO 项目内工作类型 `Agentic 缺陷`。
2. 为该工作类型创建运行字段。
3. 将表单 `Agentic 缺陷` 改为创建该工作类型，并增加必填执行模式。
4. 从共享工作流拆分出只绑定 `Agentic 缺陷` 的项目内工作流。
5. 配置五个状态和九个转换，移除无约束的任意状态转换。
6. 保存并确认普通 `Task`、`故障` 和 `Sub-task` 的工作流未变化。
7. 读取 Jira field id、status id 和 transition id/name，形成 AgenticOps profile 映射材料。
8. 在获得单独确认后创建一张配置验证任务，验证表单、状态转换和再次接管门禁。

## 10. 验收标准

- `Agentic 缺陷` 表单创建专用 `Agentic 缺陷` 工作类型，而不是 `Task`。
- 表单包含必填摘要、必填描述、选填附件和必填执行模式。
- 专用工作流只绑定 `Agentic 缺陷`，不影响 AO 其它工作类型。
- 新任务初始状态为 `待接管`。
- 不存在绕过流程直接进入 `执行中` 或 `已完成` 的任意状态转换。
- `待重新分配` 只能由用户执行 `重新分配` 后回到 `待接管`。
- `已完成` 重新打开后先进入 `待重新分配`，不会自动再次接管。
- Jira 配置能够提供稳定 field id、status id 和 transition id/name，供 profile 映射。
- 未经单独确认不创建验证任务，不删除或迁移现有 Jira 工作项。
