# AgenticOps 开发风格

## 1. 目的

本文定义 AgenticOps 的开发风格和协作约束，用于提高 AIAgent 工作效率、减少幻觉、降低误改风险。

开发必须保持文档、契约、测试和代码同步。真实 Jira / GitHub 写操作、推送、创建拉取请求、合并和发布必须经过策略、门禁和人工确认；AgenticOps 源头仓库按正式分支与发布流程执行。

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
- 阶段性范围、任务、已完成事项、当前状态、剩余工作、验收命令和实现说明只写入 Jira。
- 实施中形成的长期规则必须同步迁移到设计、Rule、标准、契约或运行资产；不把 Jira 计划复制为仓库 Markdown。
- 判断一句话归属时，先问它是在定义 AgenticOps 终态形态，还是只解释当前阶段先做什么、暂不做什么；后者必须进入 Jira。
- 规则文档使用明确的“必须 / 不得 / 应”。
- 设计文档说明背景、边界和取舍。
- 故事线写清 trigger、preconditions、main flow、output、failure handling 和 acceptance criteria。
- 示例使用脱敏、虚拟或安全内容。
- 不写 secrets、tokens、private keys、原始敏感日志。

## 4. 代码风格

目标运行时的实现方向如下：

- Python Runtime 是结构化操作层，版本由 `.python-version` 固定；两个工作面的依赖分别由 `maintainer/pyproject.toml`、`developer/pyproject.toml` 与各自 `uv.lock` 锁定。
- Shell Bootstrap 不承载安装后 AIAgent 的业务逻辑，只负责 developer-only 安装、更新、回滚、环境准备和 `ao-work` 启动。维护 AgenticOps 源头仓库时，`maintainer/scripts/release.sh`、`maintainer/scripts/hotfix.sh` 和共享库可以作为项目级发布编排例外。
- 工作面入口固定为 maintainer 的 `ao-maint` 和 developer 的 `ao-work`，不得保留统一兼容入口或 mode 切换。
- Python 代码应拆分为清晰的命令、配置、契约、任务状态、工作流、适配器、证据和反馈模块，不写巨大单文件。
- stdout 只输出结构化 JSON。
- stderr 输出中文诊断日志。
- 所有错误必须有稳定 `code`。
- 写操作必须经过策略、门禁和人工确认检查。
- **异常必须留痕，禁止静默丢弃**：任何 `except` 分支都必须至少写入一条诊断或结构化事件（stderr 中文诊断、事件日志或错误码输出），不得只 `return None` / `pass` 吞掉异常而不留任何痕迹；确属可预期的降级路径（如可选配置缺失、可选字段探测失败）也要记录一次结构化降级说明。异常路径的 `required_human_action` / `retry_safe` / `code` 必须完整。
- **异常留痕必须防日志暴涨（仅限循环/批量/高频路径）**：先判断异常是否在循环、批量遍历或高频调用内产生；单次路径（命令入口、单文件处理、一次性操作）的异常直接留痕完整信息即可，不需要风暴防护。只有循环/批量/高频路径内的异常才采用「首次完整 + 后续计数/采样/摘要」策略——同一 `code` 在同一批量操作内首次留痕完整信息，后续只追加去重计数或最近一次摘要，并设每批上限（如最多 N 条、超过后只输出汇总计数）；不得用 `print` 直接输出原始异常对象。
- 本地任务状态由 Runtime 使用 JSON / NDJSON、schema 版本、任务级锁和原子替换维护；人工配置和标准资产可以使用 YAML / Markdown。
- Linux 和 macOS 必须通过同一锁定 Python 主链路运行，不构建项目自有平台二进制。
- 发布流程必须支持从稳定 `main` 快速更新、失败回滚和原场景复验。
- GitHub 默认分支是 `main`，日常开发使用 `develop`；流程禁止直提直推 `main`，只允许通过 PR 的 Merge commit 合入。硬门禁可用时由 Ruleset 强制；GitHub Free 私有仓库使用显式软门禁，并保留“无法从服务器端阻止其它入口直推”的风险提示。
- 正常发布与 Hotfix 必须分别使用统一脚本入口，固定执行完整验证、最终确认、合并事实校验和审计。

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
- Python 命令、状态、策略和适配器的单元与契约测试。
- 资源合同测试必须确认旧 Go Runtime、`agentic-cli`、`install-resources/` 和根目录旧运行路径没有残留。
- developer-only 安装边界、更新、回滚和发布门禁回归。

外部服务相关测试必须提供本地替代验证方式。

## 6. 变更纪律

当修改某个核心规则时，必须同步检查：

- `docs/project-rules.md`
- `docs/architecture/agenticops-current-design.md`
- `docs/user-stories/agenticops-user-stories.md`
- `docs/user-stories/project-maintainer-stories.md`
- `docs/user-stories/development-engineer-stories.md`
- `developer/AGENTS.md`
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
- 检查实现是否仍遵守真实 Jira / GitHub 写操作、推送、创建拉取请求、合并和发布的人工门禁，以及 `main` PR-only 规则。
