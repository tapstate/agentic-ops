# AgenticOps 项目规则

## 1. 目的

本文定义 AgenticOps 项目开始阶段必须遵守的项目规则。规则用于约束 AgenticOps 源码、文档、AI 员工手册、操作契约、工作流配置、Go CLI 运行时、项目 AI 工作空间和反馈闭环。

相关设计文档：

- `docs/architecture/agenticops-current-design.md`
- `docs/user-stories/agenticops-user-stories.md`
- `docs/development-style.md`
- `docs/ai-working-rules.md`
- `docs/processes/standard-process-registry.md`
- `docs/strategy/positioning.md`
- `docs/runtime/cli-runtime.md`
- `docs/runtime/problem-resolution-and-update.md`
- `docs/templates/evidence-templates.md`
- `docs/examples/end-to-end-demo.md`

## 2. 产品边界

AgenticOps 是把公司员工执行标准沉淀成 AI 可执行标准流程的 AI 执行控制体系。

AgenticOps 先以研发 Jira 任务为主要落地场景：帮助研发负责人操作 AIAgent 从 Jira 接管任务到完成任务。不同任务必须先分类，再进入对应标准流程。AgenticOps 必须通过 Standard Process Registry、AI 员工手册、操作契约、Task Form Standard、工作流配置、策略门禁、运行手册、模板、事件日志、证据和反馈报告管理这些流程差异，让执行过程可恢复、可复盘、可分析，并把关键状态、关键信息、表单数据和审查结论回写到正确位置。

AgenticOps 必须遵守：

- 不替代 Jira。
- 不替代研发负责人。
- 不替代 PR 审查。
- 不创建新的任务管理事实源。
- 不以绕过人工授权、专业审查和策略门禁的全自动开发作为目标。
- 不把某个具体 Jira 工作流硬编码为核心模型。
- 不把所有任务强行压成同一条固定执行流程。
- 不跳过任务分类直接执行开发。
- 不依赖员工记住所有标准流程细节。
- 不绕过研发负责人、代码审查人、QA、运维、安全等专业角色在对应节点的审查责任。
- 不把尚未成熟的流程判断直接固化为脚本或 CLI 命令。

AgenticOps 的主链路必须真实、可控、可复用：

```text
Jira 卡片已进入迭代
-> 研发负责人手动触发 AI
-> AI 拉取 负责人名下待办
-> 研发负责人选择一个卡片
-> AI 识别任务分类并选择标准流程
-> AI 执行任务接管门禁
-> AI 生成 `run_id` 和接管记录
-> AI 本地开发与验证
-> AI 回写 Jira 证据
-> 研发负责人确认
-> 授权推送 / 创建拉取请求
-> 进入既有 CI / 审查 / 合入流程
```

## 3. 文档与计划边界

AgenticOps 文档必须按职责分层：

- `README.md` 只承担终态定位、核心模型、角色入口和稳定目录导航，不记录阶段性成果清单。
- `docs/architecture/` 定义稳定架构边界，包括流程环节、门禁、状态、容错、事实源、角色责任、安全边界、能力边界和标准资产演进机制。
- `docs/runtime/` 记录运行时设计、命令能力、当前实现边界、正式化缺口和操作说明。
- `plans/` 基于稳定架构从大阶段拆到中任务和小步骤，用 checkbox 跟踪实施进度。
- 阶段、任务、checkbox、验收命令、当前实现状态、剩余工作和 Implementation note 只能写入 `docs/README.md`、`docs/runtime/` 或 `plans/`，不得混入 README 主叙事或架构设计主叙事。
- 设计文档发现缺口时，只能说明能力边界、风险和约束；如果缺口背后涉及产品、流程、权限或事实源取舍，必须明确标记为需要用户决策，不得伪装成默认计划或默认实现。

做任何计划前，必须先确认其所依赖的架构文档已经存在且相对稳定。架构不清时，应先更新或补齐架构，再拆实施计划；不得直接用零散功能点堆砌计划。

## 4. 事实源

AgenticOps 必须保持事实源边界清晰：

- Jira 是任务、需求、负责人、迭代、状态、评论和执行证据的事实源。
- Git 仓库是代码、测试、提交和分支的事实源。
- GitHub 拉取请求与 CI 是拉取请求审查、CI、审查评论和合入记录的事实源。
- AgenticOps 只提供执行控制、操作契约、证据模板和反馈闭环。

