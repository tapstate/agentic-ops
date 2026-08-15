# AgenticOps 研发工作 AI 入口

本入口固定属于 `developer` 工作面，用于业务项目工作空间代表的研发员执行获得授权的研发工作。

- 命令入口：安装后的 `ao-work`。
- 规则入口：`developer/rules/`。
- Skill 入口：`developer/skills/`。
- 能力事实入口：`ao-work capability list|show` 与 `developer/standards/capabilities/operations.yaml`。
- 授权、配置和状态：只能使用当前业务项目工作空间 `.agentic-ops/` 及 developer 安装资产。

禁止加载 `maintainer/AGENTS.md`、AgenticOps 源头设计红线、源头发布规则、维护授权或维护状态。业务任务发现能力不足时，只生成脱敏反馈并由人工交接给项目维护者，不得从业务工作面直接修改 AgenticOps 源头。

调用任何标准操作前，AI 必须先查询能力目录。只有 `status=implemented` 且目录明确给出当前命令路径的能力可以调用；`status=capability_gap`、目录缺失、目录无效或仅存在 Operation Contract 时都必须停止自动化，按中文 `next_action` 处理。Operation Contract 表示目标行为边界，不单独证明 Runtime 已实现。`visibility=internal` 的 `task`、`report` 命令只允许由版本化 Skill 编排，不能向用户描述成任务接管、完成审计或 Jira 回写能力。

工作面由本入口确定。AI 不得自行判断、推断或通过命令参数切换工作面。
