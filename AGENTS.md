# AgenticOps 仓库指令

## 项目阶段

当前仓库已进入第一阶段本地实现。继续维护 AgenticOps 的项目定位、规则、架构、用户故事、操作契约、AI 员工手册和实施计划，同时允许在用户明确要求下实现 `agentic-cli` Go CLI 的本地 fake flow。

文档中的命令、目录和接口如果尚未有对应源码或可执行输出，只能视为目标设计，不得描述为已实现能力。当前已实现能力以源码、测试和命令输出为准。

## 项目结构

核心目录约定：

- `docs/`：架构、规则、用户故事、流程和设计说明。
- `handbooks/`：AI 员工手册，面向 AIAgent 和研发 owner。
- `plans/`：可执行推进计划，使用 checkbox 跟踪实施进度。
- `contracts/`：后续机器可读 Operation Contract 源头。
- `profiles/`：Workflow Profile 示例和默认配置。
- `skills/`：AgenticOps skills。
- `templates/`：Jira、PR 和 evidence 回写模板。
- `packages/agentic-cli/`：Go CLI Runtime 的未来实现位置。
- `examples/`：端到端演示样例。
- `tests/`：合同、脚本和文档一致性测试。
- `scripts/`：安装、检查和辅助脚本。

本地工具状态目录 `.superpowers/` 不属于项目资料，不维护、不提交。

## 语言与命名

面向用户、研发 owner 和审阅者的可见文档标题默认使用中文。产品名、角色名、工具名、命令、配置字段、协议字段、文件名、目录名和稳定编号可以保留英文或缩写。

Jira 交互中的人可见内容必须使用中文，包括标题、描述、评论、工作日志、evidence 正文、阻塞说明和补卡说明。Jira 字段名、状态名、transition 名称、issue key、命令、配置字段和协议字段可以保留原始英文或缩写。

目录名和文件名默认使用英文 ASCII lowercase-kebab-case。Markdown 正文以中文为主，首次出现关键术语时可中英并列。

## 运行时方向

第一阶段主 CLI 使用 Go 实现，统一入口为 `agentic-cli`。shell 只用于 `curl | bash` 安装引导、轻量环境检测、下载或切换 Go release 二进制，不承载 Jira、GitHub、Git、Operation Contract、policy gate、evidence 或 feedback 的业务逻辑。

`~/.agentic-ops` 是全局安装和配置目录，不是具体项目运行目录。具体项目运行目录是项目 AI 工作空间，例如 `tapstate` 或 `tapdata`。

## 测试与验证

引入运行代码后，必须在同一变更中补充可执行验证命令。当前阶段至少使用这些检查：

- `git status --short`
- `find . -maxdepth 3 -type f | sort`
- 使用 `rg` 检查 README、docs、handbooks 和 plans 中的常见占位词。

所有 secrets、tokens、private keys 和原始敏感日志都不得提交。

## 提交规则

提交信息使用英文，推荐格式：

```text
<type>(<scope>): <subject>
```

常用类型：`Feat`、`Fix`、`Docs`、`Style`、`Refactor`、`Test`、`Chore`。

每个提交只包含一个逻辑变更。非平凡提交需要在 commit body 中说明做了什么、解决什么问题以及为什么这样做。
