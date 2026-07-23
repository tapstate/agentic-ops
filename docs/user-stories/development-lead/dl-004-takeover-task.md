# DL-004 新任务接管

作为研发负责人，
我希望能让 AIAgent 接管一个新的 Jira 卡片，
以便 AI 员工在完成门禁后开始读取上下文、制定计划、开发、验证并回写证据。

### 触发方式

```sh
agentic-cli takeover-task TAP-123 --workspace tapstate
```

或自然语言：

```text
接管 TAP-123。
```

### 前置条件

- AIAgent 能力已初始化。
- Jira 卡片已进入迭代。
- 当前 Jira 用户和卡片负责人匹配。
- Jira 卡片具备需求范围、验收标准、目标仓库和验证方式。

### 主流程

1. AIAgent 调用 `takeover_task` 操作。
2. CLI 执行负责人、迭代、需求、风险、目标仓库、验证方式和权限门禁。
3. 门禁通过后，CLI 生成 `run_id`。
4. CLI 写入接管成功证据。
5. CLI 返回目标仓库、验证命令、任务摘要和下一步。
6. AIAgent 读取目标仓库上下文。
7. AIAgent 输出开发计划和风险点。
8. AIAgent 在允许范围内修改代码。
9. AIAgent 运行最小验证。
10. AIAgent 回写开发证据。
11. AIAgent 停在人工确认点，等待研发负责人确认推送或创建拉取请求。

### 输出

```json
{
  "ok": true,
  "operation": "takeover_task",
  "workspace": "tapstate",
  "issue_key": "TAP-123",
  "run_id": "TAP-123-takeover-20260721103012-a8f3",
  "task_type": "task_takeover",
  "current_stage": "takeover_started",
  "target_repo": "tapstate/example-repo",
  "next_action": "proceed"
}
```

### 失败处理

- 负责人不匹配时，停止，不写开发证据。
- 缺少验收标准、目标仓库或验证方式时，写接管失败证据。
- 权限不足时，返回 `missing_permission`。
- 风险边界不清时，要求人工确认。

### 验收标准

- 单次任务接管只处理一个 Jira 卡片。
- 接管成功和失败都有结构化记录。
- 每次接管都有唯一 `run_id`。
- AIAgent 未经确认不得推送或创建拉取请求。
- 所有操作都写入结构化事件日志。
- 写入 Jira 的接管成功、失败、阻塞和补卡说明必须使用中文。

### 保护行为

- 单次接管只能处理一个 Jira 卡片。
- 接管必须检查负责人、迭代、需求范围、验收标准、目标仓库、验证方式、风险和权限门禁。
- 接管成功必须生成唯一 `run_id` 并写入结构化事件日志。
- 接管失败必须写结构化失败记录，不能继续开发。
- 未经研发负责人确认，AIAgent 不得推送或创建拉取请求。

### 审核问题

- 当前 Jira 卡片是否已进入迭代并由当前研发负责人负责。
- 卡片是否具备需求范围、验收标准、目标仓库和验证方式。
- `target_repo` 是来自字段映射还是 workflow profile。
- 接管失败时是否清楚提示 required human action。
- 接管后是否停在正确的下一步，而不是绕过人工门禁。

### 验收证据

- `agentic-cli takeover-task <issue> --workspace <name>` 输出。
- `run_id` 对应的事件日志。
- Jira 中文接管成功、失败、阻塞或补卡说明。
- `bash tests/e2e/local-fake-flow.sh`
- 真实 Jira 卡片端到端演示记录。

### 关联设计

- `docs/contracts/operation-contract.md`
- `docs/processes/standard-process-registry.md`
- `docs/templates/evidence-templates.md`
- `docs/examples/end-to-end-demo.md`
- `docs/forms/task-form-standard.md`
