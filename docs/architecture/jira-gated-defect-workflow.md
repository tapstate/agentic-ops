# Jira 门禁式缺陷修复流程设计

## 目标

AgenticOps 只定义公司和项目层面的原则、职责边界与关键门禁。研发工程师负责目标、范围、风险和关键决策，AIAgent 在已确认边界内持续完成分析、设计、编码、测试、文档和反馈。

本设计解决两个问题：

1. 缺陷准入不通过时，AIAgent 需要结合 Jira 卡片和项目代码形成可确认的补卡建议，并把关键过程写回 Jira。
2. 缺陷修复开始前，AIAgent 需要先形成修复计划，写回 Jira 并等待研发工程师确认。

## 职责边界

### 研发工程师

- 确认问题分支、修复分支、任务目标、范围边界和风险。
- 确认补卡内容是否成为任务事实。
- 确认修复计划及其重大变更。
- 确认推送、Pull Request、合并、发布等高风险动作。

### AIAgent

- 读取 Jira、项目资产和目标分支代码。
- 识别缺失信息并给出有依据的候选内容。
- 形成结构化分析、修复计划、验证证据和阻塞说明。
- 在获得对应人工确认后执行明确的 Jira 写入或代码修改。
- 信息不足或事实冲突时停止，不编造项目事实。

## Jira 信息归属

| 信息类型 | Jira 位置 | 原则 |
| --- | --- | --- |
| 稳定任务契约 | Description | 保存问题分支、修复分支、问题现象、复现路径、验收标准等确认后的当前事实。 |
| 分析与决策轨迹 | Comment | 保存准入分析、补卡建议、人工确认、修复计划、计划变更、阻塞说明和最终证据。 |
| 结构化实施结论 | Custom field | 通过项目 profile 的逻辑字段映射更新问题分析、修复详情和测试计划。 |
| 实际耗时 | Worklog | 只记录真实投入时间，不承载门禁、决策或计划。 |

Description 表示当前有效任务契约，Comment 表示不可覆盖的决策轨迹。AIAgent 不使用 Worklog 替代评论或描述。

## 缺陷准入流程

1. AIAgent 执行 `inspect-task`，读取 Jira 原始事实、Description、结构化表单值、Comment、所有权和项目资产路径。
2. AIAgent 按 Tapdata 缺陷准入资产检查问题分支、修复分支、问题现象、复现路径和验收标准。
3. 若信息不足，AIAgent 读取候选仓库和目标分支代码，形成“准入分析与补卡建议”。
4. 研发工程师确认真实 Jira 写入后，AIAgent把分析和建议写入 Jira Comment，然后停止本次接管。
5. 研发工程师确认补卡内容后，AIAgent 更新 Description 对应章节，并追加“补卡确认结果”Comment，然后结束本次接管。
6. 下一次启动时重新执行 `inspect-task`。只有 Jira 当前事实满足准入要求，才调用 `takeover-task`。

不允许在同一次补卡写入后自动接管任务，确保 Jira 成为下一次判断的事实源。

## 修复计划门禁

任务接管后，AIAgent 必须先完成代码分析并形成版本化修复计划，至少包含：

- 根因判断和证据；
- 修改范围与明确不修改范围；
- 目标模块、文件或接口；
- 实施步骤；
- 测试方法和验收映射；
- 风险、回滚或降级方式。

AIAgent 先把“修复计划 vN，待确认”写入 Jira Comment，然后停止代码修改。研发工程师确认后，AIAgent 追加确认 Comment，才可以修改代码。范围、风险、目标分支或核心方案发生实质变化时，必须生成新版本计划并重新确认。

## 完成回写

修复完成后：

1. 通过逻辑字段映射更新问题分析、修复详情和测试计划。
2. 写入最终证据 Comment，包含变更摘要、验证命令、结果、未覆盖风险和后续事项。
3. 代码提交、推送和 Pull Request 继续遵守各自人工门禁。

## AgenticCLI 原子操作

新增三个通用操作：

```text
agentic-cli add-task-comment <issue-key> \
  --workspace <project> \
  --category <analysis|plan|decision|evidence|blocked> \
  --content-file <path> \
  [--run-id <id>] \
  --confirm-real-jira-write

agentic-cli update-task-description-sections <issue-key> \
  --workspace <project> \
  --sections-file <path> \
  --confirm-real-jira-write

agentic-cli update-task-form <issue-key> \
  --workspace <project> \
  --values-file <path> \
  --confirm-real-jira-write
```

CLI 只负责：

- 参数、配置、Jira 身份和任务所有权校验；
- 真实 Jira 写入确认门禁；
- 根据项目 profile 的状态映射和 operation contract 校验允许阶段；
- Description 章节的安全合并；
- 逻辑字段到显式可写 Jira 字段的配置映射；
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
  修复分支: develop
  问题现象: 任务启动后出现重复告警。
  复现路径: 创建任务并连续启动两次。
  验收标准: 相同任务只产生一次告警。
```

CLI 只把 ADF `heading` 节点识别为章节边界，保留 Description 中未指定的 ADF 节点，替换同名标题下的全部内容，缺失标题追加到末尾。普通段落即使以冒号结尾也不作为章节边界。目标标题重复或 Jira Description 结构无法安全处理时必须失败，不进行部分写入。

## 表单字段更新约束

表单输入使用 profile 中声明的逻辑字段名。只有映射来源为 `jira_field`、配置了 Jira 字段 ID 且显式声明 `writable: true` 的字段可以写入。负责人、assignee、代理所有权、评论映射、描述章节映射和未知字段必须拒绝，避免绕过所有权门禁或专用原子操作。

## 角色协议

统一使用：

- 中文角色：`研发工程师`
- 协议角色：`development_engineer`
- 审查门禁：`development_engineer_review`
- 决策字段：`development_engineer_decision`

当前版本不保留旧角色名称或协议别名。
