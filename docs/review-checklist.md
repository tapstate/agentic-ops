# 设计审阅清单

本文用于审阅 AgenticOps 第一阶段设计。审阅通过前，项目只维护文档和设计，不开始编码。

## 1. 建议阅读顺序

1. [目标定位](strategy/positioning.md)：确认 AgenticOps 的价值、边界和第一阶段目标。
2. [项目规则](project-rules.md)：确认项目资料、运行资料、人工门禁和安全约束。
3. [当前设计](architecture/agenticops-current-design.md)：确认整体架构、运行边界和主流程。
4. [项目结构](architecture/project-structure.md)：确认仓库目录、全局安装目录和项目 AI 工作空间边界。
5. [用户故事](user-stories/agenticops-user-stories.md)：确认安装、初始化、任务接管、恢复接管和工作日志上报。
6. [AI 员工手册](../handbooks/ai-employee-handbook.md)：确认 AIAgent 如何工作、何时停止、如何回写证据。
7. [操作契约](contracts/operation-contract.md)：确认 AIAgent 能调用哪些受控操作，以及每个操作的输入、输出和副作用。
8. [工作流配置](profiles/workflow-profile.md)：确认如何屏蔽 Jira 事实，并把具体项目流程映射成稳定配置。
9. [CLI 运行时](runtime/cli-runtime.md)：确认第一阶段控制层采用 Go CLI Runtime，shell 只做安装引导，不做常驻服务或 Web 平台。
10. [反馈闭环](workflows/feedback-loop.md)：确认工作日志如何沉淀为 AgenticOps 改进建议。

## 2. 必须确认的设计项

- AgenticOps 是 AI 执行控制体系，不替代 Jira、研发 owner、PR Review 或 CI。
- 第一阶段从已进入迭代、已指定研发 owner 的 Jira issue 开始。
- 研发 owner 手动触发任务接管，AIAgent 不能全自动接管任务。
- `tapstate/agentic-ops` 是源码、规则、手册、契约、配置模板和通用文档的源头仓库。
- `~/.agentic-ops` 是本机全局安装和配置目录，不是具体项目运行目录。
- 具体项目 AI 工作空间才是运行目录，例如 `tapstate`、`tapdata`。
- AI 员工在具体任务中产生的代码、日志、验证结果和任务上下文不能混入全局安装目录。
- AIAgent 必须通过操作契约使用工具，不能直接猜测 Jira 字段、状态或工作流。
- Git 和 GitHub 可以轻封装，但 push、PR、merge 和发布必须有人确认。
- 工作日志可以生成改进建议，但不能未经人工确认自动改写 AgenticOps 源头规则。
- 当前阶段不写实现代码；只有设计审阅确认后，才进入实现计划。

## 3. 审阅时重点找的问题

- 是否有文档暗示尚未实现的命令、脚本、配置或适配器已经存在。
- 是否有资料边界混淆，把全局安装目录、源头仓库和项目 AI 工作空间混在一起。
- 是否有 AIAgent 可以绕过研发 owner 的人工确认点。
- 是否有 Jira、GitHub、Git 或本地路径的事实被写死在通用规则中。
- 是否有标题、术语或文件说明不利于试点研发理解。
- 是否有用户故事缺少失败路径、输出证据或验收标准。
- 是否有反馈闭环会导致规则自动自我修改。

## 4. 当前结构判断

当前文档结构可以支持第一阶段审阅，不需要立即请求额外目录决策。

需要你决策的情况是：

- 希望把 `contracts/`、`profiles/`、`skills/`、`templates/` 提前填充成运行时默认配置，而不是继续作为设计说明。
- 希望第一阶段改为常驻服务或 Web 控制台，而不是本地 Go CLI。
- 希望 AgenticOps 强绑定某个固定 Jira 工作流，而不是通过 workflow profile 做项目级映射。
- 希望降低人工确认门槛，例如允许低风险任务自动 push 或自动创建 PR。
- 希望把 Git / GitHub 做成完全封装的上层领域模型，而不是轻 guard。

## 5. 审阅通过后的下一步

审阅通过表示可以开始写实现计划，但仍不表示可以直接编码。

实现计划必须先明确：

- 第一阶段最小可运行命令集合。
- 文件和目录创建规则。
- 本地配置和 secrets 隔离方式。
- operation contract 的第一批机器可读格式。
- workflow profile 的第一批配置格式。
- 端到端演示如何验证。
