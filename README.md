# AgenticOps

AgenticOps 是把公司员工执行标准沉淀成 AI 可执行标准流程的本地控制体系。

它面向研发 Jira 任务处理场景，让 Jira 继续管理任务事实源，让研发工程师继续负责关键授权，让代码审查人、QA、运维、安全等专业角色在对应节点审查结果，同时把 AI 员工的执行动作收敛到可审计的命令、操作契约、标准表单、工作日志、证据和人工门禁里。

AgenticOps 的目标不是让 AIAgent 靠临场聊天上下文猜流程，而是让 AIAgent 面向稳定标准资产工作：先识别任务类型和当前阶段，再按操作契约、工作流配置、策略门禁、运行手册和模板执行，最后把关键状态、关键信息、表单数据和证据回写到合适的位置，用于恢复、复盘和持续优化。

## 核心模型

```text
Jira 任务
-> 研发工程师授权
-> AgenticOps 标准流程
-> agentic-cli 受控执行
-> AIAgent 开发、验证和证据整理
-> 专业角色审查
-> 证据 / 反馈 / 人工门禁
-> 标准资产迭代
```

一句话定义：

```text
AgenticOps = Skill + Python Runtime + Shell Bootstrap + Rule + 标准资产 + 本地任务状态 + 证据与反馈闭环
```

术语边界：

- `AgenticOps` 是项目和执行控制体系。
- `AgenticCLI` 是 AgenticOps 成熟经验沉淀后的执行入口组件，目标实现是 Python Runtime。
- `agentic-cli` 是安装后给 AIAgent 和研发工程师使用的稳定命令入口。

## 设计原则

- Jira 是任务、需求、负责人、迭代、状态、评论和执行证据的事实源。
- Git 仓库是代码、测试、提交和分支的事实源。
- GitHub 拉取请求与 CI 是代码审查、CI、审查评论和合入记录的事实源。
- AgenticOps 不创建新的任务管理事实源。
- `agentic_run_id` 只追踪一次 AI 执行，不替代 Jira 卡片编号，也不替代 Jira 状态。
- `agent_id` 标识一个 AIAgent 身份；`agentic_id` 是任务当前绑定的 `agent_id`，不是新的身份字段。
- AIAgent 必须按任务类型 `task_type`、当前阶段 `current_stage` 和下一步动作 `agentic_next_action` 推进，不按固定角色推进。
- 架构先稳定大的流程环节、门禁、状态、容错和演进机制；计划再从大阶段拆到中任务和小步骤。
- 成熟固化的交互逻辑应沉淀为稳定、原子化的 Python 操作；Shell Bootstrap 只负责安装、更新、回滚、环境准备和启动，不承载业务判断。
- 真实 Jira 写操作、Git 推送、GitHub 拉取请求创建、合并和发布必须经过策略、门禁和人工确认。

## 谁会使用

### 项目维护者

项目维护者负责维护 AgenticOps 自身，包括架构、项目规则、操作契约、运行资产、AgenticCLI、实施计划、测试和发布链路。

从这里开始：

- [项目维护者上手](docs/maintainers/getting-started.md)
- [文档索引](docs/README.md)
- [项目目标](docs/strategy/project-goals.md)
- [目标全景](docs/strategy/skill-python-agenticops-project-overview.md)
- [当前 Go 迁移基线](docs/architecture/agenticops-current-design.md)
- [项目规则](docs/project-rules.md)
- [开发风格](docs/development-style.md)
- [源码发布流程](docs/architecture/source-release-workflow-design.md)
- [发布检查清单](docs/review-checklist.md)
- [项目结构](docs/architecture/project-structure.md)
- [Python Runtime 设计](docs/runtime/python-runtime.md)
- Jira `AO-11`：本次重构实施计划、进度和验收事实源
- [当前机器可读操作契约](install-resources/basic/contracts/operations/)

### 研发工程师

研发工程师是使用 AgenticOps 指挥 AI 处理 Jira 任务的人。研发工程师不需要关心 AgenticOps 源码细节，主要面对安装后的 `agentic-cli`、AI 员工手册、工作流配置、模板和证据链。

从这里开始：

- [研发工程师上手](docs/development-engineers/getting-started.md)
- [AI 员工手册](install-resources/basic/handbooks/ai-employee-handbook.md)
- [端到端演示](docs/examples/end-to-end-demo.md)
- [故事线总览](docs/user-stories/agenticops-user-stories.md)
- [问题修复与同步路径](docs/runtime/problem-resolution-and-update.md)

