# AgenticOps 开发风格

## 1. 目的

本文定义 AgenticOps 的开发风格和协作约束，用于提高 AIAgent 工作效率、减少幻觉、降低误改风险。

当前仓库已进入第一阶段本地实现。开发仍必须保持文档、契约、测试和代码同步，真实 Jira / GitHub 写操作、push、PR、merge 和发布不得在未确认前接入。

## 2. 开发原则

AgenticOps 开发必须遵守：

- 文档先行：先确认目标定位、项目规则、用户故事、Operation Contract、Workflow Profile 和 AI 员工手册。
- 小步交付：每次变更只解决一个明确设计问题。
- 证据优先：做结论前先读取当前文件、命令输出或权威文档。
- 不猜测外部事实：Jira 字段、GitHub 仓库、工作空间路径、权限和状态映射必须来自 profile、配置或用户确认。
- 不绕过门禁：任何 push、PR、merge、发布和规则自动修改都必须人工确认。
- 不混淆资料边界：全局资料、安装目录、项目 AI 工作空间和任务产物必须分开。

## 3. 文档风格

文档应满足：

- 标题清晰，便于快速定位。
- 每个文档只承担一个主要职责。
- 规则文档使用明确的“必须 / 不得 / 应”。
- 设计文档说明背景、边界和取舍。
- 用户故事写清 trigger、preconditions、main flow、output、failure handling 和 acceptance criteria。
- 示例使用脱敏、虚拟或安全内容。
- 不写 secrets、tokens、private keys、原始敏感日志。

## 4. 代码风格

第一阶段 Go CLI Runtime 的实现方向如下：

- CLI Runtime 第一阶段使用 Go 作为主实现语言。
- shell 只用于 `curl | bash` 安装引导，不承载业务逻辑。
- CLI 入口统一为 `agent-task-ops`。
- Go 代码应拆分为清晰的 command、contract、policy、adapter、workspace 和 feedback 模块，不写巨大单文件。
- stdout 只输出结构化 JSON。
- stderr 输出人类诊断日志。
- 所有错误必须有稳定 `code`。
- 写操作必须经过 policy、gate 和 confirmation 检查。
- Linux (linux-amd64 / linux-arm64)、macOS Intel (darwin-amd64) 和 macOS Apple Silicon (darwin-arm64) 都必须通过对应平台二进制运行。
- 发布流程必须支持快速构建、发布和自更新。

## 5. 测试方向

实现代码时，测试应覆盖：

- Operation Contract schema 校验。
- Workflow Profile 必填字段校验。
- CLI JSON 输出格式。
- 错误码稳定性。
- secrets 脱敏。
- human gate 拦截。
- workspace 与 `~/.agentic-ops` 边界。
- feedback event 格式。
- Go command、policy 和 adapter 的单元测试。

外部服务相关测试必须提供本地替代验证方式。

## 6. 变更纪律

当修改某个核心规则时，必须同步检查：

- `docs/project-rules.md`
- `docs/architecture/agenticops-current-design.md`
- `docs/user-stories/agenticops-user-stories.md`
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
- 检查实现是否仍遵守真实 Jira / GitHub 写操作、push、PR、merge 和发布的人工门禁。
