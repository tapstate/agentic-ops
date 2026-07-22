# AgenticOps 项目规则

## 1. 目的

本文定义 AgenticOps 项目开始阶段必须遵守的项目规则。规则用于约束 AgenticOps 源码、文档、AI 员工手册、Operation Contract、Workflow Profile、Go CLI Runtime、项目 AI 工作空间和反馈闭环。

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

第一阶段先落地研发 Jira 任务：帮助研发操作 AIAgent 从 Jira 接管任务到完成任务。不同任务必须先分类，再进入对应标准流程。AgenticOps 必须通过 Standard Process Registry、AI 员工手册、Operation Contract、Task Form Standard、Workflow Profile、Policy / Gate、Runbook、Templates、事件日志、evidence 和 feedback report 管理这些流程差异，让执行过程可恢复、可复盘、可分析，并把关键状态、关键信息、表单数据和审查结论回写到正确位置。

AgenticOps 必须遵守：

- 不替代 Jira。
- 不替代研发 owner。
- 不替代 PR Review。
- 不创建新的任务管理事实源。
- 不以全自动开发作为第一阶段目标。
- 不把某个具体 Jira workflow 硬编码为核心模型。
- 不把所有任务强行压成同一条固定执行流程。
- 不跳过任务分类直接执行开发。
- 不依赖员工记住所有标准流程细节。
- 不绕过研发 owner、reviewer、QA、运维、安全等专业角色在对应节点的审查责任。
- 不把尚未成熟的流程判断直接固化为脚本或 CLI 命令。

AgenticOps 第一阶段只追求跑通真实、可控、可复用的主链路：

```text
Jira issue 已进入迭代
-> 研发 owner 手动触发 AI
-> AI 拉取 owner 名下待办
-> 研发 owner 选择一个 issue
-> AI 识别任务分类并选择标准流程
-> AI 执行任务接管 gate
-> AI 生成 run_id 和接管记录
-> AI 本地开发与验证
-> AI 回写 Jira 证据
-> 研发 owner 确认
-> 授权 push / PR
-> 进入既有 CI / Review / 合入流程
```

## 3. 事实源

AgenticOps 必须保持事实源边界清晰：

- Jira 是任务、需求、owner、迭代、状态、评论和执行证据的事实源。
- Git 仓库是代码、测试、提交和分支的事实源。
- GitHub PR / CI 是 Review、CI、comments 和合入记录的事实源。
- AgenticOps 只提供执行控制、操作契约、证据模板和反馈闭环。

`run_id` 只用于追踪一次 AI 执行：

- 不替代 Jira issue key。
- 不替代 Jira 状态。
- 不要求研发 owner 手工填写。
- 必须能串联 Jira evidence、事件日志、测试结果、PR 和反馈分析。

执行记录必须覆盖：

- 当前任务类型。
- 当前任务分类和标准流程编号。
- 当前阶段。
- 下一步动作。
- 人工门禁状态。
- 当前节点表单状态。
- 专业审查结论。
- 重试和重做依据。
- 关键输入、关键输出和关键失败原因。
- 已回写的位置，例如 Jira evidence、PR comment、项目 AI 工作空间日志或 feedback report。

标准流程出问题时，处理优先级必须是：

- 能按 AI 员工手册、operation contract、workflow profile、policy、runbook 或 template 自助处理的，优先自助处理。
- 缺少 Jira 关键字段或上下文时，阻断接管并输出补全动作和模板。
- 标准资产不适配时，生成 profile、policy、template 或 runbook 的改进建议。
- 存在风险、权限不足、标准冲突或连续失败时，转人工确认。
- 只有确认问题来自 `agentic-cli` CLI 二进制逻辑错误时，才进入二进制修复发布路径。

## 4. 仓库边界

当前只有一个公司仓库作为 AgenticOps 的权威源头：

```text
git@github.com:tapstate/agentic-ops.git
```

