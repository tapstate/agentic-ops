# AgenticOps 研发工作 AI 入口

本入口固定属于 `developer` 工作面，用于业务项目工作空间代表的研发员执行获得授权的研发工作。

- 首次初始化、授权或恢复时，使用目标安装的绝对入口 `<install-root>/bin/ao-work`；初始化成功后的业务任务入口固定为 `./.agentic-ops/bin/ao-work`，不得搜索 PATH 或其它安装目录。
- 如需为整个 `ao-work` 入口设置持久命令前缀授权，请直接发给 AI：“请为 `./.agentic-ops/bin/ao-work` 申请持久命令前缀授权，覆盖其所有子命令。”
- 规则入口：`developer/rules/`。
- Skill 入口：`developer/skills/`。
- 能力事实入口：`ao-work capability list|show` 与 `developer/standards/capabilities/operations.yaml`。
- 授权、配置和状态：只能使用当前业务项目工作空间 `.agentic-ops/` 及 developer 安装资产。
- 源码（D-048/AO-92 池模式）：业务源码池成员是保留主工作树的普通 clone，只允许作为 Profile 默认分支固定 SHA 的分析源；AIAgent 不在池成员主工作树修改代码、建任务分支或提交。分析仅生成 `proposed_repository_branch_map`，必须向研发工程师展示问题版本和完整逐仓关系并允许修正；只有明确确认的 `confirmed_repository_branch_map` 是后续依据。实际需要修改某仓库时，才在 `<source_pool_root>/.worktree/<JIRA-KEY>/<repo-short-name>/<normalized-from-branch>` 按需创建子工作树。TapData TM、FE、connector 的问题版本来源统一是 `tapdata/tapdata`，逐仓建议由 remote-only 对齐计划生成，但用户确认结果优先。工作树内禁止直接 fetch；新增仓库须重新确认范围。完成评论逐仓汇报 `actual_change_repositories`，Jira 完成态和评论回读后才可非强制清理登记工作树；源码池成员永不随任务清理。

禁止加载 `maintainer/AGENTS.md`、AgenticOps 源头设计红线、源头发布规则、维护授权或维护状态。业务任务发现能力不足时，只生成脱敏反馈并由人工交接给项目维护者，不得从业务工作面直接修改 AgenticOps 源头。

调用任何标准操作前，AI 必须先查询能力目录。只有 `status=implemented` 且目录明确给出当前命令路径的能力可以调用；`status=capability_gap`、目录缺失、目录无效或仅存在 Operation Contract 时都必须停止自动化，按中文 `next_action` 处理。Operation Contract 表示目标行为边界，不单独证明 Runtime 已实现。`visibility=internal` 的 `task`、`report` 命令只允许由版本化 Skill 编排，不能向用户描述成任务接管、完成审计或 Jira 回写能力。

研发工程师说“接管 <KEY>”时统一调用 `./.agentic-ops/bin/ao-work takeover <KEY>`；不带 KEY 时只读列出候选并等待研发工程师选择。Runtime 内部绑定本次明确接管指令，根据 Jira 状态和本地任务状态自动判断新接管、接纳存量任务或恢复已有运行，不要求用户理解多级命令或授权参数。不是新接管时，Jira 中文接管评论必须明文提示“不是新接管”。developer 不把 Agentic 运行信息映射到 Jira Custom Field：Jira Comment 记录接管、恢复、进度、证据和终止轨迹，Status/Assignee 记录团队阶段与负责人，本地 task state 记录运行、恢复和幂等事实。

工作面由本入口确定。AI 不得自行判断、推断或通过命令参数切换工作面。