研发工程师读人用指引，AIAgent 读 AI 资产入口。初始化工作空间后，应要求 AIAgent 先读取 [AI 资产入口](install-resources/basic/ai-assets/README.md)，再接管具体 Jira 任务。

### AIAgent

AIAgent 不应主要依赖 README 或人用 `docs/` 执行任务，也不需要读取 AgenticOps 源码或关心运行环境实现。AIAgent 面对的是安装后的 Skill、Rule、命令入口和标准资产。

执行前读取：

- [AI 资产入口](install-resources/basic/ai-assets/README.md)
- [AI 员工手册](install-resources/basic/handbooks/ai-employee-handbook.md)
- [操作契约说明](docs/contracts/operation-contract.md)
- [机器可读操作契约](install-resources/basic/contracts/operations/)
- [工作流配置](docs/profiles/workflow-profile.md)

如果 AIAgent 是在维护 AgenticOps 源头仓库，还必须额外读取 [AIAgent 工作规则](docs/ai-working-rules.md)、[项目规则](docs/project-rules.md) 和 [源码发布流程](docs/architecture/source-release-workflow-design.md)。

## 标准资产

AgenticOps 通过稳定标准资产约束 AIAgent 的执行行为：

| 资产 | 用途 |
| --- | --- |
| AI 员工手册 | 说明 AIAgent 和研发工程师如何协作；AIAgent 工作规则是手册中的执行约束。 |
| 操作契约 | 定义可执行操作的输入、输出、失败码、副作用和人工门禁。 |
| 任务表单标准 | 定义任务各阶段必须形成的标准字段和审查依据。 |
| 工作流配置 | 把标准流程映射到具体 Jira、GitHub 和本地工作空间。 |
| 标准流程注册处 | 维护任务分类、流程阶段、责任角色和完成规则。 |
| 策略门禁 | 控制写操作、范围变更、发布、PR 和人工确认。 |
| 运行手册与模板 | 提供问题修复路径、补卡模板和证据模板。 |
| 证据与反馈闭环 | 聚合执行记录、失败模式和流程改进建议。 |

## 工作目录边界

`~/.agentic-ops` 是 `tapstate/agentic-ops` 的完整 managed clone。它的目录结构与 GitHub 仓库一致，用于保存安装后的 `agentic-cli`、安装元数据、全局配置和可安全重建的运行资产。

具体项目运行目录是项目 AI 工作空间，例如：

```text
tapstate/
tapdata/
```

项目 AI 工作空间保存该项目的 Jira 用户、Jira 空间、Jira 空间到代码仓库的映射、本地源码目录、工作流配置、任务执行上下文和反馈记录。一个 Jira 空间通常对应若干代码仓库，这个映射必须由工作流配置维护，不能由 AIAgent 临场猜测。

## 目录导航

| 目录 | 用途 |
| --- | --- |
| `docs/` | 人读文档，包括项目维护者、研发工程师、架构、规则、故事线、流程和设计说明。 |
| `runtime/` | Python Runtime 源码与运行时测试的目标位置。 |
| `bootstrap/` | 安装、更新、回滚、环境准备和 `agentic-cli` Shell 包装入口的目标位置。 |
| `skills/` | AIAgent 可发现、可复用的标准流程入口。 |
| `rules/` | 事实源、权限、语言、分支、授权和停止条件。 |
| `standards/` | 公司、契约、标准流程、策略、运行手册、模板和项目差异资产的目标位置。 |
| `install-resources/`、`packages/agentic-cli/` | 迁移期间保留的旧安装资源与 Go 能力基线，替代能力验收后逐项删除。 |
| `bin/` | 本机安装后的命令目录，仓库只跟踪 `bin/.gitkeep`，本地生成的 `bin/agentic-cli` 不提交。 |
| `.local/` | 本机安装和更新状态目录，仓库只跟踪 `.local/.gitkeep`，本地状态文件不提交。 |
| `.superpowers/` | 项目工作空间的本地执行状态目录，由 Git 忽略，不保存正式设计、计划或运行资产。 |
| `plans/` | 历史实施记录；目标结构中删除，新的计划、进度和验收统一进入 Jira。 |
| `scripts/` | AgenticOps 源头仓库发布与固定验证编排；不承载安装后业务逻辑。 |
| `tests/` | 合同、脚本和端到端验证。 |

实施计划、阶段状态、验收记录和剩余工作由 Jira 管理；仓库只保存长期有效的目标、架构、规则、标准资产和测试。
