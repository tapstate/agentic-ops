# DL-003 初始化 AIAgent 能力

作为研发负责人，
我希望能初始化当前 AIAgent 的 AgenticOps 能力，
以便 AIAgent 知道 AI 员工手册、可用操作、停止条件、人工确认点和工具调用方式。

### 触发方式

```sh
agentic-cli agent init --workspace tapstate
```

或由研发负责人在 AIAgent 会话中输入：

```text
初始化 AgenticOps 能力，工作空间是 tapstate。
```

### 前置条件

- AgenticOps 已安装。
- 项目 AI 工作空间已初始化。
- AIAgent 当前会话可以读取本地文件并调用 `agentic-cli`。

### 主流程

1. AIAgent 读取 AI 员工手册。
2. AIAgent 读取任务类型、阶段和下一步动作规则。
3. AIAgent 读取 工作流配置摘要。
4. AIAgent 读取 操作契约列表。
5. AIAgent 执行 `agentic-cli preflight --workspace tapstate`。
6. AIAgent 向研发负责人输出当前可用能力、阶段判断方式和限制。

### 输出

```json
{
  "ok": true,
  "operation": "agent_init",
  "workspace": "tapstate",
  "task_model": {
    "type_source": "operation_contract",
    "stage_source": "event_log",
    "next_action_source": "operation_result"
  },
  "capabilities": [
    "list_tasks",
    "takeover_task",
    "resume_takeover",
    "write_evidence",
    "prepare_pr",
    "feedback_report"
  ],
  "human_gates": [
    "push",
    "create_pr",
    "merge",
    "scope_change"
  ]
}
```

### 失败处理

- 如果 AIAgent 无法读取手册，停止并提示安装或路径问题。
- 如果 `agentic-cli` 不可用，提示重新安装或修复 PATH。
- 如果 `workspace preflight` 失败，AIAgent 不能开始接管任务。

### 验收标准

- AIAgent 能明确说明任务类型、阶段判断方式和可执行操作。
- AIAgent 能明确说明哪些动作必须人工确认。
- AIAgent 知道不能直接面对 Jira 字段和状态，必须通过操作契约和 CLI 工作。
- 初始化完成后，研发负责人可以直接说“列出我的任务”或“接管 TAP-123”。

### 保护行为

- AIAgent 必须读取 AI 员工手册、操作契约和工作流配置摘要后才能接管任务。
- AIAgent 必须说明人工确认点，包括推送、创建拉取请求、合并、范围变更等。
- AIAgent 不能直接面对 Jira 字段、状态或 `transition` 做临场猜测。
- `workspace preflight` 失败时，AIAgent 不能开始接管任务。

### 审核问题

- AIAgent 是否能说明当前任务类型、阶段来源和下一步动作来源。
- AIAgent 是否知道哪些操作有副作用。
- AIAgent 是否知道何时必须停止并请求研发负责人判断。
- 初始化输出是否足以让研发负责人继续说“列出我的任务”或“接管 TAP-123”。

### 验收证据

- `agentic-cli agent init --workspace <name>` 输出。
- `agentic-cli preflight --workspace <name>` 输出。
- AIAgent 对能力、阶段判断和人工确认点的说明。
- AI 员工手册和操作契约读取记录。

### 关联设计

- `handbooks/ai-employee-handbook.md`
- `docs/ai-working-rules.md`
- `docs/contracts/operation-contract.md`
- `docs/profiles/workflow-profile.md`
