# AI 资产入口

本文是 AIAgent 执行任务前的资产读取入口。`docs/` 面向人阅读；本文列出的资料面向 AIAgent 执行、恢复、门禁、证据和审计。

## 业务任务读取顺序

AIAgent 执行业务 Jira 任务前按以下顺序读取：

1. [AI 员工手册](../handbooks/ai-employee-handbook.md)：理解任务模型、工作原则、停止条件和证据要求。
2. [操作契约说明](../docs/contracts/operation-contract.md)：理解操作输入、输出、失败码、副作用和人工门禁。
3. [机器可读操作契约](../contracts/operations/)：读取当前可执行操作定义。
4. [标准流程定义](../contracts/processes/)：读取任务分类、标准流程和阶段标准。
5. [工作流配置说明](../docs/profiles/workflow-profile.md)：理解工作空间和 Jira / GitHub / 本地源码映射。
6. [工作流配置源头](../profiles/)：读取当前项目或示例工作流配置。
7. [运行资产源头](../assets/)：读取安装后分发给 AIAgent 的手册、契约、策略、运行手册和模板。

## 源头仓库维护读取顺序

如果 AIAgent 维护的是 AgenticOps 源头仓库，而不是执行业务 Jira 任务，还必须额外读取：

1. [AIAgent 工作规则](../docs/ai-working-rules.md)：理解维护 AgenticOps 源头仓库时的工作约束。
2. [项目研发期规则](../docs/development-phase-rules.md)：理解第一个版本上线前的临时门禁。
3. [项目规则](../docs/project-rules.md)：理解源头仓库的长期维护规则。

## 使用边界

- AIAgent 不应把 README 或 `docs/` 当作执行事实源；README 和 `docs/` 主要帮助人理解项目。
- AIAgent 执行具体业务 Jira 任务时，以 AI 员工手册、操作契约、标准流程、工作流配置、策略、运行手册和模板为准。
- AIAgent 不得临场猜测 Jira 字段、目标仓库、状态流转或人工门禁。
- 缺少字段、映射、权限、验收标准或目标仓库时，必须停止并请求研发负责人或流程负责人补齐。

## 当前目录说明

当前阶段先提供统一 AI 资产入口，暂不移动运行资产源文件，避免影响 `agentic-cli` 的资产加载、测试和发布打包路径。

后续如果迁移目录，应把以下源头逐步归并到 `ai-assets/`，并同步调整 CLI loader、测试和发版脚本：

- `handbooks/`
- `contracts/`
- `profiles/`
- `assets/`
- `docs/ai-working-rules.md`
- `docs/contracts/`
- `docs/profiles/`
- `docs/runtime/`
- `docs/templates/`
