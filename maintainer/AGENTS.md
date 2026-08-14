# AgenticOps 项目维护 AI 入口

本入口固定属于 `maintainer` 工作面，只维护、测试、发布和演进 `tapstate/agentic-ops` 源头项目。

- 命令入口：仓库内 `maintainer/bin/ao-maint`。
- 规则入口：`maintainer/rules/`。
- Skill 入口：`maintainer/skills/`。
- 授权、配置和状态：只能使用 `maintainer/` 命名空间及源头仓库事实。

禁止加载 `developer/AGENTS.md`、业务项目工作空间授权、业务任务状态或 developer 专用 Skill。需要验证研发工作面时，只能由维护测试把 `ao-work` 当作黑盒，并使用测试前明确确认的输入清单；不得导入 developer Runtime 或继承现有本机业务身份。

工作面由本入口确定。AI 不得自行判断、推断或通过命令参数切换工作面。
