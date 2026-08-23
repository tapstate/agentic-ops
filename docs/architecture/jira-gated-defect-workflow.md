# Jira 门禁式缺陷修复流程设计

> 本文定义现役业务流程。命令事实以 `ao-work capability list|show` 为准：`takeover_task` 已由顶层 `ao-work takeover [<KEY>]` 实现；`inspect_task` 与 `update_task_form` 仍是 `capability_gap`。developer 接管采用 D-051 的 Assignee、Status、受管 Comment 与本地状态模型，不使用 Agentic Jira Custom Field。

## 目标

AgenticOps 只定义公司和项目层面的原则、职责边界与关键门禁。研发工程师负责目标、范围、风险和关键决策，AIAgent 在已确认边界内持续完成分析、设计、编码、测试、文档和反馈。

本设计解决两个问题：

1. 缺陷准入不通过时，AIAgent 需要结合 Jira 卡片和项目代码形成可确认的补卡建议，并把关键过程写回 Jira。
2. 缺陷修复开始前，AIAgent 需要形成完整设计，在设计审查后连续推进到代码审查。

## 职责边界

### 研发工程师

- 审查完整设计、范围、验证方式和逐项风险。
- 在代码审查节点检查 PR 当前 Head 或未推送本地 commit。
- 决定事实冲突、范围变化和高风险取舍。
- 单独确认合并、发布、Git Tag、强推和历史改写。

### AIAgent

- 读取 Jira、项目资产和目标分支代码。
- 识别缺失信息并给出有依据的候选内容。
- 形成结构化分析、修复计划、验证证据和阻塞说明。
- 在工作项级连续执行授权内完成普通 Jira 进度回写、代码修改、验证和任务分支交付。
- 信息不足或事实冲突时停止，不编造项目事实。

## Jira 信息归属

| 信息类型 | Jira 位置 | 原则 |
| --- | --- | --- |
| 稳定任务契约 | Description | 保存问题分支、问题版本、问题现象、复现路径、验收标准等确认后的当前事实。 |
| 分析与决策轨迹 | Comment | 保存准入分析、补卡建议、人工确认、修复计划、计划变更、阻塞说明和最终证据。 |
| 结构化实施结论 | 不使用 Agentic Custom Field | 通过 Description、受管 Comment 与本地审计分别保存稳定事实、可见轨迹和恢复状态。 |
| 实际耗时 | Worklog | 只记录真实投入时间，不承载门禁、决策或计划。 |

Description 表示当前有效任务契约，Comment 表示不可覆盖的决策轨迹。AIAgent 不使用 Worklog 替代评论或描述。

## 缺陷准入流程

1. 用户明确要求接管后，AIAgent 执行 `ao-work takeover <issue-key>`；Runtime 自动判断新接管、接纳存量或恢复，完成 Comment、必要 Status transition 和本地状态回读。
2. AIAgent 查询 `jira_inspect` 并执行 `ao-work jira inspect --issue-key <issue-key>` 读取基础 Jira 事实，再通过 Jira 页面或项目认可的只读工具补齐 Description、Comment 和项目资产路径；不得读取或依赖 Agentic Custom Field。
3. AIAgent 按 Tapdata 缺陷准入资产检查问题分支、问题版本、问题现象、复现路径和验收标准。
4. 若信息不足，AIAgent 读取候选仓库和目标分支代码，形成“准入分析与补卡建议”，在当前工作项授权范围内通过受控 Jira Comment 留痕，然后停止进入实现。
5. 补卡完成后重新读取 Jira 事实并重新分析；保持同一接管 run，不创建第二条接管记录，也不沿用旧准入结论。

## 修复计划门禁

任务接管后，AIAgent 必须先完成代码分析并形成版本化修复计划，至少包含：

- 根因判断和证据；
- 修改范围与明确不修改范围；
- 目标模块、文件或接口；
- 实施步骤；
- 测试方法和验收映射；
- 风险、回滚或降级方式。

AIAgent 展示完整设计、范围、验证方式和逐项风险，进入设计审查。设计确认后形成工作项级连续执行授权，普通实现、验证、必要 Jira 进度回写、提交、任务分支推送和 PR 创建连续推进到代码审查，不增加准入摘要确认或通用方案摘要确认。范围、风险、目标分支或核心方案发生实质变化时，旧授权失效并重新进入设计审查或风险决策。

## 完成回写

修复完成后：

1. 写入最终证据 Comment，包含变更摘要、验证命令、结果、未覆盖风险和后续事项。
2. 功能、修复和任务分支交付到真实 PR 当前 Head 后进入代码审查；其它允许分支形成未推送本地 commit 后进入推送前审查。
3. 不写 Agentic Custom Field；合并、发布等受保护动作继续使用独立人工门禁。

## ao-work 原子操作边界

当前已实现的 Comment 与 Description 使用分阶段协议：

```text
ao-work jira comment plan -> apply -> readback
ao-work jira description plan -> apply（内部写后回读）
```

Custom Field 写入对应 `update_task_form` 目标能力，当前为 `capability_gap`，必须开专题完成字段映射、Context、Screen、权限和验收后再启用。

CLI 只负责：

- 参数、配置、Jira 身份和任务所有权校验；
- 真实 Jira 写入确认门禁；
- 根据项目 profile 的状态映射和 operation contract 校验允许阶段；
- Description 章节的安全合并；
- Jira 写入前审计意图、写入后完成审计和可区分的部分成功结果；
- 结构化结果。

CLI 不负责：

- 判断 Tapdata 缺陷准入是否通过；
- 推断需要哪些业务字段；
- 生成分析、补卡建议或修复计划；
- 决定何时进入下一流程阶段。

## 描述章节更新约束

章节输入采用 YAML：

```yaml
sections:
  问题分支: develop
  问题版本: develop
  问题现象: 任务启动后出现重复告警。
  复现路径: 创建任务并连续启动两次。
  验收标准: 相同任务只产生一次告警。
```

CLI 只把 ADF `heading` 节点识别为章节边界，保留 Description 中未指定的 ADF 节点，替换同名标题下的全部内容，缺失标题追加到末尾。普通段落即使以冒号结尾也不作为章节边界。目标标题重复或 Jira Description 结构无法安全处理时必须失败，不进行部分写入。

## Custom Field 边界

developer 工作面不创建、映射、探测或读写 Agentic Jira Custom Field。`update_task_form` 只保留未实现的历史目标契约，不能用于当前缺陷流程；负责人、Status、Comment 和 Description 必须分别走现役专用能力，不能借通用字段写入绕过所有权、工作流或审计门禁。

## 角色协议

统一使用：

- 中文角色：`研发工程师`
- 协议角色：`development_engineer`
- 审查门禁：`development_engineer_review`
- 决策字段：`development_engineer_decision`

当前版本不保留旧角色名称或协议别名。
