# AI 资产入口

本文是 AIAgent 执行任务前的资产读取入口。`docs/` 面向人阅读；本文列出的资料面向 AIAgent 执行、恢复、门禁、证据和审计。

## 业务任务读取顺序

AIAgent 执行业务 Jira 任务前按以下顺序读取：

1. [AI 员工手册](../handbooks/ai-employee-handbook.md)：理解任务模型、工作原则、停止条件和证据要求。
2. [操作契约说明](../../../docs/contracts/operation-contract.md)：理解操作输入、输出、失败码、副作用和人工门禁。
3. [机器可读操作契约](../contracts/operations/)：读取当前可执行操作定义。
4. [标准流程定义](../contracts/processes/)：读取任务分类、标准流程和阶段标准。
5. [工作流配置说明](../../../docs/profiles/workflow-profile.md)：理解工作空间和 Jira / GitHub / 本地源码映射。
6. [公司级硬规定](../company/standards/core-hard-rules.md)：读取跨项目通用硬规则和人工门禁。
7. [项目资产包](../projects/)：读取当前项目的 profile、规范、运行手册、模板和工具声明。
8. 当前项目 AI 工作空间 `.agentic-ops/profile.local.yaml`：只读取本地 overlay，不要求复制完整全局资源。
9. [策略](../policies/)、[运行手册](../runbooks/) 和 [模板](../templates/)：读取安装后分发给 AIAgent 的门禁、处理步骤和证据格式。

实际执行前应运行 `agentic-cli profile resolve --project <project>` 查看 effective profile 和字段来源；处理具体 Jira 卡片前应运行 `agentic-cli inspect-task <issue-key> --workspace <project>` 获取事实和项目资产引用。资产解析优先级固定为：

```text
项目工作空间 overlay
> ~/.agentic-ops/user/
> install-resources/basic/projects/<project>/
> install-resources/basic/company/
> agentic-cli 内置兜底
```

该顺序只用于配置和 profile 字段来源解析，不等同于规则冲突优先级。规则冲突必须按 `项目规则 > AIAgent 规则 > 公司规则 > 个人规则` 执行；个人层可以提供本机默认值，但不能覆盖更高优先级规则。

## 源头仓库维护读取顺序

如果 AIAgent 维护的是 AgenticOps 源头仓库，而不是执行业务 Jira 任务，还必须额外读取：

1. [AIAgent 工作规则](../../../docs/ai-working-rules.md)：理解维护 AgenticOps 源头仓库时的工作约束。
2. [项目研发期规则](../../../docs/development-phase-rules.md)：理解第一个版本上线前的临时门禁。
3. [项目规则](../../../docs/project-rules.md)：理解源头仓库的长期维护规则。

## 使用边界

- AIAgent 不应把 README 或 `docs/` 当作执行事实源；README 和 `docs/` 主要帮助人理解项目。
- AIAgent 执行具体业务 Jira 任务时，以 AI 员工手册、操作契约、标准流程、工作流配置、项目级规范、策略、运行手册和模板为准。
- AIAgent 收到“按 `~/.agentic-ops/agent-guides.md` 启用 AgenticOps。”时，应先读取全局指引，再从当前工作空间 `AGENTS.md`、`.agentic-ops/agent.json` 和本入口初始化；不得要求读取研发工程师个人 wiki、个人长期记忆或上一段聊天上下文。
- AIAgent 不得临场猜测 Jira 字段、状态流转或人工门禁。
- 卡片不满足项目准入标准时，AIAgent 必须先读取项目准入资产和目标代码，形成结构化分析与补卡建议；按项目规定写回 Jira 后结束当前接管，不能用会话内推断绕过重新检查。
- 字段映射缺失、权限不足或无法安全更新 Jira 事实时，必须停止并请求研发工程师或流程负责人处理。

## 当前目录说明

当前阶段先提供统一 AI 资产入口，暂不移动运行资产源文件，避免影响 `agentic-cli` 的资产加载、测试和发布打包路径。

后续如果迁移目录，应把以下源头逐步归并到 `ai-assets/`，并同步调整 CLI loader、测试和发版脚本：

- `handbooks/`
- `contracts/`
- `company/`
- `projects/`
- `standards/`
- `assets/`
- `docs/ai-working-rules.md`
- `docs/contracts/`
- `docs/profiles/`
- `docs/runtime/`
- `docs/templates/`
