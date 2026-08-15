# PM-005 处理反馈并形成改进建议

> **目标故事 / `capability_gap`。** 本故事定义未来的聚合分析与 proposal 验收行为，不声明现役 `ao-maint feedback ...` 已实现。当前 maintainer parser 没有这些命令；不得调用、模拟成功或把历史 Go 能力当成现役入口。现阶段只接受 developer `task_to_pr_review` Runtime 生成的脱敏结果包及其完整 retrospective，由公司员工指导员显式交接后人工分析。

作为项目维护者，
我希望能从任务执行记录中分析重复失败、阻塞点和人工确认点，
以便把有效经验沉淀为 AgenticOps 改进建议。

### 触发方式

现役触发：

1. developer 工作面执行 `ao-work task-run finalize`，生成 `ready_for_pr_review`、`blocked` 或 `failed` 结果包；
2. 结果包必须包含四类逐项复盘、全部 finding、人工介入、失败、重试、等待和残留风险引用；
3. 公司员工指导员显式选择该脱敏包并交给独立 maintainer 工作面；maintainer 不自动扫描业务工作空间；
4. maintainer 先只读验收包内协议，再人工整理 observation 与 proposal。

以下仅是未来目标命令合同，当前调用应视为 `capability_gap`：

```sh
./maintainer/bin/ao-maint feedback report --input-manifest <manifest.json>
./maintainer/bin/ao-maint feedback analyze --input-manifest <manifest.json>
./maintainer/bin/ao-maint feedback propose --input-manifest <manifest.json>
```

### 前置条件

- 输入由人工确认的清单或脱敏包显式提供；`ao-maint` 不从本机其它业务工作空间自动搜集资料。

- 输入包由 developer Runtime 生成并带完整 hash-chain timeline、任务级证据和 retrospective；早期阻塞包也必须如实说明未核验项。
- 反馈数据已经脱敏。
- 改进建议不会未经人工确认直接修改源头仓库。

### 主流程

1. 公司员工指导员显式接收并只读验收 developer 结果包，不继承业务凭据或工作空间状态。
2. 当前阶段由维护者人工聚合失败码、阻塞原因、人工确认、重试、等待和四类质量 finding；未来能力才自动生成报告。
3. 维护者形成 observation，并保留对应结果包/事件的脱敏引用。
4. 维护者把可行动改进转成 proposal，说明影响故事、载体、收益、风险和复现频率。
5. 用户确认后，proposal 才能进入设计、计划或实现变更；确认前不修改源头资产。

### 输出

以下 JSON 是未来目标输出，不是当前完成声明。现役输出是经人工校对的 observation/proposal 文档或 Jira 记录，并引用原结果包：

```json
{
  "ok": true,
  "operation": "feedback_propose",
  "proposals": 3,
  "agentic_next_action": "owner_review"
}
```

### 失败处理

- 缺少事件日志时提示检查工作空间配置。
- 结果包缺少完整 retrospective、引用断链或未脱敏时停止人工分析并退回 developer 工作面补正。
- 发现敏感内容时停止生成报告。
- 重复失败只能形成 proposal，不能自动修改公司规范。

### 验收标准

- 目标能力实现后，能按显式输入的工作空间、时间范围、失败码或任务类型生成反馈报告；当前以单个 task-to-PR 结果包人工处理。
- 现役人工整理与未来报告都必须包含成功、失败、阻塞、人工确认、重试、等待、四类质量审查和重复问题。
- 改进建议经过人工确认后才进入 AgenticOps 源头仓库。

### 保护行为

- developer task-to-PR 结果包和 retrospective 是现役触发材料；反馈报告是未来按需分析工具，两者都不替代 Jira/Git/GitHub 事实。
- 重复失败只能形成 proposal，不能自动修改 AgenticOps 源头规则。
- 报告和建议不得包含 secrets 或敏感原始内容。
- proposal 进入设计、计划或实现前必须经过人工确认。
- maintainer 不得自动发现、读取或继承其它业务工作空间的资料、授权和配置。

### 审核问题

- 报告输入来自哪些事件日志、证据或任务审计记录。
- 输出是否区分 observation、proposal 和 accepted change。
- 是否把“按需分析”误写成每个任务完成后的强制日报。
- 改进建议是否明确影响故事线、设计、契约、配置、策略或代码。

### 验收证据

- 当前能力目录/CLI 证明 `ao-maint feedback ...` 尚未实现，不能伪称成功。
- developer `task_to_pr_review` 结果包中的完整 retrospective、事件引用和人工交接记录。
- 人工整理的 observation 与 proposal 及其脱敏证据引用。
- 未来实现后再补 `ao-maint feedback report|analyze|propose --input-manifest <manifest.json>` 固定输出。
- 人工确认 proposal 的记录。

### 关联设计

- `docs/workflows/feedback-loop.md`
- `docs/runtime/problem-resolution-and-update.md`
- `docs/templates/evidence-templates.md`
- `docs/project-rules.md`
