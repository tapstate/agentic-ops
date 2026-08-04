# DE-004 新任务接管

作为研发工程师，
我希望能让 AIAgent 接管一个新的 Jira 卡片，
以便 AI 员工在完成门禁后开始读取上下文、制定计划、开发、验证并回写证据。

### 触发方式

```sh
agentic-cli takeover-task TAP-123
```

或自然语言：

```text
接管 TAP-123。
```

### 前置条件

- AIAgent 能力已初始化。
- Jira 卡片已进入迭代。
- 当前 Jira 用户和卡片负责人匹配。
- AIAgent 已通过 `inspect-task` 读取 Jira 事实和项目资产。
- AIAgent 已按项目准入资产确认卡片满足接管要求。

### 主流程

1. AIAgent 调用 `inspect-task`，按项目资产判断准入。
2. 准入不足时，AIAgent 结合 Jira 和代码形成补卡建议，写入 Jira 后结束本次接管；补卡确认后更新 Description 并再次结束。
3. 后续执行重新调用 `inspect-task`。准入通过后，AIAgent 调用 `takeover_task`。
4. CLI 执行负责人、代理所有权、任务分类、标准流程、状态入口和真实 Jira 写入门禁。
5. 门禁通过后，CLI 生成 `agentic_run_id` 并绑定当前 AIAgent。
6. AIAgent 读取目标仓库上下文，形成版本化修复计划并写入 Jira Comment。
7. 研发工程师确认计划，AIAgent 把确认结果写入 Jira Comment。
8. 确认结果写入后，AIAgent 才能在允许范围内修改代码并运行验证。
9. AIAgent 更新结构化 Jira 字段并写入最终证据 Comment。
10. AIAgent 停在人工确认点，等待研发工程师确认提交、推送或创建拉取请求。

### 输出

```json
{
  "ok": true,
  "operation": "takeover_task",
  "workspace": "tapstate",
  "issue_key": "TAP-123",
  "agentic_run_id": "TAP-123-takeover-20260721103012-a8f3",
  "task_type": "task_takeover",
  "current_stage": "takeover_started",
  "target_repo": "tapstate/example-repo",
  "agentic_next_action": "proceed"
}
```

### 失败处理

- 负责人不匹配时，停止，不写开发证据。
- 项目准入信息不足时，不调用 `takeover-task`，先完成代码分析和 Jira 补卡闭环。
- 权限不足时，返回 `missing_permission`。
- 风险边界不清时，要求人工确认。

### 验收标准

- 单次任务接管只处理一个 Jira 卡片。
- 接管成功和失败都有结构化记录。
- 每次接管都有唯一 `agentic_run_id`。
- AIAgent 未经确认不得推送或创建拉取请求。
- 所有操作都写入结构化事件日志。
- 写入 Jira 的接管成功、失败、阻塞和补卡说明必须使用中文。

### 保护行为

- 单次接管只能处理一个 Jira 卡片。
- CLI 接管必须检查负责人、代理所有权、任务分类、标准流程、状态入口和真实 Jira 写入权限；项目准入由 AIAgent 按项目资产判断。
- 修复计划和研发工程师确认结果未写入 Jira 前，不得修改代码。
- 接管成功必须生成唯一 `agentic_run_id` 并写入结构化事件日志。
- 接管失败必须写结构化失败记录，不能继续开发。
- 未经研发工程师独立确认或授予仍有效的工作项级连续执行授权，AIAgent 不得推送或创建拉取请求。确认版本化设计或修复计划后，授权范围内可以连续推进到拉取请求审查；合并、发布、范围变化和授权失效仍需新的人工确认。

### 审核问题

- 当前 Jira 卡片是否已进入迭代并由当前研发工程师负责。
- 卡片是否已由 AIAgent 按项目资产完成准入检查。
- 准入分析、补卡确认、修复计划和计划确认是否分别写入 Jira。
- `target_repo` 是来自字段映射还是 workflow profile。
- 接管失败时是否清楚提示 required human action。
- 接管后是否停在正确的下一步，而不是绕过人工门禁。

### 验收证据

- `agentic-cli takeover-task <issue> --workspace <name>` 输出。
- `agentic_run_id` 对应的事件日志。
- Jira 中文接管成功、失败、阻塞或补卡说明。
- `bash tests/e2e/local-fake-flow.sh`
- 真实 Jira 卡片端到端演示记录。

### 关联设计

- `docs/contracts/operation-contract.md`
- `docs/processes/standard-process-registry.md`
- `docs/templates/evidence-templates.md`
- `docs/examples/end-to-end-demo.md`
- `docs/forms/task-form-standard.md`
