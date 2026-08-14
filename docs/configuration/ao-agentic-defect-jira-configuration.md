# AO Agentic 缺陷 Jira 配置记录

## 1. 配置范围

本记录保存 2026-08-01 在 Jira Cloud `tapdata.atlassian.net` 的 AO 业务空间中实际回读的 `Agentic 缺陷` 配置。它是后续 AgenticOps project profile 的映射材料，不替代 Jira 当前配置。

AO 专用 `Agentic 缺陷` profile 保留为 maintainer 试验资产，位于 `maintainer/standards/experiments/ao/profile.yaml`。它不随 developer-only 安装交付，不成为 Tapdata 等业务项目默认工作流；profile 校验和本地流程测试不执行真实 Jira 写入，真实卡片读取仍需显式输入授权与测试清单。

## 2. 项目与表单

| 配置项 | 实际值 |
| --- | --- |
| Jira site | `https://tapdata.atlassian.net` |
| Cloud ID | `9ee330de-c28d-40f8-a92e-5317b5670ea8` |
| Project key | `AO` |
| Project ID | `10248` |
| Project name | `agentic-ops` |
| Project type | `business` |
| Project style | `next-gen`，简化工作流 |
| Form name | `Agentic 缺陷` |
| Form ID | `68` |
| Form URL | `https://tapdata.atlassian.net/jira/core/projects/AO/form/68/builder` |

表单已配置为创建 `Agentic 缺陷` 工作类型，包含：

| 字段 | 要求 |
| --- | --- |
| 摘要 | 必填 |
| 描述 | 必填 |
| 附件 | 选填 |
| 执行模式 | 必填，只提供 `研发模式` 和 `无人值守模式` |

表单说明为：

```text
请说明问题、预期结果、影响范围和已知限制。提交后，任务将进入 AgenticOps 待接管流程。
```

已通过表单预览回读字段、必填标记和执行模式选项，未提交表单，未创建验证工作项。

## 3. 工作类型

| 配置项 | 实际值 |
| --- | --- |
| Work type name | `Agentic 缺陷` |
| Work type ID | `10103` |
| Hierarchy level | `0`，标准工作项 |
| Entity ID | `f0c92cbf-f0a9-45d1-bffe-dbb7feec0c40` |
| Settings URL | `https://tapdata.atlassian.net/jira/core/projects/AO/settings/issuetypes/10103` |

## 4. 字段映射

### 4.1 用户输入与决策字段

| 逻辑字段 | Jira 显示名 | Field ID | 类型或选项 |
| --- | --- | --- | --- |
| `execution_mode` | 执行模式 | `customfield_10353` | 单选；`研发模式=10582`、`无人值守模式=10583` |
| `task_branch` | 任务分支 | `customfield_10360` | 短文本 |
| `decision_request` | 待决策事项 | `customfield_10361` | 段落 |
| `decision_deadline` | 决策截止时间 | `customfield_10362` | 时间戳 |
| `decision_result` | 决策结果 | `customfield_10363` | 段落 |

### 4.2 AgenticOps 运行字段

| 逻辑字段 | 英文默认名 | 简体中文显示名 | Field ID | 类型 |
| --- | --- | --- | --- | --- |
| `agentic_id` | Agentic ID | 当前 AIAgent | `customfield_10364` | 短文本 |
| `agentic_run_id` | Agentic Run ID | 运行 ID | `customfield_10365` | 短文本 |
| `agentic_takeover_at` | Agentic Takeover Time | 接管时间 | `customfield_10366` | 时间戳 |
| `agentic_next_action` | Agentic Next Action | 下一步动作 | `customfield_10367` | 短文本 |
| `agentic_completion_evidence` | Agentic Completion Evidence | 完成证据 | `customfield_10368` | 段落 |
| `agentic_heartbeat_at` | Agentic Heartbeat Time | 最近心跳时间 | `customfield_10369` | 时间戳 |

Jira 创建元数据已回读以上十一个字段。运行时必须使用 Field ID 定位字段，不使用英文默认名或中文翻译名作为唯一标识。

本次配置过程中产生且无数据的六个 camelCase 临时字段已经删除：`agenticId`、`agenticRunId`、`agenticStarted`、`agenticAction`、`agenticResult`、`agenticUpdated`。Jira 提供 60 天恢复期，但 AgenticOps 不保留这些旧字段的兼容映射。

## 5. 状态映射

| 状态 | Status ID | 类别 |
| --- | --- | --- |
| 待接管 | `10177` | 待办 |
| 执行中 | `10178` | 进行中 |
| 等待决策 | `10179` | 进行中 |
| 待重新分配 | `10180` | 待办 |
| 已完成 | `10181` | 完成 |

## 6. 转换映射

