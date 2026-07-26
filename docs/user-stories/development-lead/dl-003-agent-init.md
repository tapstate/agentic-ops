# DL-003 初始化 AIAgent 能力

作为研发负责人，
我希望能初始化当前 AIAgent 的 AgenticOps 能力，
以便 AIAgent 知道 AI 员工手册、可用操作、停止条件、人工确认点和工具调用方式。

### 触发方式

```sh
agentic-cli agent init
```

或由研发负责人在 AIAgent 会话中输入：

```text
按 ~/.agentic-ops/agent-guides.md 启用 AgenticOps。
```

### 前置条件

- AgenticOps 已安装。
- 项目 AI 工作空间已初始化。
- 项目 AI 工作空间中存在 `.agentic-ops/agent.json` 和 `AGENTS.md`。
- AIAgent 当前会话可以读取本地文件并调用 `agentic-cli`。

### 主流程

1. AIAgent 读取 `~/.agentic-ops/agent-guides.md`。
2. AIAgent 读取当前工作空间的 `AGENTS.md` 和 `.agentic-ops/agent.json`。
3. AIAgent 执行 `agentic-cli agent init`，从输出中确认全局指引和本地 AI 资产入口。
4. AIAgent 读取 AI 资产入口、AI 员工手册、任务类型、阶段和下一步动作规则。
5. AIAgent 读取工作流配置摘要和操作契约列表。
6. AIAgent 执行 `agentic-cli preflight`。
7. AIAgent 向研发负责人输出当前可用能力、阶段判断方式和限制。

AIAgent 不得依赖研发负责人个人 Obsidian wiki、长期记忆或上一段聊天上下文完成初始化。初始化事实源必须来自当前项目 AI 工作空间和 `~/.agentic-ops/install-resources/basic/` 中的已安装资产。

### 输出

```json
{
  "ok": true,
  "operation": "agent_init",
  "workspace": "tapstate",
  "task_type": "capability_initialization",
  "current_stage": "agent_capability_initialized",
  "next_action": "list_tasks",
  "activation_phrase": "按 ~/.agentic-ops/agent-guides.md 启用 AgenticOps。",
  "guide_entry": "$HOME/.agentic-ops/agent-guides.md",
  "asset_entry": "$HOME/.agentic-ops/install-resources/basic/ai-assets/README.md",
  "instruction_source": "agent_guides_and_workspace_state",
  "memory_dependency": false,
  "capabilities": [
    "preflight",
    "list_tasks",
    "task_run",
    "takeover_task",
    "resume_takeover",
    "write_evidence",
    "branch_align",
    "prepare_pr",
    "feedback_report"
  ],
  "human_gates": [
    "real_jira_write",
    "git_push",
    "create_pr",
    "update_pr",
    "merge",
    "release",
    "scope_change"
  ],
  "next_steps": [
    "read_guide_entry",
    "read_asset_entry",
    "run_preflight",
    "list_tasks"
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
- 研发负责人只说“按 `~/.agentic-ops/agent-guides.md` 启用 AgenticOps。”时，新 AIAgent 能基于全局指引、本地 `AGENTS.md`、`.agentic-ops/agent.json` 和安装资产初始化，不依赖个人 wiki。

### 保护行为

- AIAgent 必须读取全局指引、本地 AI 资产入口、AI 员工手册、操作契约和工作流配置摘要后才能接管任务。
- AIAgent 必须说明人工确认点，包括推送、创建拉取请求、合并、范围变更等。
- AIAgent 不能直接面对 Jira 字段、状态或 `transition` 做临场猜测。
- `workspace preflight` 失败时，AIAgent 不能开始接管任务。

### 审核问题

- AIAgent 是否能说明当前任务类型、阶段来源和下一步动作来源。
- AIAgent 是否知道哪些操作有副作用。
- AIAgent 是否知道何时必须停止并请求研发负责人判断。
- 初始化输出是否足以让研发负责人继续说“列出我的任务”或“接管 TAP-123”。

### 验收证据

- `agentic-cli agent init` 输出。
- `agentic-cli preflight` 输出。
- AIAgent 对能力、阶段判断和人工确认点的说明。
- AI 员工手册和操作契约读取记录。

### 关联设计

- `install-resources/basic/handbooks/ai-employee-handbook.md`
- `docs/ai-working-rules.md`
- `docs/contracts/operation-contract.md`
- `docs/profiles/workflow-profile.md`
