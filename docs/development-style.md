# AgenticOps 开发风格

## 1. 目的

本文定义 AgenticOps 的开发风格和协作约束，用于提高 AIAgent 工作效率、减少幻觉、降低误改风险。

开发必须保持文档、契约、测试和代码同步。真实 Jira / GitHub 写操作、推送、创建拉取请求、合并和发布必须经过策略、门禁和人工确认。第一个版本发布正式上线前的临时限制见 `docs/development-phase-rules.md`。

## 2. 开发原则

AgenticOps 开发必须遵守：

- 架构先行：先确认并稳定架构边界，再基于架构拆解计划和实施任务。
- 文档先行：先确认目标定位、项目规则、故事线、操作契约、工作流配置和 AI 员工手册。
- 计划分层：计划必须从大阶段拆到中任务和小步骤，每个步骤都有明确验证方式。
- 小步交付：每次变更只解决一个明确设计问题。
- 证据优先：做结论前先读取当前文件、命令输出或权威文档。
- 不猜测外部事实：Jira 字段、GitHub 仓库、工作空间路径、权限和状态映射必须来自工作流配置、配置或用户确认。
- Jira 交互语言一致：写入 Jira 的标题、描述、评论、工作日志、证据正文、阻塞说明和补卡说明必须使用中文；字段名、状态名、`transition` 名称和卡片编号可以保留原始值。
- 不绕过门禁：任何推送、创建拉取请求、合并、发布和规则自动修改都必须人工确认。
- 不混淆资料边界：全局资料、安装目录、项目 AI 工作空间和任务产物必须分开。

## 3. 文档风格

文档应满足：

- 标题清晰，便于快速定位。
- 每个文档只承担一个主要职责。
- README 只写终态定位、核心模型、角色入口和稳定导航，不写阶段性成果清单。
- 目标、架构、规则、故事线和运行时设计只写终态形态、事实源、角色责任、门禁、安全边界、能力边界和稳定操作说明。
- 阶段性范围、阶段任务、已完成事项、当前实现状态、剩余工作、验收命令和实现说明只写入 `plans/`。
- 计划完成后保留为历史推进记录，不继续作为当前设计事实源；若计划内容已经变成长期规则，必须同步迁移到设计、规则、手册、契约或运行资产。
- 判断一句话归属时，先问它是在定义 AgenticOps 终态形态，还是只解释当前阶段先做什么、暂不做什么；后者必须进入 `plans/`。
- 规则文档使用明确的“必须 / 不得 / 应”。
- 设计文档说明背景、边界和取舍。
- 故事线写清 trigger、preconditions、main flow、output、failure handling 和 acceptance criteria。
- 示例使用脱敏、虚拟或安全内容。
- 不写 secrets、tokens、private keys、原始敏感日志。

## 4. 代码风格

Go CLI 运行时的实现方向如下：

- CLI 运行时使用 Go 作为主实现语言。
- shell 只用于 `curl | bash` 安装引导，不承载业务逻辑。
- CLI 入口统一为 `agentic-cli`。
- Go 代码应拆分为清晰的命令、契约、策略、适配器、工作空间和反馈模块，不写巨大单文件。
- stdout 只输出结构化 JSON。
- stderr 输出人类诊断日志。
- 所有错误必须有稳定 `code`。
- 写操作必须经过策略、门禁和人工确认检查。
- Linux (linux-amd64 / linux-arm64)、macOS Intel (darwin-amd64) 和 macOS Apple Silicon (darwin-arm64) 都必须通过对应平台二进制运行。
- 发布流程必须支持快速构建、发布和自更新。

## 5. 测试方向

实现代码时，测试应覆盖：

- 操作契约 schema 校验。
- 工作流配置必填字段校验。
- CLI JSON 输出格式。
- 错误码稳定性。
- secrets 脱敏。
- 人工门禁拦截。
- workspace 与 `~/.agentic-ops` 边界。
- feedback event 格式。
- Go 命令、策略和适配器的单元测试。

外部服务相关测试必须提供本地替代验证方式。

## 6. 变更纪律

当修改某个核心规则时，必须同步检查：

- `docs/project-rules.md`
- `docs/architecture/agenticops-current-design.md`
- `docs/user-stories/agenticops-user-stories.md`
- `docs/user-stories/project-maintainer-stories.md`
- `docs/user-stories/development-lead-stories.md`
- `handbooks/ai-employee-handbook.md`
- `docs/contracts/operation-contract.md`
- `docs/profiles/workflow-profile.md`
- `docs/workflows/feedback-loop.md`

如果只改其中一个文档导致规则不一致，必须继续修正相关文档。

## 7. 完成标准

不能用“看起来完成”作为完成标准。每次文档工作完成前必须：

- 检查文件是否存在。
- 检查标题结构。
- 搜索未完成占位标记。
- 检查新文档是否被 README 或相关索引引用。
- 检查实现是否仍遵守真实 Jira / GitHub 写操作、推送、创建拉取请求、合并和发布的人工门禁。
