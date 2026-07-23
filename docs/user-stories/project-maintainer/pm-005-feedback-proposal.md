# PM-005 处理反馈并形成改进建议

作为项目维护者，
我希望能从任务执行记录中分析重复失败、阻塞点和人工确认点，
以便把有效经验沉淀为 AgenticOps 改进建议。

### 触发方式

```sh
agentic-cli feedback report --workspace <name> --date 2026-07-23
agentic-cli feedback analyze --workspace <name> --date 2026-07-23
agentic-cli feedback propose --workspace <name> --date 2026-07-23
```

### 前置条件

- 工作空间已有任务级事件日志、证据或审计记录。
- 反馈数据已经脱敏。
- 改进建议不会未经人工确认直接修改源头仓库。

### 主流程

1. 维护者生成反馈报告。
2. 维护者聚合失败码、阻塞原因和人工确认点。
3. 维护者形成 observation。
4. 维护者把可行动改进转成 proposal。
5. 用户确认后，proposal 才能进入设计、计划或实现变更。

### 输出

```json
{
  "ok": true,
  "operation": "feedback_propose",
  "proposals": 3,
  "next_action": "owner_review"
}
```

### 失败处理

- 缺少事件日志时提示检查工作空间配置。
- 发现敏感内容时停止生成报告。
- 重复失败只能形成 proposal，不能自动修改公司规范。

### 验收标准

- 能按工作空间、时间范围、失败码或任务类型生成反馈报告。
- 报告包含成功、失败、阻塞、人工确认点和重复问题。
- 改进建议经过人工确认后才进入 AgenticOps 源头仓库。

### 保护行为

- 反馈报告是按需分析工具，不替代任务级审计记录。
- 重复失败只能形成 proposal，不能自动修改 AgenticOps 源头规则。
- 报告和建议不得包含 secrets 或敏感原始内容。
- proposal 进入设计、计划或实现前必须经过人工确认。

### 审核问题

- 报告输入来自哪些事件日志、证据或任务审计记录。
- 输出是否区分 observation、proposal 和 accepted change。
- 是否把“按需分析”误写成每个任务完成后的强制日报。
- 改进建议是否明确影响故事线、设计、契约、配置、策略或代码。

### 验收证据

- `agentic-cli feedback report --workspace <name> --date <date>` 输出。
- `agentic-cli feedback analyze --workspace <name> --date <date>` 输出。
- `agentic-cli feedback propose --workspace <name> --date <date>` 输出。
- 人工确认 proposal 的记录。

### 关联设计

- `docs/workflows/feedback-loop.md`
- `docs/runtime/problem-resolution-and-update.md`
- `docs/templates/evidence-templates.md`
- `docs/project-rules.md`