`run_id` 只用于追踪一次 AI 执行：

- 不替代 Jira 卡片编号。
- 不替代 Jira 状态。
- 不要求研发负责人手工填写。
- 必须能串联 Jira 证据、事件日志、测试结果、拉取请求和反馈分析。

`agent_id` 和 `current_agent_id` 属于所有权控制：

- `agent_id` 标识一个 AIAgent 身份，同一个 `agent_id` 可以产生多个 `run_id`。
- `current_agent_id` 是任务当前绑定的 `agent_id`，用于所有权门禁和并发冲突检测。
- 同一个 Jira 卡片可以有多个历史 `run_id`，但同一时刻最多只能有一个有效的 `current_agent_id`。
- 任务完成或明确交接结束后，必须清理 `current_agent_id`；清理动作不删除历史 `run_id`。

执行记录必须覆盖：

- 当前任务类型 `task_type`。
- 当前任务分类 `task_class` 和标准流程编号 `process_id`。
- 当前阶段 `current_stage`。
- 下一步动作 `next_action`。
- 人工门禁状态。
- 当前节点表单状态。
- 专业审查结论。
- 重试和重做依据。
- 关键输入、关键输出和关键失败原因。
- 已回写的位置，例如 Jira 证据、拉取请求评论、项目 AI 工作空间日志或反馈报告。

标准流程出问题时，处理优先级必须是：

- 能按 AI 员工手册、操作契约、工作流配置、策略、运行手册或模板自助处理的，优先自助处理。
- 缺少 Jira 关键字段或上下文时，阻断接管并输出补全动作和模板。
- 标准资产不适配时，生成工作流配置、策略、模板或运行手册的改进建议。
- 存在风险、权限不足、标准冲突或连续失败时，转人工确认。
- 只有确认问题来自 `agentic-cli` CLI 二进制逻辑错误时，才进入二进制修复发布路径。

## 5. 仓库边界

当前只有一个公司仓库作为 AgenticOps 的权威源头：

```text
git@github.com:tapstate/agentic-ops.git
```

该仓库管理全局通用资料：

```text
docs/          架构、目标定位、用户故事、流程、计划
assets/        安装后交付给研发负责人和 AIAgent 使用的运行资产源头
contracts/     操作契约和结构定义
skills/        AgenticOps 技能和 AI 员工工作规则
handbooks/     AI 员工手册
profiles/      工作流配置示例和默认配置
packages/      agentic-cli Go CLI 运行时
templates/     Jira / 拉取请求 / 证据模板
examples/      端到端演示样例
tests/         自动化测试
scripts/       本地和 CI 辅助脚本
```

仓库内文档、目录和脚本文件名默认使用英文 ASCII lowercase-kebab-case。面向用户的正文优先使用中文。

同一个仓库内使用目录区分资料职责，不使用不同分支分管源码、设计、计划或运行资产。正式交付时通过 release 包控制使用者可见内容，研发负责人和 AIAgent 默认只接触安装后的命令、资产、模板和规范。

当前项目规则只适用于 `tapstate/agentic-ops` 项目本身。不得把其它项目的研发规范、分支策略、验证命令、目录约定或上线前临时规则合并进 AgenticOps 当前项目规则。

不同项目的 AI 工作空间必须分开维护。AgenticOps 源头仓库、全局安装目录 `~/.agentic-ops`、以及 `tapstate`、`tapdata` 等具体项目 AI 工作空间不能混用；只有明确标注为跨项目通用资产的规则，才可以沉淀到 AgenticOps 通用资料中。

当前项目维护规范只约束维护 `tapstate/agentic-ops` 源头仓库的维护者或项目维护代理，不等同于安装后 AIAgent 执行业务 Jira 任务的运行规范。

安装后 AIAgent 的执行规范必须维护在 AI 员工手册、操作契约、工作流配置、运行资产、模板和对应运行文档中。不得把当前项目研发期规则、提交规则、分支规则或仓库维护流程直接套用为 AIAgent 运行期执行规范；也不得把某个业务项目的 AIAgent 执行细则反向写成 AgenticOps 当前项目维护规则。

## 6. 项目研发期规则

AgenticOps 第一个版本发布正式上线前的临时规范统一维护在 `docs/development-phase-rules.md`。正式上线后，应删除本节或解除对该文档的依赖，再把仍需长期保留的内容迁移到对应永久规则区块。

