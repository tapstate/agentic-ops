# 证据模板

## 1. 目的

证据模板定义 AgenticOps 写入 Jira、拉取请求和本地证据的标准格式。它们用于让任务接管、失败、阻塞、开发完成和反馈建议可追踪、可复盘。

当前阶段只设计模板内容，不创建运行时模板文件。

## 2. 模板规则

证据必须：

- 关联 Jira 卡片编号。
- 关联 workspace。
- 关联 `agentic_run_id`。
- 关联任务类型、当前阶段和下一步动作。
- 说明当前阶段。
- 说明下一步。
- 说明当前节点已输出或更新的标准表单字段。
- 说明专业审查结论、重试依据或重做起点。
- 写入 Jira 的标题、描述、评论、工作日志、证据正文、阻塞说明和补卡说明必须使用中文。
- 不包含 secrets、tokens、private keys、原始敏感日志、完整 Jira 描述或敏感代码片段。

## 3. 任务接管成功

```markdown
## 任务接管成功

- 事项: `<issue-key>`
- 工作空间: `<workspace>`
- 研发工程师: `<owner>`
- 任务类型: `task_takeover`
- 运行 ID: `<agentic_run_id>`
- 目标仓库: `<target_repo>`
- 当前阶段: `takeover_started`
- 下一步: `<agentic_next_action>`

### 执行计划

1. `<step-1>`
2. `<step-2>`

### 验证方式

- `<verification-command>`

### 下一步

AI 员工将读取目标仓库上下文并开始本地开发。未经研发工程师确认版本化设计或修复计划并授予工作项级连续执行授权，不会推送或创建拉取请求。
```

## 4. 任务接管失败

```markdown
## 任务接管失败

- 事项: `<issue-key>`
- 工作空间: `<workspace>`
- 任务类型: `task_takeover`
- 运行 ID: `<agentic_run_id>`
- 当前阶段: `takeover_gate`
- 失败码: `<code>`
- 下一步: `<agentic_next_action>`
- 可重试: `<retryable>`
- 重做起点: `<redo_from_stage>`

### 失败原因

`<safe-message>`

### 需要人工补充

1. `<required-human-action>`

### 下一步

请研发工程师补充信息后重新触发接管或恢复接管。
```

## 5. 阻塞

```markdown
## AI 执行阻塞

- 事项: `<issue-key>`
- 工作空间: `<workspace>`
- 任务类型: `<task_type>`
- 运行 ID: `<agentic_run_id>`
- 当前阶段: `<current_stage>`
- 阻塞码: `<code>`
- 下一步: `<agentic_next_action>`
- 可重试: `<retryable>`
- 重做起点: `<redo_from_stage>`

### 阻塞原因

`<safe-message>`

### 已完成动作

- `<completed-action>`

### 需要人工处理

- `<required-human-action>`
```

## 6. 工作项连续执行授权

```markdown
## 工作项连续执行授权

- 事项: `<issue-key>`
- 工作空间: `<workspace>`
- 任务类型: `task_takeover`
- 运行 ID: `<agentic_run_id>`
- Agent ID: `<agent-id>`
- Agentic ID: `<agentic-id>`
- 目标仓库: `<target-repo>`
- 工作分支: `<work-branch>`
- 目标分支: `<base-branch>`
- 计划版本: `<approved-plan-version>`
- 授权引用: `<authorization-reference>`
- 当前阶段: `implementation`
- 下一步: `continue_to_pr_review`

### 已确认范围

- `<approved-scope>`

### 验证方式

- `<verification-method>`

### 明确非范围

- `<excluded-scope>`

### 授权动作

- 实现、验证和提交。
- 推送任务分支。
- 必要 Jira 回写。
- 创建或更新拉取请求。

### 失效条件

- 所有权或绑定事实变化。
- 范围或风险扩大。
- 必要验证受阻或连续失败。
- 外部写入结果不明确。
```

## 7. 拉取请求审查包

```markdown
## 拉取请求审查包

- 事项: `<issue-key>`
- 工作空间: `<workspace>`
- 运行 ID: `<agentic_run_id>`
- 授权引用: `<authorization-reference>`
- 拉取请求: `<pr-url>`
- Base: `<base-branch>`
- Head: `<work-branch>`
- 固定 Head SHA: `<fixed-head-sha>`
- 当前阶段: `pr_review`
- 下一步: `<agentic_next_action>`

### 变更与提交

- `<change-summary>`
- `<commit-summary>`

### 验证与审查事实

- `<verification-result>`
- `<ci-fact>`
- `<review-fact>`

### Jira 回写

- `<jira-writeback-reference>`

### 残留风险

- `<residual-risk>`

### 需要人工处理

- `<required-human-action>`
```

## 8. 专业审查退回

```markdown
## 专业审查退回

- 事项: `<issue-key>`
- 工作空间: `<workspace>`
- 任务类型: `<task_type>`
- 运行 ID: `<agentic_run_id>`
- 审查节点: `<review-gate>`
- 审查角色: `<review-role>`
- 当前阶段: `<current_stage>`
- 下一步: `<agentic_next_action>`
- 重做起点: `<redo_from_stage>`

### 审查结论

`<reviewer-decision>`

### 需要处理

- `<reviewer-required-action>`

### 下一步

AI 员工将按审查结论修复、重新验证，并在必要时重做受影响阶段表单。
```

## 9. 反馈建议

```markdown
## AgenticOps 改进建议

- 工作空间: `<workspace>`
- 分析范围: `<feedback-scope>`
- 来源: `feedback-analysis`

### 观察

- `<observation>`

### 建议

- `<proposal>`

### 影响范围

- `<affected-doc-or-contract>`

### 决策要求

需要人工确认后才能修改 AgenticOps 源头规则。
```
