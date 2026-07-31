# PM-004 诊断问题并选择修复载体

作为项目维护者，
我希望能按问题类型选择正确修复载体，
以便避免把所有问题都升级为二进制修复或临时人工绕过。

### 触发方式

```sh
agentic-cli doctor --workspace <name>
agentic-cli feedback bundle --workspace <name> --run-id <agentic_run_id> --redact
agentic-cli update check
agentic-cli profile validate --workspace <name>
agentic-cli policy validate --workspace <name>
```

### 前置条件

- 已有失败码、事件日志、诊断包或复现步骤。
- 诊断数据已经脱敏。
- 已明确问题影响的是 CLI 逻辑、工作流配置、任务字段、策略门禁还是发布资产。

### 主流程

1. 维护者收集脱敏诊断包。
2. 维护者按失败码和问题分类定位修复载体。
3. CLI 逻辑错误进入版本修复。
4. Jira 流程状态不适配进入 workflow profile 更新。
5. Jira 卡片属性缺失进入补卡模板和阻断说明。
6. 关键步骤门禁调整进入 policy 更新。
7. 发布或安装问题进入 update、release 或 rollback 流程。

### 输出

```json
{
  "ok": true,
  "operation": "classify_problem",
  "problem_type": "workflow_profile_mismatch",
  "repair_carrier": "profile_update",
  "agentic_next_action": "prepare_profile_change"
}
```

### 失败处理

- 诊断包疑似包含敏感内容时停止分析并要求脱敏。
- 问题分类不明确时，先补充事实，不直接改设计或代码。
- 涉及权限、事实源或自动化程度改变时，提示用户决策。

### 验收标准

- 问题能被归入明确修复载体。
- 诊断输出不包含敏感原始内容。
- 修复路径能说明是否需要版本发布、资产热更新、补卡或人工决策。

### 保护行为

- 不把所有问题默认升级为二进制修复。
- 诊断包不得包含 secrets、tokens、private keys、原始 Jira 描述、原始敏感日志或敏感代码片段。
- 问题分类不明确时必须先补事实，不能直接修改设计、契约或代码。
- 涉及权限、事实源或自动化程度变化时必须提示用户决策。

### 审核问题

- 当前问题属于 CLI 逻辑、workflow profile、Jira 卡片属性、policy、release/update 中哪一类。
- 诊断数据是否已脱敏。
- 修复载体是否能解释为什么不是其它路径。
- 修复后是否有对应回归入口。

### 验收证据

- `agentic-cli doctor --workspace <name>` 的结构化输出。
- `agentic-cli feedback bundle --workspace <name> --run-id <agentic_run_id> --redact` 生成的脱敏包。
- `bash tests/e2e/problem-resolution-flow.sh`
- 失败码、问题分类和建议修复载体的输出记录。

### 关联设计

- `docs/runtime/problem-resolution-and-update.md`
- `install-resources/basic/runbooks/problem-resolution.md`
- `docs/workflows/feedback-loop.md`
- `docs/templates/evidence-templates.md`
