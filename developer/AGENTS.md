# AgenticOps 研发工作 AI 入口

本入口固定属于 `developer` 工作面，用于业务项目工作空间代表的研发员执行获得授权的研发工作。

- 首次初始化、授权或恢复时，使用目标安装的绝对入口 `<install-root>/bin/ao-work`；初始化成功后的业务任务入口固定为 `./.agentic-ops/bin/ao-work`，不得搜索 PATH 或其它安装目录。
- 规则入口：`developer/rules/`。
- Skill 入口：`developer/skills/`。
- 能力事实入口：`ao-work capability list|show` 与 `developer/standards/capabilities/operations.yaml`。
- 授权、配置和状态：只能使用当前业务项目工作空间 `.agentic-ops/` 及 developer 安装资产。
- 源码（D-048 池模式）：业务源码在中央克隆池 `<source_pool_root>`（研发员级配置 `~/.agentic-ops/user/config.yaml`）；任务执行源码挂在 `<source_pool_root>/<JIRA-KEY>/<from_branch>/<repo>` 任务工作树集，分支由 Project Profile `branches` 推导，per-worktree 身份写入 worktree config。工作树内禁止直接 fetch，统一在池成员（`<source_pool_root>/<owner>/<repo>`）执行。

禁止加载 `maintainer/AGENTS.md`、AgenticOps 源头设计红线、源头发布规则、维护授权或维护状态。业务任务发现能力不足时，只生成脱敏反馈并由人工交接给项目维护者，不得从业务工作面直接修改 AgenticOps 源头。

调用任何标准操作前，AI 必须先查询能力目录。只有 `status=implemented` 且目录明确给出当前命令路径的能力可以调用；`status=capability_gap`、目录缺失、目录无效或仅存在 Operation Contract 时都必须停止自动化，按中文 `next_action` 处理。Operation Contract 表示目标行为边界，不单独证明 Runtime 已实现。`visibility=internal` 的 `task`、`report` 命令只允许由版本化 Skill 编排，不能向用户描述成任务接管、完成审计或 Jira 回写能力。

研发工程师说“接管 <KEY>”时统一调用 `./.agentic-ops/bin/ao-work takeover <KEY>`；不带 KEY 时只读列出候选并等待研发工程师选择。Runtime 内部绑定本次明确接管指令，根据 Jira 状态和本地任务状态自动判断新接管、接纳存量任务或恢复已有运行，不要求用户理解多级命令或授权参数。不是新接管时，Jira 中文接管评论必须明文提示“不是新接管”。developer 不把 Agentic 运行信息映射到 Jira Custom Field：Jira Comment 记录接管、恢复、进度、证据和终止轨迹，Status/Assignee 记录团队阶段与负责人，本地 task state 记录运行、恢复和幂等事实。

工作面由本入口确定。AI 不得自行判断、推断或通过命令参数切换工作面。
