# AgenticOps AIAgent 启动指引

本文是安装后 AIAgent 的全局启动入口。研发工程师在项目 AI 工作空间启动 AIAgent 后，应发送：

```text
按 ~/.agentic-ops/agent-guides.md 启用 AgenticOps。
```

收到该指令后，AIAgent 必须先读取本文，再读取当前项目 AI 工作空间中的本地配置和已安装 AI 资产。

## 读取顺序

1. 确认当前目录是项目 AI 工作空间，不是 `~/.agentic-ops`，也不是 `tapstate/agentic-ops` 源头仓库。
2. 读取当前目录的 `.agentic-ops/agent.json`，确认 `workspace`、`project`、`jira_user`、`jira_project` 和本地 `profile`。
3. 执行 `agentic-cli agent init`，确认输出中的 `guide_entry`、`asset_entry`、`memory_dependency=false`、`human_gates` 和 `next_steps`。
4. 读取 `~/.agentic-ops/install-resources/basic/ai-assets/README.md`。
5. 执行 `agentic-cli profile resolve --project <project>`，读取 effective profile、配置来源和项目资产路径。
6. 执行 `agentic-cli preflight`。预检失败时停止接管任务，并把缺失配置、权限或路径问题说明给研发工程师。

## 工具入口

不同 AIAgent 工具会读取不同的本地指引文件。项目 AI 工作空间初始化后，根目录工具指引文件只作为适配器，最终都应回到本文和本地 `.agentic-ops/agent.json`。

- Codex：优先读取当前目录 `AGENTS.md`。
- Claude：优先读取当前目录 `CLAUDE.md`；如果不存在，则按本文和 `.agentic-ops/agent.json` 初始化。
- Gemini：优先读取当前目录 `GEMINI.md`；如果不存在，则按本文和 `.agentic-ops/agent.json` 初始化。
- 其它 AIAgent：直接按本文初始化。

## 边界

- 不依赖研发工程师个人 Obsidian wiki、个人长期记忆或上一段聊天上下文。
- 不临场猜测 Jira 字段、目标仓库、工作流状态或证据格式。
- 接管具体任务前先执行 `inspect-task`，再按项目准入资产判断；CLI 不替代项目业务判断。
- 不在初始化阶段执行真实 Jira 写操作、Git 推送、创建或更新拉取请求、合并、发布或范围变更。
- 具体项目状态只读取当前项目 AI 工作空间，不能写死到本文。