该仓库管理全局通用资料：

```text
docs/          架构、目标定位、用户故事、流程、计划
assets/        安装后交付给研发 owner 和 AIAgent 使用的运行资产源头
contracts/     Operation Contract 和 schema
skills/        AgenticOps skills 和 AI 员工工作规则
handbooks/     AI 员工手册
profiles/      workflow profile 示例和默认配置
packages/      agentic-cli Go CLI runtime
templates/     Jira / PR / evidence 模板
examples/      端到端演示样例
tests/         自动化测试
scripts/       本地和 CI 辅助脚本
```

仓库内文档、目录和脚本文件名默认使用英文 ASCII lowercase-kebab-case。面向用户的正文优先使用中文。

同一个仓库内使用目录区分资料职责，不使用不同分支分管源码、设计、计划或运行资产。分支只用于开发协作、审阅和发布准备。正式交付时通过 release 包控制使用者可见内容，研发 owner 和 AIAgent 默认只接触安装后的命令、资产、模板和规范。

## 5. 安装边界

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
- operation contracts。
- 可安全重建的缓存。

`~/.agentic-ops` 不得保存：

- 具体业务任务的长期上下文。
- 业务仓库代码变更。
- 未脱敏的原始 Jira 内容。
- 未脱敏的测试日志。
- secrets、tokens、private keys。

安装入口第一阶段约定为：

```sh
curl -fsSL https://raw.githubusercontent.com/tapstate/agentic-ops/init.sh | bash
```

安装脚本必须支持 Linux (linux-amd64 / linux-arm64)、macOS Intel (darwin-amd64) 和 macOS Apple Silicon (darwin-arm64)，并且不得覆盖用户已有本地配置。

## 6. 项目 AI 工作空间边界

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
- workflow profile。
- 任务执行上下文。
- feedback 记录。

具体工作空间产物必须写入项目 AI 工作空间、目标业务仓库、Jira / PR evidence，或受控的任务执行记录位置。

建议项目 AI 工作空间事件目录：

```text
<project-ai-workspace>/
  .agentic-ops/
    runs/
    feedback/
```

## 7. AI 员工手册规则

AgenticOps 必须包含 AI 员工手册，并将其作为一等交付物。

AI 员工手册必须同时服务：

- AIAgent：明确任务类型、当前阶段、下一步动作、工具、流程、gate、证据和停止条件。
- 研发 owner：提供快捷操作方式，让研发能用自然语言或 CLI 指挥 AI 完成任务。

AI 员工手册必须覆盖：

- 任务类型：安装、工作空间初始化、AIAgent 初始化、新任务接管、恢复接管、PR comments 修复、工作日志上报、AgenticOps 改进建议。
- 任务分类：需求变更、缺陷修复、技术任务、排查分析和流程改进等标准分类。
- 阶段模型：已接收、预检中、等待接管、分析中、开发中、验证中、证据回写中、等待人工确认、阻塞、已交接。
- 下一步动作：由 operation contract、workspace profile、当前 evidence 和人工门禁共同决定。
- 工作入口：拉待办、任务接管、继续失败任务、修复 PR comments、回写证据。
- 行为边界：不自动 push、不自动创建 PR、不自动 merge、不扩大需求范围、不泄露敏感信息。
- 停止条件：需求不清、风险扩大、权限不足、测试无法运行、连续修复失败、需要人工判断。
- 交付要求：代码 diff、测试结果、风险说明、Jira / PR evidence、下一步建议。

所有 skills、operation contracts、workflow profiles、CLI 命令和 evidence templates 必须与 AI 员工手册保持一致。

## 8. 操作契约规则

AgenticOps 必须通过 Operation Contract 管理 AIAgent 可执行操作的输入、输出、失败模型和副作用。

AIAgent 不应直接面对 Jira 字段、Jira 状态、Jira transition 或 Jira comment 模板。AIAgent 必须面向稳定 operation 工作。