## 7. 安装边界

AgenticOps 默认安装到：

```text
~/.agentic-ops
```

`~/.agentic-ops` 是用户本机的全局安装和配置目录，不是具体项目或具体任务的运行目录。

`~/.agentic-ops` 可以保存：

- AgenticOps release 二进制和安装元数据。
- 全局配置。
- 通用 AI 员工手册。
- 通用 skills。
- 通用 templates。
- 操作契约。
- 可安全重建的缓存。

`~/.agentic-ops` 不得保存：

- 具体业务任务的长期上下文。
- 业务仓库代码变更。
- 未脱敏的原始 Jira 内容。
- 未脱敏的测试日志。
- secrets、tokens、private keys。

安装入口约定为：

```sh
curl -fsSL https://raw.githubusercontent.com/tapstate/agentic-ops/init.sh | bash
```

安装脚本必须支持 Linux (linux-amd64 / linux-arm64)、macOS Intel (darwin-amd64) 和 macOS Apple Silicon (darwin-arm64)，并且不得覆盖用户已有本地配置。

## 8. 项目 AI 工作空间边界

具体项目的运行目录必须是对应项目 AI 工作空间，例如：

```text
tapstate/
tapdata/
```

不同项目 AI 工作空间可以对应不同：

- Jira 空间。
- GitHub organization。
- GitHub repositories。
- 本地源码目录。
- 工作流配置。
- 任务执行上下文。
- feedback 记录。

具体工作空间产物必须写入项目 AI 工作空间、目标业务仓库、Jira / PR 证据，或受控的任务执行记录位置。

建议项目 AI 工作空间事件目录：

```text
<project-ai-workspace>/
  .agentic-ops/
    runs/
    feedback/
```

## 9. AI 员工手册规则

AgenticOps 必须包含 AI 员工手册，并将其作为一等交付物。

AI 员工手册必须同时服务：

- AIAgent：明确任务类型、当前阶段、下一步动作、工具、流程、门禁、证据和停止条件。
- 研发负责人：提供快捷操作方式，让研发能用自然语言或 CLI 指挥 AI 完成任务。

AI 员工手册必须覆盖：

- 任务类型：安装、工作空间初始化、AIAgent 初始化、新任务接管、恢复接管、拉取请求审查意见修复、任务完成审计、AgenticOps 改进建议。
- 任务分类：需求变更、缺陷修复、技术任务、排查分析和流程改进等标准分类。
- 阶段模型：`已接收`、`预检中`、`等待接管`、`分析中`、`开发中`、`验证中`、`证据回写中`、`等待人工确认`、`阻塞`、`已交接`。
- 下一步动作：由操作契约、工作流配置、当前证据和人工门禁共同决定。
- 工作入口：拉待办、任务接管、继续失败任务、修复拉取请求审查意见、回写证据。
- 行为边界：不自动推送、不自动创建拉取请求、不自动合并、不扩大需求范围、不泄露敏感信息。
- 停止条件：需求不清、风险扩大、权限不足、测试无法运行、连续修复失败、需要人工判断。
- 交付要求：代码差异、测试结果、风险说明、Jira / 拉取请求证据、下一步建议。

所有技能、操作契约、工作流配置、CLI 命令和证据模板必须与 AI 员工手册保持一致。

## 10. 操作契约规则

AgenticOps 必须通过操作契约管理 AIAgent 可执行操作的输入、输出、失败模型和副作用。

AIAgent 不应直接面对 Jira 字段、Jira 状态、Jira `transition` 或 Jira 评论模板。AIAgent 必须面向稳定操作工作。

核心操作包括：

```text
install
workspace_init
agent_init
list_tasks
takeover_task
resume_takeover
read_task_context
write_evidence
mark_blocked
request_owner_confirmation
prepare_pr
fix_pr_comments
feedback_collect
feedback_analyze
feedback_report
feedback_propose
```

每个操作契约必须定义：

- 操作名。
- 契约版本。
- 操作意图。
- 适用的任务类型。
- 允许执行该操作的阶段。
- 完成后建议的下一步动作。
- 结构化输入。
- 前置门禁。
- 结构化输出。
- 稳定失败码。
- 人工动作建议。
- 副作用。
- 是否需要人工确认。

