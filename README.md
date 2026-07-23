# AgenticOps

AgenticOps 是把公司员工执行标准沉淀成 AI 可执行标准流程的本地控制体系。

它面向研发 Jira 任务处理场景，让 Jira 继续管理任务事实源，让研发 owner 继续负责关键授权，让 reviewer、QA、运维、安全等专业角色在对应节点审查结果，同时把 AI 员工的执行动作收敛到可审计的命令、操作契约、标准表单、工作日志、证据和人工门禁里。

AgenticOps 的目标不是让 AIAgent 靠临场聊天上下文猜流程，而是让 AIAgent 面向稳定标准资产工作：先识别任务类型和当前阶段，再按操作契约、工作流配置、策略门禁、运行手册和模板执行，最后把关键状态、关键信息、表单数据和证据回写到合适的位置，用于恢复、复盘和持续优化。

## 核心模型

```text
Jira 任务
-> 研发 owner 授权
-> AgenticOps 标准流程
-> agentic-cli 受控执行
-> AIAgent 开发、验证和证据整理
-> 专业角色审查
-> 证据 / 反馈 / 人工门禁
-> 标准资产迭代
```

一句话定义：

```text
AgenticOps = AI 员工手册（含 AIAgent 工作规则）+ 项目规则 + 操作契约 + 任务表单标准 + 工作流配置 + 策略门禁 + 运行手册 + 模板 + AgenticCLI + 证据与反馈闭环
```

术语边界：

- `AgenticOps` 是项目和执行控制体系。
- `AgenticCLI` 是 AgenticOps 成熟经验沉淀后的执行入口组件。
- `agentic-cli` 是安装后给 AIAgent 和研发 owner 使用的 CLI 二进制。

## 设计原则

- Jira 是任务、需求、owner、迭代、状态、评论和执行证据的事实源。
- Git 仓库是代码、测试、提交和分支的事实源。
- GitHub PR / CI 是 Review、CI、comments 和合入记录的事实源。
- AgenticOps 不创建新的任务管理事实源。
- `run_id` 只追踪一次 AI 执行，不替代 Jira issue key，也不替代 Jira 状态。
- AIAgent 必须按任务类型 `task_type`、当前阶段 `current_stage` 和下一步动作 `next_action` 推进，不按固定角色推进。
- 架构先稳定大的流程环节、门禁、状态、容错和演进机制；计划再从大阶段拆到中任务和小步骤。
- 成熟固化的交互逻辑应沉淀为稳定、原子化的 CLI 操作；脚本入口只做受控编排或调用，不承载业务判断。
- 真实 Jira 写操作、Git push、GitHub PR 创建、merge 和发布必须经过策略、门禁和人工确认。

## 谁会使用

### 项目维护者

项目维护者负责维护 AgenticOps 自身，包括架构、项目规则、操作契约、运行资产、AgenticCLI、实施计划、测试和发布链路。

从这里开始：

- [文档索引](docs/README.md)
- [当前设计](docs/architecture/agenticops-current-design.md)
- [项目规则](docs/project-rules.md)
- [开发风格](docs/development-style.md)
- [项目结构](docs/architecture/project-structure.md)
- [实施计划](plans/)
- [CLI 实现](packages/agentic-cli/)
- [机器可读操作契约](contracts/operations/)

### 研发 owner

研发 owner 是使用 AgenticOps 指挥 AI 处理 Jira 任务的人。研发 owner 不需要关心 AgenticOps 源码细节，主要面对安装后的 `agentic-cli`、AI 员工手册、工作流配置、模板和证据链。

从这里开始：

- [AI 员工手册](handbooks/ai-employee-handbook.md)
- [端到端演示](docs/examples/end-to-end-demo.md)
- [用户故事](docs/user-stories/agenticops-user-stories.md)
- [问题修复与同步路径](docs/runtime/problem-resolution-and-update.md)

### AIAgent

AIAgent 不应主要依赖 README 执行任务，也不需要读取 AgenticOps 源码或关心 Go 编译环境。AIAgent 面对的是安装后的命令行工具、AI 员工手册、操作契约、模板和工作规则。

执行前读取：

- [AI 员工手册](handbooks/ai-employee-handbook.md)
- [AIAgent 工作规则](docs/ai-working-rules.md)
- [操作契约说明](docs/contracts/operation-contract.md)
- [机器可读操作契约](contracts/operations/)
- [工作流配置](docs/profiles/workflow-profile.md)

## 标准资产

AgenticOps 通过稳定标准资产约束 AIAgent 的执行行为：

| 资产 | 用途 |
| --- | --- |
| AI 员工手册 | 说明 AIAgent 和研发 owner 如何协作；AIAgent 工作规则是手册中的执行约束。 |
| 操作契约 Operation Contract | 定义可执行操作的输入、输出、失败码、副作用和人工门禁。 |
| 任务表单标准 Task Form Standard | 定义任务各阶段必须形成的标准字段和审查依据。 |
| 工作流配置 Workflow Profile | 把标准流程映射到具体 Jira、GitHub 和本地工作空间。 |
| 标准流程注册处 Standard Process Registry | 维护任务分类、流程阶段、责任角色和完成规则。 |
| 策略门禁 Policy / Gate | 控制写操作、范围变更、发布、PR 和人工确认。 |
| 运行手册与模板 Runbook / Template | 提供问题修复路径、补卡模板和证据模板。 |
| 证据与反馈闭环 Evidence / Feedback Loop | 聚合执行记录、失败模式和流程改进建议。 |

## 工作目录边界

`~/.agentic-ops` 是全局安装和配置目录，不是具体项目运行目录。它用于保存安装后的 `agentic-cli`、安装元数据、全局配置和可安全重建的运行资产。

具体项目运行目录是项目 AI 工作空间，例如：

```text
tapstate/
tapdata/
```

项目 AI 工作空间保存该项目的 Jira 空间、GitHub 仓库、本地源码目录、工作流配置、任务执行上下文和反馈记录。

## 目录导航

| 目录 | 用途 |
| --- | --- |
| `docs/` | 架构、规则、用户故事、流程和设计说明。 |
| `handbooks/` | AI 员工手册，面向 AIAgent 和研发 owner。 |
| `assets/` | 安装后交付给研发 owner 和 AIAgent 使用的运行资产源头。 |
| `contracts/` | 机器可读操作契约和标准流程定义。 |
| `profiles/` | 工作流配置示例和默认配置。 |
| `plans/` | 基于稳定架构拆解的可执行推进计划。 |
| `packages/agentic-cli/` | AgenticCLI Go 运行时实现。 |
| `scripts/` | 安装、构建、发布和本地检查脚本。 |
| `tests/` | 合同、脚本和端到端验证。 |

阶段状态、验收记录和剩余工作由 [文档索引](docs/README.md)、[运行时问题修复路径](docs/runtime/problem-resolution-and-update.md) 和 [实施计划](plans/) 维护。