以下 ID 已同时通过工作流编辑器和真实工作项可用转换接口回读，并在验证工作项上实际执行。

| 转换 | Transition ID | 来源 | 目标 |
| --- | --- | --- | --- |
| Create | `1` | 开始 | 待接管 |
| 接管任务 | `2` | 待接管 | 执行中 |
| 请求决策 | `3` | 执行中 | 等待决策 |
| 继续执行 | `4` | 等待决策 | 执行中 |
| 完成任务 | `5` | 执行中 | 已完成 |
| 决策超时 | `6` | 等待决策 | 待重新分配 |
| 结束接管 | `7` | 执行中 | 待重新分配 |
| 重新分配 | `8` | 待重新分配 | 待接管 |
| 重新打开 | `9` | 已完成 | 待重新分配 |

## 7. 工作流绑定

AO 是 `business / next-gen / simplified` 空间。Jira 的项目内简化工作流编辑器不提供独立工作流名称输入，且全局工作流列表中不存在可绑定 AO 的 `Agentic 工作流` 资产。因此：

- 逻辑名称仍为 `Agentic 工作流`。
- Jira 中的权威识别条件是工作流编辑器标题只关联 `Agentic 缺陷`。
- 工作流只保留五个专用状态、一个创建转换和八个业务转换。
- 不存在任意状态转换。
- 普通 `Task`、`故障`、`Sub-task` 和 `Workstream` 未绑定专用状态。

发布时 Jira 只选择 `Agentic 缺陷`，并按以下规则迁移该工作类型的既有状态：

| 旧状态 | 新状态 |
| --- | --- |
| To Do | 待接管 |
| In Progress | 执行中 |
| Done | 已完成 |

## 8. 验证结果

- 表单目标工作类型已回读为 `Agentic 缺陷`。
- 表单预览已验证摘要、描述、附件、执行模式和中文说明。
- 已发布工作流的五个状态 ID 为 `10177` 至 `10181`。
- 已发布工作流包含 `Create` 和八个业务转换，转换 ID 为 `1` 至 `9`。
- `Agentic 缺陷` 工作流编辑器只显示该工作类型。
- 从普通 `Task` 回读的共享工作流仍为 `To Do / In Progress / Done`，保留三个任意状态转换，且不含 Agentic 专用状态。
- Jira 全局工作流列表按 `Agentic`、`AO:` 和 `agentic-ops` 查询均无匹配，符合项目内简化工作流边界。
- Jira 项目元数据确认 AO 的 Project ID 为 `10248`，工作类型 ID 为 `10103`。
- Jira 创建元数据确认十一个自定义字段 ID 和执行模式选项 ID。

AgenticOps 源头仓库中的本地对接验证入口为：

```sh
bash tests/e2e/ao-profile-flow.sh
```

该验证会校验 AO profile、反馈分析契约和反馈建议契约；它只验证配置与本地 CLI 行为，不创建或修改真实 Jira 工作项。

## 9. 实例验证证据

| 配置项 | 实际值 |
| --- | --- |
| 验证工作项 | [AO-1](https://tapdata.atlassian.net/browse/AO-1) |
| Jira issue ID | `41447` |
| 摘要 | `[配置验证] Agentic 缺陷工作流` |
| 工作类型 | `Agentic 缺陷`，ID `10103` |
| 初始状态 | `待接管`，ID `10177` |
| 执行模式 | `研发模式`，option ID `10582` |
| 最终状态 | `已完成`，ID `10181` |
| 验证评论 | `46508` |

真实工作项在各状态下回读到的可用转换为：

| 当前状态 | 唯一或全部可用转换 |
| --- | --- |
| 待接管 | `接管任务=2` |
| 执行中 | `请求决策=3`、`完成任务=5`、`结束接管=7` |
| 等待决策 | `继续执行=4`、`决策超时=6` |
| 待重新分配 | `重新分配=8` |
| 已完成 | `重新打开=9` |

验证工作项实际执行并回读了全部八个业务转换：

1. 主流程：`接管任务 -> 请求决策 -> 继续执行 -> 完成任务`。
2. 重新授权：`重新打开 -> 重新分配`；重新打开后只进入 `待重新分配`，不会直接回到 `待接管`。
3. 主动退出：`接管任务 -> 结束接管 -> 重新分配`。
4. 决策超时：`接管任务 -> 请求决策 -> 决策超时 -> 重新分配`。
5. 最终收口：`接管任务 -> 完成任务`，最终状态保持 `已完成`。

Jira changelog 回读到全部状态变化，最终只提供 `重新打开=9`；验证评论 `46508` 已说明该工作项仅用于配置验证并记录验证结论。实例验证没有删除工作项，也没有修改现有业务工作项。