写操作必须声明副作用。任何涉及 Jira 写入、Git 提交、Git 推送、GitHub 拉取请求创建或拉取请求更新的操作必须经过策略、门禁和人工确认检查。

## 11. 工作流配置规则

AgenticOps 核心绑定研发流程语义，不绑定某一套具体 Jira 工作流。

工作流配置负责把 操作契约映射到具体项目流程。

工作流配置必须能表达：

- Jira `base_url`、Jira 项目、JQL。
- Jira Form Mapping，把 `owner`、`sprint`、`acceptance_criteria`、`target_repo`、`risk` 等 AgenticOps 标准字段映射到具体 Jira 字段、描述模板、评论模板或工作空间配置。
- Jira 状态和 `transition` 映射。
- 专业审查节点和对应角色，例如研发负责人、代码审查人、QA、运维或安全。
- 每个关键阶段允许重试还是必须重做前序表单。
- GitHub 组织和代码仓库映射。
- 本地源码路径。
- 允许的写操作。
- 人工确认点。
- 证据模板。

TapData / TapState 方案 C 可以作为第一套默认工作流配置，但不得硬编码进核心模型。

## 12. CLI 运行时规则

控制层必须采用本地优先的 Go CLI 运行时。

shell 只用于安装引导，例如 `curl | bash` 的 `init.sh`。业务逻辑、操作、策略、适配器、日志和反馈分析不得写在 shell 中。

统一入口为：

```sh
agentic-cli
```

推荐安装位置：

```text
~/.agentic-ops/bin/agentic-cli
```

CLI 必须遵守：

- stdout 只输出结构化 JSON。
- stderr 输出人类诊断日志。
- 所有失败返回稳定 `code`。
- 退出码有固定语义。
- 写操作必须检查策略、门禁和人工确认。
- secrets 不允许出现在 stdout、stderr 或事件日志中。
- Linux (linux-amd64 / linux-arm64)、macOS Intel (darwin-amd64) 和 macOS Apple Silicon (darwin-arm64) 都应通过对应平台二进制运行。

主 CLI 发布目标：

```text
darwin-arm64
darwin-amd64
linux-amd64
linux-arm64
```

安装 bootstrap 允许依赖 `bash`、`curl` 和系统解压工具。`agentic-cli` 运行时不得依赖 `jq` 或本地 Python 环境。

`agentic-cli preflight` 必须检查 OS、CPU 架构、GitHub CLI、GitHub 登录状态、Jira 凭证、工作流配置和当前业务仓库匹配关系。

CLI 操作和脚本入口必须遵守成熟度边界：

- 成熟固化的交互逻辑可以沉淀为原子化操作。
- 脚本入口只做受控编排或调用，不承载 Jira、GitHub、Git、策略、门禁、证据或反馈的业务判断。
- 原子操作必须输入输出稳定、失败码明确、副作用可审计。
- 原子操作必须能说明失败后应重试、重做、阻断还是转人工。
- 尚未稳定的流程判断必须先进入运行手册、工作流配置、策略草案或反馈建议。
- 框架负责大的流程环节、门禁、状态和演进机制，不把每个任务的临场细节写死。
- AIAgent 在具体环节内执行任务并沉淀经验，周期性复盘再决定是否固化为标准资产。

## 13. Git 和 GitHub 规则

GitHub / Git 当前不会替换，因此不需要做可替换平台级抽象，但必须做安全操作级封装。

AIAgent 可以直接读取：

```text
git status
git diff
git log
git show
```

以下动作必须通过 AgenticOps 操作或 CLI 防护管控：

```text
git commit
git push
git merge
git rebase
git clean
gh pr create
gh pr edit
```

未经研发负责人确认，AIAgent 不得执行推送、创建拉取请求、重新提交修复或合并。

## 14. 人工门禁规则

以下动作必须暂停并等待人工确认：

- 任务接管前 负责人不匹配。
- 需求范围、验收标准、目标仓库或验证方式缺失。
- 实际影响范围超出 Jira 已确认边界。
- 需要改变复杂度、风险等级或需求范围。
- AI 连续修复失败或无法解释失败原因。
- 推送、创建拉取请求、重新提交修复。
- 拉取请求审查意见存在需要取舍的修改。
- 合入、发布、线上风险相关动作。

AIAgent 必须能向研发负责人说明暂停原因、当前证据、建议下一步和需要谁确认。