第一阶段核心 operation 包括：

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

每个 operation contract 必须定义：

- 操作名。
- 契约版本。
- 操作意图。
- 适用的任务类型。
- 允许执行该 operation 的阶段。
- 完成后建议的下一步动作。
- 结构化输入。
- 前置 gate。
- 结构化输出。
- 稳定失败码。
- 人工动作建议。
- 副作用。
- 是否需要人工确认。

写操作必须声明副作用。任何涉及 Jira 写入、Git commit、Git push、GitHub PR 创建或 PR 更新的 operation 必须经过 policy / gate / confirmation 检查。

## 9. 工作流配置规则

AgenticOps 核心绑定研发流程语义，不绑定某一套具体 Jira workflow。

Workflow Profile 负责把 Operation Contract 映射到具体项目流程。

Workflow Profile 必须能表达：

- Jira base URL、project、JQL。
- Jira Form Mapping，把 owner、sprint、acceptance criteria、target repo、risk 等 AgenticOps 标准字段映射到具体 Jira 字段、描述模板、评论模板或 workspace 配置。
- Jira 状态和 transition 映射。
- 专业审查节点和对应角色，例如研发 owner、reviewer、QA、运维或安全。
- 每个关键阶段允许重试还是必须重做前序表单。
- GitHub organization 和 repo 映射。
- 本地源码路径。
- 允许的写操作。
- 人工确认点。
- evidence 模板。

TapData / TapState 方案 C 可以作为第一套默认 profile，但不得硬编码进核心模型。

## 10. CLI 运行时规则

第一阶段控制层必须采用本地优先的 Go CLI Runtime。

shell 只用于安装引导，例如 `curl | bash` 的 `init.sh`。业务逻辑、operation、policy、adapter、日志和反馈分析不得写在 shell 中。

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
- 写操作必须检查 policy、gate 和 confirmation。
- secrets 不允许出现在 stdout、stderr 或事件日志中。
- Linux (linux-amd64 / linux-arm64)、macOS Intel (darwin-amd64) 和 macOS Apple Silicon (darwin-arm64) 都应通过对应平台二进制运行。

第一阶段主 CLI 发布目标：

```text
darwin-arm64
darwin-amd64
linux-amd64
linux-arm64
```

安装 bootstrap 允许依赖 `bash`、`curl` 和系统解压工具。`agentic-cli` 运行时不得依赖 `jq` 或本地 Python 环境。

`agentic-cli preflight` 必须检查 OS、CPU 架构、GitHub CLI、GitHub 登录状态、Jira 凭证、workspace profile 和当前业务仓库匹配关系。

CLI operation 和脚本入口必须遵守成熟度边界：

- 成熟固化的交互逻辑可以沉淀为原子化 operation。
- 脚本入口只做受控编排或调用，不承载 Jira、GitHub、Git、policy、gate、evidence 或 feedback 的业务判断。
- 原子 operation 必须输入输出稳定、失败码明确、副作用可审计。
- 原子 operation 必须能说明失败后应重试、重做、阻断还是转人工。
- 尚未稳定的流程判断必须先进入 runbook、workflow profile、policy 草案或 feedback proposal。
- 框架负责大的流程环节、门禁、状态和演进机制，不把每个任务的临场细节写死。
- AIAgent 在具体环节内执行任务并沉淀经验，周期性复盘再决定是否固化为标准资产。

## 11. Git 和 GitHub 规则

GitHub / Git 当前不会替换，因此不需要做可替换平台级抽象，但必须做安全操作级封装。

AIAgent 可以直接读取：

```text
git status
git diff
git log
git show
```

以下动作必须通过 AgenticOps operation 或 CLI guard 管控：

```text
git commit
git push
git merge
git rebase
git clean
gh pr create
gh pr edit
```

未经研发 owner 确认，AIAgent 不得执行 push、创建 PR、重新提交修复或 merge。

