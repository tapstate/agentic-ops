# AgenticOps 项目维护 AI 入口

本入口固定属于 `maintainer` 工作面，只维护、测试、发布和演进 `tapstate/agentic-ops` 源头项目。

- 命令入口：仓库内 `maintainer/bin/ao-maint`。
- 规则入口：`maintainer/rules/`。
- Skill 入口：`maintainer/skills/`。
- 授权、配置和状态：只能使用 `maintainer/` 命名空间及源头仓库事实。

禁止加载 `developer/AGENTS.md`、业务项目工作空间授权、业务任务状态或 developer 专用 Skill。需要验证研发工作面时，只能由维护测试把 `ao-work` 当作黑盒，并使用测试前明确确认的输入清单；不得导入 developer Runtime 或继承现有本机业务身份。

工作面由本入口确定。AI 不得自行判断、推断或通过命令参数切换工作面。

处理 AO Jira 工作项时，用户操作统一理解为“接管 `<AO-KEY>`”，公开命令固定为 `ao-maint takeover <AO-KEY>`。Runtime 自动区分新接管、恢复、接纳存量和阻断；恢复或接纳存量必须向用户明文说明并留下审计。不得用 Atlassian Connector、直接 REST API 或 Shell 网络请求绕过 maintainer Runtime。

`ao-maint` 的全部 Jira 任务操作固定只允许 AO 项目，包括 inspect、takeover、建卡、评论、Description、Worklog 和状态流转。非 AO 的 issue key、project key、父任务、计划文件或远端回读必须在读取凭证、网络请求、计划落盘或决策审计前以 `maintainer_jira_project_scope_mismatch` 阻断；Service 还必须独立重复校验，不能只依赖 CLI。`jira auth` 只管理 maintainer 本地凭证并明示 `allowed_project_keys=["AO"]`，不能扩大为 TAP 等业务项目权限；业务项目任务必须在对应 developer 工作空间使用 `ao-work`。

新接管必须由 Runtime 先写中文开始处理评论，再把 Jira 状态流转为“正在进行”，并逐项回读。设计审查确认后形成绑定工作项、运行 ID、仓库、分支、设计摘要、修改范围和验证方式的工作项级连续执行授权。授权范围内的正常分析、实现、验证和必要 Jira 进度回写连续推进，不逐项暂停；只在设计审查、代码审查、风险或范围决策时暂停。代码审查按版本化分支策略执行：功能、修复和任务分支推进到 commit、push、PR 后提供 PR 地址及当前 Head，在 PR 上逐项审查；`develop` 等其它允许分支先形成未推送 commit，提供提交编号并在推送前逐项审查。两种通道都必须列出确认事项、变更点和风险，不能要求用户确认或复制内部 `impact_id`。`main`、合并、发布、Git Tag、强推和历史改写继续使用独立人工门禁。

根仓库及任何 AgenticOps worktree 都必须加载本文件和 `maintainer/rules/source-maintenance.md`；不得把上述约束只留在聊天上下文或本机临时状态中。
