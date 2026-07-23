# AgenticOps 仓库指令

## 项目研发期规则

AgenticOps 第一个版本发布正式上线前，必须遵守 `docs/development-phase-rules.md`。正式上线后，应删除本节或解除对该文档的依赖，再把仍需长期保留的内容迁移到对应永久规则区块。

## 项目结构

核心目录约定：

- `docs/`：架构、规则、用户故事、流程和设计说明。
- `handbooks/`：AI 员工手册，面向 AIAgent 和研发负责人。
- `plans/`：可执行推进计划，使用勾选项跟踪实施进度。
- `contracts/`：后续机器可读操作契约源头。
- `profiles/`：工作流配置示例和默认配置。
- `skills/`：AgenticOps skills。
- `templates/`：Jira、拉取请求和证据回写模板。
- `packages/agentic-cli/`：Go CLI 运行时的未来实现位置。
- `examples/`：端到端演示样例。
- `tests/`：合同、脚本和文档一致性测试。
- `scripts/`：安装、检查和辅助脚本。

本地工具状态目录 `.superpowers/` 不属于项目资料，不维护、不提交。

## 项目边界与工作区隔离

当前规则只适用于 `tapstate/agentic-ops` 项目本身。不得把其它项目的研发规范、分支策略、验证命令、目录约定或上线前临时规则合并进 AgenticOps 当前项目规则。

不同项目的 AI 工作空间必须分开维护。AgenticOps 源头仓库、全局安装目录 `~/.agentic-ops`、以及 `tapstate`、`tapdata` 等具体项目 AI 工作空间不能混用；只有明确标注为跨项目通用资产的规则，才可以沉淀到 AgenticOps 通用资料中。

## 规范类型边界

当前项目维护规范只约束维护 `tapstate/agentic-ops` 源头仓库的维护者或项目维护代理，不等同于安装后 AIAgent 执行业务 Jira 任务的运行规范。

安装后 AIAgent 的执行规范必须维护在 AI 员工手册、操作契约、工作流配置、运行资产、模板和对应运行文档中。不得把当前项目研发期规则、提交规则、分支规则或仓库维护流程直接套用为 AIAgent 运行期执行规范；也不得把某个业务项目的 AIAgent 执行细则反向写成 AgenticOps 当前项目维护规则。

## 语言与命名

面向用户、研发负责人和审阅者的可见文档标题默认使用中文。产品名、角色名、工具名、命令、配置字段、协议字段、文件名、目录名和稳定编号可以保留英文或缩写。

Jira 交互中的人可见内容必须使用中文，包括标题、描述、评论、工作日志、证据正文、阻塞说明和补卡说明。Jira 字段名、状态名、transition 名称、issue key、命令、配置字段和协议字段可以保留原始英文或缩写。

目录名和文件名默认使用英文 ASCII lowercase-kebab-case。Markdown 正文以中文为主，首次出现关键术语时可中英并列。

## 运行时方向

AgenticCLI 使用 Go 实现，统一入口为 `agentic-cli`。shell 只用于 `curl | bash` 安装引导、轻量环境检测、下载或切换 Go release 二进制，不承载 Jira、GitHub、Git、操作契约、策略门禁、证据或反馈的业务逻辑。

`~/.agentic-ops` 是全局安装和配置目录，不是具体项目运行目录。具体项目运行目录是项目 AI 工作空间，例如 `tapstate` 或 `tapdata`。

## 测试与验证

引入运行代码后，必须在同一变更中补充可执行验证命令。正式上线前的额外验证要求见 `docs/development-phase-rules.md`。

所有 secrets、tokens、private keys 和原始敏感日志都不得提交。

## 提交规则

提交信息推荐格式：

```text
<type>(<scope>): <subject>
```

`type` 和 `scope` 使用英文；`subject` 使用中文，简洁说明本次提交做了什么。常用类型：`Feat`、`Fix`、`Docs`、`Style`、`Refactor`、`Test`、`Chore`。

commit body / description 使用中文，说明做了什么、解决什么问题以及为什么这样做。

每个提交只包含一个逻辑变更。非平凡提交必须包含中文 commit body / description。
