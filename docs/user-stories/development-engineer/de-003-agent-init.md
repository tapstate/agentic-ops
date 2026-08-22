# DE-003 初始化 AIAgent 能力

> **目标故事合同。** 本文不维护当前完成度；执行前必须以 `ao-work capability list|show` 为准。以下流程和输出描述目标验收行为，不证明现役入口存在。

作为研发工程师，
我希望能初始化当前 AIAgent 的 AgenticOps 能力，
以便 AIAgent 知道 AI 员工手册、可用操作、停止条件、人工确认点和工具调用方式。

### 触发方式

```sh
ao-work capability show agent_init
```

或由研发工程师在 AIAgent 会话中输入：

```text
按当前业务项目工作空间 AGENTS.md 启用 AgenticOps。
```

### 前置条件

- AgenticOps 已安装。
- 项目 AI 工作空间已初始化。
- 项目 AI 工作空间中存在 `.agentic-ops/agent.json` 和 `AGENTS.md`。
- AIAgent 当前会话可以读取本地文件并调用 `ao-work`。

### 主流程

1. AIAgent 读取当前工作空间的 `AGENTS.md` 和 `.agentic-ops/agent.json`。
2. AIAgent 从固定引用加载安装目录中的 `developer/AGENTS.md`、developer Skill、Rule 和标准资产。
3. AIAgent 执行 `ao-work capability show agent_init`；若返回 `capability_gap`，按中文 `next_action` 停止，不得模拟目标输出。
4. AIAgent 读取任务类型、阶段、下一步动作和停止规则。
5. AIAgent 读取工作流配置摘要和操作契约列表。
6. AIAgent 向研发工程师输出当前可用能力、阶段判断方式和限制。

AIAgent 不得依赖研发工程师个人 Obsidian wiki、长期记忆或上一段聊天上下文完成初始化。初始化事实源必须来自当前项目 AI 工作空间和 `~/.agentic-ops/developer/` 中的已安装资产；不得加载 `maintainer/` 或根源头维护规则。

### 输出

```json
{
  "ok": true,
  "operation": "agent_init",
  "workspace": "tapstate",
  "task_type": "capability_initialization",
  "current_stage": "agent_capability_initialized",
  "agentic_next_action": "list_tasks",
  "workplane": "developer",
  "guide_entry": "<project-ai-workspace>/AGENTS.md",
  "asset_entry": "$HOME/.agentic-ops/developer/AGENTS.md",
  "instruction_source": "workspace_and_developer_assets",
  "memory_dependency": false,
  "capabilities": [
    "preflight",
    "list_tasks",
    "inspect_task",
    "add_task_comment",
    "update_task_description_sections",
    "update_task_form",
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
    "list_tasks"
  ]
}
```

### 失败处理

- 如果 AIAgent 无法读取手册，停止并提示安装或路径问题。
- 如果 `ao-work` 不可用，提示重新安装或修复 PATH。
- 如果 `.agentic-ops/agent.json`、`.agentic-ops/profile.local.yaml`、`AGENTS.md` 管理块或 `source_root` 缺失，`agent init` 返回 `workspace_initialization_incomplete`，AIAgent 必须引导研发工程师重新运行 `workspace init`。

### 验收标准

- AIAgent 能明确说明任务类型、阶段判断方式和可执行操作。
- AIAgent 能明确说明哪些动作必须人工确认。
- AIAgent 知道不能直接面对 Jira 字段和状态，必须通过操作契约和 CLI 工作。
- 初始化完成后，研发工程师可以直接说“列出我的任务”或“接管 TAP-123”。
- 研发工程师只说“按当前业务项目工作空间 `AGENTS.md` 启用 AgenticOps。”时，新 AIAgent 能基于本地 `AGENTS.md`、`.agentic-ops/agent.json` 和安装资产初始化，不依赖个人 wiki。

### 保护行为

- AIAgent 必须读取全局指引、本地 AI 资产入口、AI 员工手册、操作契约和工作流配置摘要后才能接管任务。
- AIAgent 必须说明人工确认点，包括推送、创建拉取请求、合并、范围变更等。
- AIAgent 不能直接面对 Jira 字段、状态或 `transition` 做临场猜测。

### 审核问题

- AIAgent 是否能说明当前任务类型、阶段来源和下一步动作来源。
- AIAgent 是否知道哪些操作有副作用。
- AIAgent 是否知道何时必须停止并请求研发工程师判断。
- 初始化输出是否足以让研发工程师继续说“列出我的任务”或“接管 TAP-123”。

### 验收证据

- `ao-work capability show agent_init` 输出的当前状态；能力实现后再补目标命令输出。
- AIAgent 对能力、阶段判断和人工确认点的说明。
- developer AI 入口和操作契约读取记录。

### 关联设计

- `developer/AGENTS.md`
- `docs/ai-working-rules.md`
- `docs/contracts/operation-contract.md`
- `docs/profiles/workflow-profile.md`
