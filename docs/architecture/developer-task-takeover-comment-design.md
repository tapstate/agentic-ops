# developer 任务接管 Comment 设计

## 1. 结论与边界

developer 工作面的业务 Jira 任务接管采用 `Assignee + Status + 受管 Comment + 本地 task state`，不创建、映射、探测或读写 Agentic Jira Custom Field。

本设计只约束安装后的 `ao-work` 和业务项目 AI 工作空间。`ao-maint` 只管理 Jira AO 项目；AO maintainer 工作面的专用字段、工作流和恢复规则由其独立设计约束，不受本文修改。

当前不提供跨工作空间、跨代理的并发锁。出现真实并发接管需求后，基于冲突场景、失败成本和 Jira 可用原语单独设计，不能把 Comment 误称为原子锁。

## 2. 事实源分工

| 信息 | 事实源 | 作用 |
| --- | --- | --- |
| 当前负责人 | Jira `Assignee` | 判断当前 Jira 用户是否有权接管和继续处理 |
| 团队可见阶段 | Jira `Status` | 表达项目工作流阶段，由 Project Profile 映射和严格 transition 规则推进 |
| 接管、恢复、进度与终态轨迹 | Jira Comment | 让人可见、可追溯，并为跨工具验收提供稳定引用 |
| 运行编号、细粒度阶段、恢复点与幂等状态 | 本地 task state | 支撑单工作空间内的连续执行、恢复和审计 |
| AIAgent 稳定身份 | 安装身份 `agent_id` | 标识执行者，不替代 Jira 负责人 |

Jira Comment 不替代 Jira Status，本地状态不覆盖 Jira 事实；三者冲突时停止并要求人工判断。

## 3. 统一入口与自动分类

用户只需要表达“接管 `<KEY>`”。AIAgent 调用：

```sh
ao-work task takeover <KEY> --authorization-reference <AUTHORIZATION_REFERENCE>
```

Runtime 自动分类：

- `new_takeover`：Jira 尚未进入目标执行状态，且本地没有可恢复运行。
- `accept_existing_task`：Jira 已在目标执行状态，但本地没有既有接管进度；Comment 必须明文写“不是新接管”。
- `resume_takeover`：本地 run 已处于 `takeover_started` 或 `blocked`；Comment 必须明文写“不是新接管”。

`ao-work task resume` 是只读恢复诊断，不承担正式恢复留痕。未提供 Jira key 的 `task takeover` 也只读列出候选，研发工程师确认后再执行写操作。

## 4. 接管顺序

正式接管按以下顺序执行：

1. 读取 Jira 当前用户和卡片，验证工作空间绑定与 `Assignee`。
2. 验证当前状态、目标执行状态及可用 transition 的严格映射；已知映射缺口在任何写操作前阻断。
3. 生成或复用本地 `agentic_run_id`，计算接管类型。
4. 写入结构化中文接管 Comment，并按评论 ID 与稳定标记回读确认。
5. 如需要，执行 Jira transition 并回读目标 Status。
6. 写入本地 `takeover_started` 记录和 Comment 引用。

受管 Comment 至少包含任务编号、接管类型、运行编号、AIAgent、工作空间、时间、状态变化、当前阶段和下一步动作。稳定标记绑定 `issue_key`、`agentic_run_id`、接管类型和授权引用摘要，用于同一授权重试时避免重复评论；标记不包含授权原文或秘密。幂等复用和 task-to-PR 正式接管验证还必须确认 Comment 作者是当前工作空间绑定的 Jira 账户，不能只匹配可复制的文本标记。

如果 Comment 写入结果不明确或回读不一致，必须在 transition 前停止。如果 Comment 已确认但后续 transition 失败，Comment 保留为已发起接管的审计事实，Runtime 返回失败并等待人工核对，不能声称接管完成。

## 5. task-to-PR 验收

developer Runtime 的 Jira probe 通过当前 `issue_key + agentic_run_id` 对应的受管接管 Comment 验证 `formal_takeover_verified`，输出 `takeover_comment_id`。共享 manifest 不再声明 Agentic 字段映射。

没有匹配 Comment 时仍可生成完整失败或阻塞结果包，但不得声称正式接管已验证；必须记录绑定 Jira probe 的 `automation_gap` 和残留风险。

## 6. 新工作空间继承

`ao-work workspace init` 生成的业务项目 AI 入口只加载 developer 资产。以下源头共同保证新工作空间继承本设计：

- `developer/AGENTS.md`：自然语言入口、工作面边界和强制行为。
- `developer/skills/daily-task-operations/SKILL.md`：简洁命令编排与接管分类提示。
- `developer/standards/capabilities/operations.yaml`：能力状态和真实命令。
- `developer/standards/contracts/`：输入、输出、副作用和失败合同。

不得在新工作空间初始化时生成 Agentic Custom Field 映射，也不得把根仓库 maintainer 规则复制到业务工作空间。

## 7. 风险与后续触发条件

当前 Comment 写入与 Status transition 不是 Jira 单次原子操作，存在 Comment 已成功但 transition 失败的部分完成状态。顺序和回读可以让该状态可见、可恢复，但不能防止两个独立工作空间同时尝试接管。

只有出现下列真实需求之一时，才启动并发专题设计：

- 同一卡片确实会被多个开发者工作空间同时接管。
- 重复执行造成代码冲突、错误状态推进或不可接受的人工恢复成本。
- Jira 项目提供可验证的原子 transition 条件、Automation 或其它锁原语。

专题设计必须保留 `ao-work task takeover <KEY>` 的简洁入口，由 Runtime 内部处理并发协议，不把额外选择转嫁给用户。
