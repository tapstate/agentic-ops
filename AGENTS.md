# AgenticOps 仓库指令

## 项目研发期规则

AgenticOps 第一个版本发布正式上线前，必须遵守 `docs/development-phase-rules.md`。正式上线后，应删除本节或解除对该文档的依赖，再把仍需长期保留的内容迁移到对应永久规则区块。

## 目标文档必读

涉及设计、优化、计划、架构调整、流程调整、标准资产调整或会影响项目演进方向的变更前，必须先读取 `docs/strategy/project-goals.md`。

普通小修、测试、构建和不影响方向的局部文档措辞调整，不因本条额外增加阅读要求。

## 项目结构

核心目录约定：

- `docs/`：人读文档，包括架构、规则、用户故事、流程和设计说明。
- `install-resources/basic/`：跨平台通用安装资源，包括 AI 资产入口、手册、操作契约、工作流配置、策略、运行手册和模板。
- `install-resources/<os-arch>/`：平台二进制产物，只放对应平台的 `agentic-cli`。
- `install-resources/checksums.txt`：安装资源校验和。
- `bin/`：安装后的本机命令目录，仓库只提交 `bin/.gitkeep`，本地 `bin/agentic-cli` 不提交。
- `.local/`：本机安装和更新状态，仓库只提交 `.local/.gitkeep`，本地状态文件不提交。
- `plans/`：可执行推进计划，使用勾选项跟踪实施进度。
- `skills/`：AgenticOps skills。
- `packages/agentic-cli/`：Go CLI 运行时的未来实现位置。
- `examples/`：端到端演示样例。
- `tests/`：合同、脚本和文档一致性测试。
- `scripts/`：安装、检查和辅助脚本。

项目工作空间下的 `.superpowers/` 只保存 Superpowers 等工具的本地执行状态、检查点、临时分析和缓存，不属于项目资料，不维护、不提交。正式设计必须写入 `docs/` 的对应主题目录，可执行计划必须写入顶层 `plans/`；不得创建或提交 `docs/superpowers/`。

## 项目边界与工作区隔离

当前规则只适用于 `tapstate/agentic-ops` 项目本身。不得把其它项目的研发规范、分支策略、验证命令、目录约定或上线前临时规则合并进 AgenticOps 当前项目规则。

不同项目的 AI 工作空间必须分开维护。AgenticOps 源头仓库、全局安装目录 `~/.agentic-ops`、以及 `tapstate`、`tapdata` 等具体项目 AI 工作空间不能混用；只有明确标注为跨项目通用资产的规则，才可以沉淀到 AgenticOps 通用资料中。

## 规范类型边界

当前项目维护规范只约束维护 `tapstate/agentic-ops` 源头仓库的维护者或项目维护代理，不等同于安装后 AIAgent 执行业务 Jira 任务的运行规范。

安装后 AIAgent 的执行规范必须维护在 AI 员工手册、操作契约、工作流配置、运行资产、模板和对应运行文档中。不得把当前项目研发期规则、提交规则、分支规则或仓库维护流程直接套用为 AIAgent 运行期执行规范；也不得把某个业务项目的 AIAgent 执行细则反向写成 AgenticOps 当前项目维护规则。

## 规则类别与优先级

规则写入前必须先区分类别，不得把个人规则、公司规则、项目规则和 AIAgent 规则混写。

规则冲突时按以下优先级执行：

```text
项目规则 > AIAgent 规则 > 公司规则 > 个人规则
```

- 个人规则：只记录个人偏好、本机身份、个人 wiki 和本地工作流，维护在个人记忆库或本地 `~/.agentic-ops/user/`，不得写入公司或项目标准资产。
- 公司规则：只记录 TapData 跨项目硬规定、事实源边界、人工门禁、保密和通用提交要求，维护在 `install-resources/basic/company/`。
- 项目规则：只记录具体项目的语言、分支、提交、验证和工具例外，维护在对应项目仓库规则、项目 AI 工作空间或 `install-resources/basic/projects/<project>/`。
- AIAgent 规则：只记录 AIAgent 执行时的停止条件、交互语言、门禁、证据、审计和工具调用要求，维护在 AI 员工手册、操作契约、策略、运行手册、模板或当前工作空间 `AGENTS.md`。

项目规则覆盖公司规则或 AIAgent 规则时，必须能从项目规则文件或项目工作空间配置中看到明确来源；不得只依赖聊天上下文。

## 语言与命名

面向用户、研发工程师和审阅者的可见文档标题默认使用中文。产品名、角色名、工具名、命令、配置字段、协议字段、文件名、目录名和稳定编号可以保留英文或缩写。

AIAgent 面向用户、研发工程师、流程负责人、审阅者或 Jira 参与者的自然语言交互必须使用中文。

Jira 交互中的人可见内容必须使用中文，包括摘要、标题、描述、评论、工作日志、证据正文、阻塞说明、补卡说明和任务审计记录。Jira 字段名、状态名、transition 名称、issue key、命令、配置字段、协议字段、错误码、代码标识和日志关键字可以保留原始英文或缩写，但必须用中文解释结论、风险和需要人工处理的动作。

目录名和文件名默认使用英文 ASCII lowercase-kebab-case。Markdown 正文以中文为主，首次出现关键术语时可中英并列。

## 运行时方向

AgenticCLI 使用 Go 实现，统一入口为 `agentic-cli`。shell 只用于 `gh api | bash` 认证安装引导、轻量环境检测、managed clone 更新、校验安装资源和复制当前平台二进制，不承载 Jira、GitHub、Git、操作契约、策略门禁、证据或反馈的业务逻辑。

`~/.agentic-ops` 是 `tapstate/agentic-ops` 的完整 managed clone，不是具体项目运行目录。具体项目运行目录是项目 AI 工作空间，例如 `tapstate` 或 `tapdata`。

## 测试与验证

引入运行代码后，必须在同一变更中补充可执行验证命令。正式上线前的额外验证要求见 `docs/development-phase-rules.md`。

所有 secrets、tokens、private keys 和原始敏感日志都不得提交。

## 提交规则

本节属于 `tapstate/agentic-ops` 项目规则，只约束维护 AgenticOps 源头仓库。

提交信息推荐格式：

```text
<type>(<scope>): <tag> <subject>
```

AgenticOps 是内部项目，提交标题和提交描述正文使用中文。

`type`、`scope`、Jira key、命令和配置字段作为结构化标识可以保留英文。`tag` 指 Jira 任务编号，例如 `TAP-1234`。TapData 公司代码提交必须绑定 Jira 任务卡片，不得省略 `<tag>`。常用类型：`Feat`、`Fix`、`Docs`、`Style`、`Refactor`、`Test`、`Chore`。

Jira 任务编号必须能从分支名、用户指令、Jira 卡片或任务上下文中确认；无法确认时必须停止并请求研发工程师补齐，不得创建无 Jira 绑定的代码提交。

`scope` 使用英文模块名、包名或目录名；没有清晰模块时可以省略。`subject` 使用中文，简洁说明本次提交做了什么，末尾不加句号。

commit body / description 使用中文，说明做了什么、解决什么问题、为什么这样做、验证结果和风险。非平凡提交必须包含 body。

每个提交只包含一个逻辑变更。不得在提交信息中粘贴完整 Jira 描述、敏感日志、凭证或未经脱敏的客户信息。

AIAgent 执行本仓库任务时，即使项目规则允许直提 `main`，也只有在研发工程师明确要求“提交变更”或“提交代码”后才能执行 `git commit`。提交完成后不得自动推送。
