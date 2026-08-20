# developer 任务接管 Comment 与流程语义设计

## 1. 结论与边界

developer 工作面的业务 Jira 任务接管采用 `Assignee + Status + 受管 Comment + 本地 task state`，不创建、映射、探测或读写 Agentic Jira Custom Field。

用户只表达“接管 <KEY>”。Runtime 自动判断新接管、接纳存量或恢复；接管完成后再进行任务分类、流程选择和设计分析。普通信息分析、证据化补全和方案分级连续推进，固定暂停点是设计审查、代码审查和风险决策。

本设计只约束安装后的 `ao-work` 和业务项目 AI 工作空间。`ao-maint` 只管理 Jira AO 项目，其字段、工作流和恢复规则独立维护。

当前不提供跨工作空间并发锁。Comment 是可见审计和幂等证据，不是原子锁。

## 2. 事实源分工

| 信息 | 事实源 | 作用 |
| --- | --- | --- |
| 当前负责人 | Jira `Assignee` | 判断当前 Jira 用户是否有权接管和继续处理 |
| 团队可见阶段 | Jira `Status` | 由 Project Profile 严格映射和 transition 推进 |
| 接管、恢复、进度与终态轨迹 | Jira Comment | 让人可见、可追溯，为跨工具验收提供引用 |
| 运行编号、细粒度阶段、恢复点与幂等状态 | 本地 task state | 支撑单工作空间连续执行和恢复，不替代 Jira |
| AIAgent 稳定身份 | 安装身份 `agent_id` | 标识执行者，不替代 Jira 负责人 |

Jira Comment 不替代 Status，本地状态不覆盖 Jira 事实；三者冲突时停止并进入风险决策。

## 3. 统一操作与自动分类

当前 Runtime 原子入口为：

```sh
ao-work task takeover <KEY> --authorization-reference <INTERNAL_REFERENCE>
```

`INTERNAL_REFERENCE` 由 AIAgent 绑定研发工程师明确的“接管 <KEY>”指令，用户不查看或复制。顶层公开命令 `ao-work takeover` 由 AO-48 落地；在此之前不得让用户理解多级内部命令。

成功类型：

- `new_takeover`：没有可恢复本地运行，Jira 尚未进入目标执行状态。
- `accept_existing_task`：没有可恢复本地运行，Jira 已在执行状态且没有可见冲突。
- `resume_takeover`：当前工作空间存在同任务、同运行的可验证恢复点。

`blocked` 是失败结果，不是第四种 `takeover_kind`。负责人、状态映射、Agent 身份、本地运行或受管 Comment 冲突时必须阻断。

`ao-work task resume` 是只读恢复诊断，不承担正式恢复留痕。未提供 Jira key 的 takeover 只读列出候选，由研发工程师选择目标后再正式接管。

## 4. 标准流程

```text
明确接管指令
-> 接管预检
-> Comment/Status/本地 run 回读完成
-> 信息分析与带来源补全
-> 方案分析与风险分流
-> 设计审查
-> 实现或调查
-> 代码/结果审查
-> 完成审计
```

初始接管只要求项目、Assignee、Status/transition、Agent 身份和本地恢复事实可验证。`task_class`、`process_id`、`target_repo`、`target_branch` 和 `verification_method` 在接管后补全；缺失或冲突时阻止实现，不阻止建立接管轨迹。

准入摘要确认和通用方案摘要确认不再是独立用户门禁。方案正常时进入设计审查；需要用户取舍或存在非平凡风险时进入风险决策；事实、权限或能力冲突时阻断。

## 5. 接管写入顺序

正式接管当前按以下顺序执行：

1. 读取 Jira 当前用户和卡片，验证工作空间绑定与 `Assignee`。
2. 验证 Status、目标执行状态及可用 transition 的严格映射；已知缺口在任何写操作前阻断。
3. 生成或复用本地 `agentic_run_id`，计算接管类型。
4. 写入结构化中文接管 Comment，并按 Comment ID、作者与稳定标记回读确认。
5. 如需要，执行 Jira transition 并回读目标 Status。
6. 写入本地 `takeover_started` 记录和 Comment 引用。

受管 Comment 至少包含任务编号、接管类型、运行编号、AIAgent、工作空间、时间、状态变化、当前阶段和下一步动作。稳定标记绑定 `issue_key`、`agentic_run_id`、接管类型和授权摘要，不包含授权原文或秘密。复用必须验证 Comment 作者是当前工作空间绑定的 Jira 账户。

非新接管必须同时在 `human_notice` 和 Comment 中写“不是新接管”。成功输出包含 `takeover_status=completed`、三种之一的 `takeover_kind`、Comment ID、Status 前后值和结构化下一动作。

Comment 与 Status 目前不是单次 Jira 原子操作。Comment 写入结果不明确时必须在 transition 前停止；Comment 已确认而 transition 失败时保留 Comment 审计，不声称接管完成。AO-49、AO-50 负责后续 Saga 与本地 phase 完善。

## 6. task-to-PR 验收

developer Runtime 的 Jira probe 通过当前 `issue_key + agentic_run_id` 对应的受管接管 Comment 验证 `formal_takeover_verified`，输出 `takeover_comment_id`。共享 manifest 不声明 Agentic 字段映射。

没有匹配 Comment 时可以生成失败或阻塞结果包，但不得声称正式接管已验证；必须记录 Jira probe 的自动化缺口和残留风险。

## 7. 新工作空间继承

`ao-work workspace init` 生成的业务项目 AI 入口只加载 developer 资产。以下源头共同保护本设计：

- `developer/AGENTS.md`：自然语言入口、工作面边界和固定门禁。
- `developer/skills/daily-task-operations/SKILL.md`：接管优先、自动分类和非新提示。
- `developer/standards/capabilities/operations.yaml`：当前真实 Runtime 能力。
- `developer/standards/contracts/`：输入、输出、流程、门禁和失败合同。
- developer 故事与固定资源测试：保护用户可见行为和安装继承。

新工作空间不得生成 Agentic Custom Field 映射，不得把根仓库 maintainer 规则复制到业务工作空间。

## 8. 风险与后续触发条件

当前顺序和回读能让部分完成状态可见，但不能防止两个独立工作空间同时尝试接管。只有出现真实并发冲突或不可接受恢复成本时才启动并发专题设计。

后续工作项分工：

- AO-48：顶层 `ao-work takeover` 与统一路由。
- AO-49：Comment/Status Jira Saga、回读和不确定结果恢复。
- AO-50：本地操作 phase、事件和输出 Schema。
- AO-51：Skill、文档、Fake Jira 与真实 Jira E2E 总验收。
