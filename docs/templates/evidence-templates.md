# 证据模板

## 1. 目的

Evidence Templates 定义 AgenticOps 写入 Jira / PR / 本地 evidence 的标准格式。它们用于让任务接管、失败、阻塞、开发完成和反馈建议可追踪、可复盘。

当前阶段只设计模板内容，不创建运行时模板文件。

## 2. 模板规则

Evidence 必须：

- 关联 issue key。
- 关联 workspace。
- 关联 `run_id`。
- 关联任务类型、当前阶段和下一步动作。
- 说明当前阶段。
- 说明下一步。
- 不包含 secrets、tokens、private keys、原始敏感日志、完整 Jira 描述或敏感代码片段。

## 3. 任务接管成功

```markdown
## 任务接管成功

- 事项: `<issue-key>`
- 工作空间: `<workspace>`
- 研发 owner: `<owner>`
- 任务类型: `task_takeover`
- 运行 ID: `<run_id>`
- 目标仓库: `<target_repo>`
- 当前阶段: `takeover_started`
- 下一步: `<next_action>`

### 执行计划

1. `<step-1>`
2. `<step-2>`

### 验证方式

- `<verification-command>`

### 下一步

AI 员工将读取目标仓库上下文并开始本地开发。未经研发 owner 确认，不会 push 或创建 PR。
```

## 4. 任务接管失败

```markdown
## 任务接管失败

- 事项: `<issue-key>`
- 工作空间: `<workspace>`
- 任务类型: `task_takeover`
- 运行 ID: `<run_id>`
- 当前阶段: `takeover_gate`
- 失败码: `<code>`
- 下一步: `<next_action>`

### 失败原因

`<safe-message>`

### 需要人工补充

1. `<required-human-action>`

### 下一步

请研发 owner 补充信息后重新触发接管或恢复接管。
```

## 5. 阻塞

```markdown
## AI 执行阻塞

- 事项: `<issue-key>`
- 工作空间: `<workspace>`
- 任务类型: `<task_type>`
- 运行 ID: `<run_id>`
- 当前阶段: `<current_stage>`
- 阻塞码: `<code>`
- 下一步: `<next_action>`

### 阻塞原因

`<safe-message>`

### 已完成动作

- `<completed-action>`

### 需要人工处理

- `<required-human-action>`
```

## 6. 本地开发完成

```markdown
## 本地开发完成

- 事项: `<issue-key>`
- 工作空间: `<workspace>`
- 任务类型: `task_takeover`
- 运行 ID: `<run_id>`
- 当前阶段: `development_completed`
- 下一步: `request_owner_confirmation`

### 变更摘要

- `<change-summary>`

### 验证结果

- `<verification-result>`

### 残留风险

- `<residual-risk>`

### 人工确认

等待研发 owner 确认是否允许 push / PR。
```

## 7. 反馈建议

```markdown
## AgenticOps 改进建议

- 工作空间: `<workspace>`
- 日期: `<yyyy-mm-dd>`
- 来源: `daily-feedback`

### 观察

- `<observation>`

### 建议

- `<proposal>`

### 影响范围

- `<affected-doc-or-contract>`

### 决策要求

需要人工确认后才能修改 AgenticOps 源头规则。
```
