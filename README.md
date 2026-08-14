# AgenticOps

AgenticOps 是把公司员工执行标准沉淀成 AI 可执行标准流程，并通过业务项目工作空间交付可持续进化的 AI 研发员的本地控制体系。

一个业务项目 AgenticOps 工作空间代表一名研发员；`~/.agentic-ops` 只是共享安装，不代表人员。一台电脑可以维护多个彼此隔离的研发员工作空间。公司员工指导员负责安装、授权、校对和进化研发员；Jira 继续管理任务事实源，代码审查人、QA、运维、安全等专业角色继续在对应节点审查结果。

AgenticOps 的目标不是让 AIAgent 靠临场聊天上下文猜流程，而是让 AIAgent 面向稳定标准资产工作：先识别任务类型和当前阶段，再按操作契约、工作流配置、策略门禁、运行手册和模板执行，最后把关键状态、关键信息、表单数据和证据回写到合适的位置，用于恢复、复盘和持续优化。

## 核心模型

```text
Jira 任务
-> 公司员工指导员授权
-> AgenticOps 标准流程
-> ao-work 受控执行
-> AgenticOps 研发员开发、验证和证据整理
-> 专业角色审查
-> 证据 / 反馈 / 人工门禁
-> 标准资产迭代
```

一句话定义：

```text
AgenticOps = 两个隔离工作面 + Skill + Python Runtime + Shell Bootstrap + Rule + 标准资产 + 本地任务状态 + 证据与反馈闭环
```

术语边界：

- `AgenticOps` 是项目和执行控制体系。
- 一个业务项目工作空间是一名 AgenticOps 研发员；该工作空间只绑定一个 Jira 账户。
- 项目维护者承担公司员工指导员职责，维护标准、能力和边界，指导研发员持续进化。
- `maintainer` 是维护 AgenticOps 源头项目的工作面，唯一命令入口是 `ao-maint`。
- `developer` 是研发员执行业务项目任务的工作面，唯一命令入口是 `ao-work`。
- 两个命令不是两名员工，而是两个隔离工作面的操作入口；不得用同一命令的 mode 参数互相切换。

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

### 公司员工指导员（项目维护者）

项目维护者承担公司员工指导员职责，负责通过 `maintainer` 工作面维护 AgenticOps 自身的架构、项目规则、操作契约、运行资产、测试和发布链路，让研发员能力持续符合公司标准。

从这里开始：

- [项目维护者上手](docs/maintainers/getting-started.md)
- [文档索引](docs/README.md)
- [项目目标](docs/strategy/project-goals.md)
- [目标全景](docs/strategy/skill-python-agenticops-project-overview.md)
- [历史实现记录（冻结）](docs/architecture/agenticops-current-design.md)
- [项目规则](docs/project-rules.md)
- [开发风格](docs/development-style.md)
- [源码发布流程](docs/architecture/source-release-workflow-design.md)
- [发布检查清单](docs/review-checklist.md)
- [项目结构](docs/architecture/project-structure.md)
- [Python Runtime 设计](docs/runtime/python-runtime.md)
- Jira `AO-11`：本次重构实施计划、进度和验收事实源
- [当前机器可读操作契约](developer/standards/contracts/operations/)

### 公司员工指导员（业务指导）

业务研发工程师在使用 AgenticOps 时承担公司员工指导员职责，负责创建研发员工作空间、明确任务、授权高风险动作、校对结果和反馈改进，不需要关心 AgenticOps 源码细节。

从这里开始：

- [研发工程师上手](docs/development-engineers/getting-started.md)
- [developer AI 执行规则](developer/AGENTS.md)
- [端到端演示](docs/examples/end-to-end-demo.md)
- [故事线总览](docs/user-stories/agenticops-user-stories.md)
- [问题修复与同步路径](docs/runtime/problem-resolution-and-update.md)

研发工程师读人用指引；`ao-work workspace init` 会在业务项目工作空间生成固定的 developer AI 入口，AIAgent 从当前工作空间 `AGENTS.md` 开始，并从 `.agents/skills/` 发现受管 developer Skill，不读取 maintainer 资产。后续任务只给 Jira key；完整 manifest 由工作空间、Project Profile、Jira 卡片、Runtime 和已审查 AI 计划共同生成，不作为用户逐字段配置表。

### AgenticOps 研发员

业务项目工作空间代表 AgenticOps 研发员。底层 AIAgent 不应主要依赖 README 或人用 `docs/` 执行任务，也不读取 AgenticOps 源头维护资产；它面对的是安装后的 `developer` Skill、Rule、`ao-work` 和标准资产。

执行前读取：

- 当前业务项目工作空间 `AGENTS.md`
- [developer AI 执行规则](developer/AGENTS.md)
- [操作契约说明](docs/contracts/operation-contract.md)
- [机器可读操作契约](developer/standards/contracts/operations/)
- [工作流配置](docs/profiles/workflow-profile.md)

如果 AIAgent 在维护 AgenticOps 源头仓库，根 `AGENTS.md` 会固定导向 `maintainer/AGENTS.md`；它还必须读取 [项目规则](docs/project-rules.md) 和 [源码发布流程](docs/architecture/source-release-workflow-design.md)。业务项目 AI 入口不得加载这些维护规则。

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

`~/.agentic-ops` 是稳定 `main` 的 developer-only sparse managed clone，只包含研发工程师工作面所需的 `ao-work`、Skill、Rule、标准资产、锁定 Python 环境和可安全重建的安装状态。它不包含 `maintainer/`、developer 测试、fixture 或 fake producer，不代表任何研发员，也不能作为 AgenticOps 源头维护入口。

具体项目运行目录是项目 AI 工作空间，例如：

```text
tapstate/
tapdata/
```

项目 AI 工作空间保存该研发员唯一的 Jira 账户、Jira 空间、Jira 空间到代码仓库的映射、本地源码目录、工作流配置、任务执行上下文和反馈记录。一个 Jira 空间通常对应若干代码仓库，这个映射必须由工作流配置维护，不能由 AIAgent 临场猜测。

## 目录导航

| 目录 | 用途 |
| --- | --- |
| `docs/` | 人读文档，包括项目维护者、研发工程师、架构、规则、故事线、流程和设计说明。 |
| `maintainer/` | 源头项目维护工作面：`ao-maint`、维护 Runtime、维护 Skill、规则、故事门禁、发布脚本和维护测试。 |
| `developer/` | 业务研发工作面：`ao-work`、业务 Runtime、业务 Skill、规则、标准资产、Bootstrap 和业务测试。 |
| `shared/` | 经审查后才允许两个工作面共同读取的中立资料；默认不共享代码和规则。 |
| `.superpowers/` | 项目工作空间的本地执行状态目录，由 Git 忽略，不保存正式设计、计划或运行资产。 |
| `maintainer/scripts/` | AgenticOps 源头仓库发布与固定验证编排；不承载安装后业务逻辑。 |

旧 Go Runtime、平台二进制、`agentic-cli`、`install-resources/` 及根目录旧运行资产已删除。历史行为只通过版本分支、Tag 和 Git 历史查阅，不是现役入口或兼容层。

实施计划、阶段状态、验收记录和剩余工作由 Jira 管理；仓库只保存长期有效的目标、架构、规则、标准资产和测试。
