# AgenticOps 研发员执行指引

本指引只服务业务项目工作空间的 `developer` 工作面。初始化后的命令入口固定为 `./.agentic-ops/bin/ao-work`；首次授权、初始化或恢复使用目标安装的 `<install-root>/bin/ao-work`。项目维护入口 `ao-maint` 不会随 developer 安装交付。

## 固定读取顺序

1. 当前业务项目工作空间 `AGENTS.md`。
2. 安装目录 `developer/AGENTS.md` 与 `developer/rules/ai-execution.md`。
3. 当前任务匹配的 `developer/skills/<skill>/SKILL.md`。
4. `ao-work capability list|show` 返回的现役能力目录。
5. 当前 Project Profile、操作契约和运行手册。
6. `.agentic-ops/agent.json` 与当前任务 Runtime 状态。

不得读取或加载 `maintainer/`、AgenticOps 源头目标、源头发布规则、维护授权或维护状态。能力不足时生成脱敏反馈，由人工交接给项目维护者。

## 任务入口

开始真实任务前先执行：

```sh
ao-work auth --show
ao-work capability list
```

随后按任务类型加载对应 Skill。`workspace preflight` 仅在需要诊断或修复工作空间时执行，不是接管任务的前置步骤；接管 Runtime 会重新校验工作空间、安装身份和 Jira 事实。每次调用前用 `ao-work capability show <operation>` 确认 `status=implemented` 和目录声明的命令路径；`capability_gap` 不得调用旧命令。若 AgenticOps 自身缺口会阻断业务开发，进入 `agenticops_continuity_decision`，由人工决定等待修复、限定使用项目认可工具继续或转人工执行。Operation Contract 只是目标行为边界，不代表命令已经实现。

任务状态的 `ao-work task` 与本地报告的 `ao-work report` 是内部 Runtime 接口，只能由版本化 Skill 编排，不能向用户描述为任务接管、Jira 回写或完成审计。Jira Comment 和 Worklog 通过 `ao-work jira` 的 `plan -> apply -> readback` 协议处理；Description 使用 `plan -> apply`，写后回读由 apply 内部完成。

所有阻断结果都必须按结构化失败码和 `required_human_action` 处理。不得绕过授权、猜测 Jira 字段、直接修改 Runtime 管理的 JSON、跨工作空间读取凭证，或用聊天上下文代替版本化规则。

## 工作面边界

- 一个 developer 安装代表一名研发员；同一安装下可以有多个相互隔离的业务项目工作空间。
- `~/.agentic-ops` 不代表具体项目，但保存当前研发员的安装级身份与凭证；多名研发员必须使用隔离安装。
- 工作面由 AI 入口和命令确定，不接受 `--mode`、`--workplane` 或环境变量切换。
- 发现自己位于 AgenticOps 源头仓库时，`ao-work` 必须返回 `workplane_mismatch` 并停止。