## 15. 反馈闭环规则

AgenticOps 必须包含 AIAgent 反馈通道，用于在任务完成、阻塞或交接时提交任务级审计记录，并在需要时按执行记录分析和优化 AgenticOps。

反馈闭环必须遵守：

```text
Go CLI 执行操作
-> 产生结构化事件日志
-> 到达完成、阻塞或交接节点
-> AIAgent 提交任务级审计记录到 Jira 卡片、审计服务或目标仓库证据链
-> 维护者按需按运行、任务类型、失败码、时间范围或工作空间聚合分析
-> AIAgent 分析失败、卡点、重复人工确认、专业审查退回、重试、重做、有效经验和规则缺口
-> 生成改进建议
-> 人确认后更新 AgenticOps 规则、手册、契约和 Go CLI
```

反馈通道只做分析和建议，不允许 AIAgent 根据日志自动修改 AgenticOps 源头规则。

事件日志必须写入具体项目 AI 工作空间：

```text
<project-ai-workspace>/
  .agentic-ops/
    runs/
    feedback/
```

事件日志必须使用安全摘要，不得记录 secrets、原始敏感日志、完整 Jira 描述或敏感代码片段。

反馈进入 AgenticOps 源头规则前必须经过：

```text
Observation -> Proposal -> Accepted Change
```

## 16. 安全规则

严禁提交或持久化：

- secrets
- tokens
- private keys
- 真实 `.env`
- 原始敏感日志
- 未脱敏 Jira 原文
- 未脱敏业务代码片段

本地凭证只能通过被忽略的环境文件、系统凭证管理或运行时注入。

Jira / GitHub 写操作必须可审计。任何写操作都必须关联 `operation`、`workspace`、`issue_key`、`run_id`、`task_type`、`current_stage`、`next_action` 和事件日志。

## 17. 文档规则

项目至少维护：

- 目标定位文档。
- 设计审阅清单。
- 设计决策记录。
- 项目规则文档。
- 用户故事文档。
- 当前设计文档。
- 项目开发风格文档。
- AIAgent 防幻觉工作规则。
- AI 员工手册。
- 操作契约文档。
- 工作流配置说明。
- 反馈闭环说明。
- 端到端演示脚本。
- CLI 运行时设计说明。
- 证据模板设计说明。

文档必须保持简洁、可执行、便于试点研发直接使用。

面向用户、研发负责人和审阅者的可见文档标题和正文默认使用中文。只有以下内容保留英文或缩写：

- 属性名、状态名、配置键、协议字段和错误码，例如 `run_id`、`current_agent_id`、`side_effects`、`missing_form_field`。
- 命令、参数、文件路径、目录名和代码符号，例如 `agentic-cli workspace init`、`--jira-project`、`contracts/operations/`。
- 产品名、平台名、组件名和行业通用稳定名词，例如 `AgenticOps`、`AIAgent`、`Jira`、`GitHub`、`CI`、`CLI`。
- 用户故事、任务或契约的稳定编号，例如 `US-001`。

中文正文使用“研发负责人”“流程负责人”“代码审查人”等中文角色名。只有在字段名、配置项、协议字段、代码示例或模板占位符中，才保留 `owner`、`reviewer` 等英文标识。自然描述中的动作、职责、流程、证据、门禁、策略、模板、运行手册和反馈报告必须使用中文；例如描述动作时写“推送”“合并”“创建拉取请求”，只有引用命令时才写 `git push`、`git merge` 或 `gh pr create`。

Jira 交互中的人可见内容必须使用中文，包括标题、描述、评论、工作日志、证据正文、阻塞说明和补卡说明。Jira 字段名、状态名、`transition` 名称、卡片编号、命令、配置字段和协议字段可以保留原始英文或缩写。

AgenticOps 提交信息推荐格式为 `<type>(<scope>): <subject>`。`type` 和 `scope` 使用英文；`subject` 使用中文，简洁说明本次提交做了什么。commit body / description 使用中文，说明做了什么、解决什么问题以及为什么这样做。非平凡提交必须包含中文 commit body / description。

当规则变化影响 AIAgent 行为时，必须同步更新：

- AI 员工手册。
- 操作契约。
- CLI 命令说明。
- 用户故事或验收标准。
- AIAgent 工作规则。
- 项目开发风格。