## 12. 人工门禁规则

以下动作必须暂停并等待人工确认：

- 任务接管前 owner 不匹配。
- 需求范围、验收标准、目标仓库或验证方式缺失。
- 实际影响范围超出 Jira 已确认边界。
- 需要改变复杂度、风险等级或需求范围。
- AI 连续修复失败或无法解释失败原因。
- push、创建 PR、重新提交修复。
- PR Review comments 存在需要取舍的修改。
- 合入、发布、线上风险相关动作。

AIAgent 必须能向研发 owner 说明暂停原因、当前 evidence、建议下一步和需要谁确认。

## 13. 反馈闭环规则

AgenticOps 必须包含 AIAgent 反馈通道，用于按天分析执行日志并优化 AgenticOps。

反馈闭环必须遵守：

```text
Go CLI 执行 operation
-> 产生结构化事件日志
-> 每天按 workspace 汇总
-> AIAgent 分析失败、卡点、重复人工确认、专业审查退回、重试、重做、有效经验和规则缺口
-> 生成改进建议
-> 人确认后更新 AgenticOps 规则 / 手册 / contracts / Go CLI
```

第一阶段反馈通道只做分析和建议，不允许 AIAgent 根据日志自动修改 AgenticOps 源头规则。

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

## 14. 安全规则

严禁提交或持久化：

- secrets
- tokens
- private keys
- 真实 `.env`
- 原始敏感日志
- 未脱敏 Jira 原文
- 未脱敏业务代码片段

本地凭证只能通过被忽略的环境文件、系统凭证管理或运行时注入。

Jira / GitHub 写操作必须可审计。任何写操作都必须关联 operation、workspace、issue key、run_id、task_type、current_stage、next_action 和事件日志。

## 15. 文档规则

第一阶段至少维护：

- 目标定位文档。
- 设计审阅清单。
- 设计决策记录。
- 项目规则文档。
- 用户故事文档。
- 当前设计文档。
- 项目开发风格文档。
- AIAgent 防幻觉工作规则。
- AI 员工手册。
- Operation Contract 文档。
- Workflow Profile 说明。
- Feedback Loop 说明。
- 端到端演示脚本。
- CLI Runtime 设计说明。
- Evidence Templates 设计说明。

文档必须保持简洁、可执行、便于试点研发直接使用。

面向用户、研发 owner 和审阅者的可见文档标题默认使用中文。只有以下内容保留英文或缩写：

- 产品名、角色名和工具名，例如 `AgenticOps`、`AIAgent`、`Jira`、`GitHub`、`CLI`。
- 命令、配置字段、协议字段、文件名和目录名。
- 用户故事、任务或契约的稳定编号，例如 `US-001`、`run_id`。

Jira 交互中的人可见内容必须使用中文，包括标题、描述、评论、工作日志、evidence 正文、阻塞说明和补卡说明。Jira 字段名、状态名、transition 名称、issue key、命令、配置字段和协议字段可以保留原始英文或缩写。

当规则变化影响 AIAgent 行为时，必须同步更新：

- AI 员工手册。
- Operation Contract。
- CLI 命令说明。
- 用户故事或验收标准。
- AIAgent 工作规则。
- 项目开发风格。

## 16. 第一阶段验收

第一阶段最低验收标准：

- 研发 owner 能通过安装命令完成安装。
- 研发 owner 能初始化项目 AI 工作空间。
- AIAgent 能初始化 AgenticOps 能力。
- AI 能列出 owner 名下 Jira 待办。
- AI 能接管一个 issue，并执行 gate。
- 接管成功或失败都能写入结构化 Jira evidence。
- AI 能完成一次真实或接近真实的代码修改。
- AI 能运行最小验证并回写结果。
- AI 完成后停在人工确认点。
- 研发 owner 确认后再 push / PR。
- 每次 operation 都有结构化事件日志。
- 每天能生成 feedback report 和改进建议。
