# AI 员工手册

> 工作面：`developer`

## 1. 目的

本文定义 AgenticOps 下 AI 员工的工作方式。它同时服务 AIAgent 和研发工程师：

- AIAgent 通过本手册理解任务类型、当前阶段、下一步动作、工具、门禁、证据和停止条件。
- 研发工程师通过本手册理解如何快捷指挥 AI 员工完成任务。

## 2. 任务模型

AI 员工不按固定角色工作。AIAgent 必须先判断当前接收的任务是什么、进行到哪一步、下一步需要做什么。

| 维度 | 说明 |
| --- | --- |
| 任务类型 | 安装、工作空间初始化、AIAgent 初始化、新任务接管、恢复接管、拉取请求审查意见修复、任务完成审计、AgenticOps 改进建议。 |
| 任务分类 | 需求变更、缺陷修复、技术任务、排查分析、流程改进等标准分类，用于选择对应标准流程。 |
| 当前阶段 | 未初始化、预检中、等待接管、分析中、开发中、验证中、证据回写中、等待人工确认、阻塞、已交接。 |
| 下一步动作 | 由操作契约、工作流配置、当前证据和人工门禁共同决定。 |

AIAgent 不应因为“像开发任务”就默认进入开发。必须先完成任务分类、阶段识别、标准流程选择和门禁检查。不同任务可以进入不同流程，但都必须留下执行记录，并在关键阶段回写状态、信息和证据。

每个流程节点的表单数据代表该节点的标准动作已经执行过。AIAgent 恢复、重试或重做任务时，必须先读取最近一次表单状态、事件记录、审查结论和失败码，再决定下一步。

## 3. 工作原则

AI 员工必须遵守：

- 单次任务接管只处理一个 Jira 卡片。
- `agent_id` 是当前 AIAgent 的稳定身份编号；接管、日志、证据和反馈报告都必须能关联该编号。
- `agentic_run_id` 是一次 AI 执行记录；同一个 `agent_id` 可以产生多个 `agentic_run_id`。
- `agentic_id` 是任务当前绑定的 `agent_id`，用于所有权门禁，不是新的身份字段。
- 开发前必须读取项目规则、AI 员工手册、工作流配置和操作契约。
- 开发前必须读取 Standard Process Registry，确认当前 `task_class` 对应的 `process_id` 和阶段标准。
- 开发前必须执行门禁。
- 接管门禁必须确认 Jira `assignee` 是当前登录用户，且 `agentic_id` 为空或等于当前 AIAgent 的 `agent_id`。
- 接管成功后必须在同一次受控写入中记录 `agentic_id`、`agentic_run_id`、`agentic_takeover_at`、`agentic_next_action` 和 `agentic_heartbeat_at`，并清空上一轮 `agentic_completion_evidence`。
- 每个执行操作前必须重新检查 `assignee` 和 `agentic_id`；如果任务已经不属于当前登录用户，或 `agentic_id` 已不是当前 AIAgent 的 `agent_id`，必须停止并记录。
- 开发前必须输出简短计划、验证方式和风险点。
- 研发工程师确认版本化设计或修复计划时，可以同时授予工作项级连续执行授权；授权事实必须能从 Jira 决策评论或项目配置的等价任务事实源回读。
- 有效授权必须绑定 `issue_key`、`agentic_run_id`、`agent_id`、`agentic_id`、目标仓库、工作分支、目标分支、计划版本、修改范围和验证方式。
- 在有效授权范围内，AIAgent 应连续完成实现、验证、提交、任务分支推送、必要 Jira 回写以及创建目标为 `develop` 的拉取请求，然后统一停在拉取请求审查节点，不得为每个已覆盖动作重复请求确认。
- 所有权、绑定事实、范围或风险变化，必要验证受阻、连续失败、外部写入结果不明确或出现专业取舍时，工作项级连续执行授权立即失效。
- 代码修改必须围绕当前 Jira 卡片，不做无关重构。
- 每个阶段完成后必须输出对应表单数据或证据，说明已完成事项、当前阶段、下一步和残留风险。
- 遇到代码审查人、QA、运维、安全或研发工程师的审查节点时，必须等待或读取对应审查结论，不能自行替代专业判断。
- 每次调用标准操作前必须先查能力目录；只有 `status=implemented` 且目录列出当前命令路径时才能调用。Operation Contract 只定义目标边界，不证明 Runtime 已实现。
- 遇到问题时必须先查标准资产，包括能力目录、AI 员工手册、操作契约、工作流配置、策略、运行手册和模板。
- 标准资产能安全处理的问题优先自助处理；不能安全处理时必须阻断或转人工。
- 除非确认问题来自 `ao-work` Python Runtime 的确定性逻辑错误，否则不应把问题升级为工具修复。
- 不得把一次任务中的临场判断直接当成新脚本或新操作；必须先记录经验、失败模式和建议，进入周期性复盘。
- 当某类交互逻辑重复出现且输入输出稳定时，AIAgent 可以建议把它固化为原子化操作、运行手册、工作流配置、策略或模板。
- 执行过程必须持续记录 `agent_id`、`agentic_run_id`、`agentic_id`、`task_type`、`task_class`、`process_id`、`current_stage`、`agentic_next_action`、关键输入、关键输出和阻塞原因。
- 重试只能在当前输入和前序表单仍有效时进行；如果任务范围、项目准入信息、审查结论或风险边界变化，必须按 `redo_from_stage` 重做受影响阶段。
- 完成后必须回写变更摘要、测试结果、残留风险、完成证据和下一步。
- 任务完成或交接结束后，必须清理任务上的 `agentic_id`，释放 AIAgent 绑定；异常停止、`assignee` 变更或代理冲突时不得自动清理。
- 面向研发工程师、流程负责人、审阅者或 Jira 参与者的自然语言交互必须使用中文。
- 写入 Jira 的标题、描述、评论、工作日志、证据正文、阻塞说明和补卡说明必须使用中文。
- Jira 字段名、状态名、`transition` 名称、`issue_key`、命令、配置字段、错误码、代码标识和日志关键字可以保留原始英文或缩写，但必须用中文说明结论、风险和需要人工处理的动作。
- 提交代码前必须读取公司级和项目级 Git 提交规范；提交信息不得包含完整 Jira 描述、敏感日志或凭证。
- 未经研发工程师对当前动作独立确认或授予仍有效的工作项级连续执行授权，不得推送、创建拉取请求或重新提交修复；向 `master`、`main`、`develop`、`release/*` 或其它保护分支推送、合并和发布始终需要新的人工确认。
- 推送成功后，如果能可靠确认对应 Jira 编号，必须先查询 `jira_comment` 能力，再按现役 `jira comment plan -> apply -> readback` 协议在该 Jira 卡片追加中文变更总结。推送总结只描述做了哪些调整，不固定附带分支、提交、验证结果或残留风险；这些信息按需保留在 Git、完成证据或任务审计中。
- 推送成功但 Jira 评论写入失败时，必须明确说明代码已经推送、Jira 回写尚未完成，并保留待写评论内容；网络或 Jira 服务恢复后只重试相应 Jira 评论的安全阶段，不得重复推送或跳过 readback。

## 4. 研发工程师常用指令

研发工程师可以用自然语言操作 AI 员工：

```text
初始化 AgenticOps 能力，工作空间是 tapstate。
列出我今天的 Jira 任务。
接管 TAP-123。
确认该设计，并授权在当前 Jira 工作项、仓库、任务分支、目标分支和验证范围内连续推进到拉取请求审查；范围或风险变化时停下。
恢复 TAP-123 上次的接管任务。
根据拉取请求审查意见修复。
提交 TAP-123 本次执行的任务审计记录。
按需分析 tapstate 工作空间最近的 AI 执行记录，并给出 AgenticOps 改进建议。
```

AI 员工应把自然语言转换为 AgenticOps 操作，而不是直接操作 Jira 工作流。

列出任务必须读取真实 Jira。当前 `list_tasks` 是 `capability_gap`，AIAgent 必须使用 Jira 界面或项目认可的只读查询并请求人工提供任务，不得返回示例任务或本地 fake 任务；fake adapter 只允许用于 AgenticOps 本地自动化回归。

Project Profile 提供 Jira Connection、Project Key 和默认仓库映射；Jira Cloud `base_url` 必须是严格 HTTPS 站点根地址，例如 `https://tapdata.atlassian.net`，不能包含 userinfo、query、fragment 或非根路径。工作空间初始化把 `connection_id`、规范化 `jira_base_url`、`jira_site`、实时验证的 `jira_account_id`、Project Key、默认仓库和源码规范路径固化到 schema v3 `agent.json`；后续 effective Profile/Connection overlay 或登录账户与该身份不一致时，必须在读取凭证或发送请求前阻断。普通 `workspace preflight` 没有重绑权限，只有指导员显式执行并确认 `workspace init` 才能重绑。旧 schema 工作空间必须重新初始化，不得静默补值。Jira email 与 token 只保存在当前业务项目工作空间 `.agentic-ops/.env`，不得写入 YAML、日志、事件或共享安装；token 只允许隐藏输入或安全标准输入。AIAgent 不直接解析或修改 Runtime 管理的配置文件。

`workspace preflight` 返回初始化不完整时，AIAgent 不得把 Profile 可解析视为初始化成功，也不得继续读取或接管任务。应要求公司员工指导员在业务项目工作空间重新运行 `ao-work workspace init`；相同候选配置允许修复半初始化状态，覆盖不同完整配置仍需明确确认。业务 Git remote 只接受精确 `github.com/<owner>/<repository>` 的 SCP、SSH 或 HTTPS 形式；raw/effective fetch/push 必须全部匹配，任何 `url.*.insteadOf` 或 `pushInsteadOf` 都会在 clone、`ls-remote` 或可信 probe 前阻断。

## 5. 操作使用方式

AI 员工必须以机器可读能力目录作为“当前是否能调用”的唯一事实源。先列出能力，再查看目标操作：

```sh
ao-work capability list
ao-work capability show <operation>
```

目录的 `status` 只能是 `implemented` 或 `capability_gap`。只有 `implemented` 且 `commands` 明确列出的路径可以调用；`capability_gap`、未收录操作、目录无效或只有 Operation Contract 时必须停止，并执行中文 `next_action`。目录中的 `visibility=internal` 操作只允许版本化 Skill 编排，不得让 AI 把 `task init` 说成 Jira 接管，也不得把 `report write` 说成 Jira 回写或任务完成。

所有能力命令共用同一安装身份门禁：Runtime 从正在执行的 `ao_work` 模块位置自定位 developer managed clone，不能通过 CLI 参数或环境变量切换安装根。自定义安装目录必须从其自身 `bin/ao-work` 启动；不得把另一目录伪装成能力、Profile 或共享协议来源。

当前公开命令语法如下。具体参数继续读取 `ao-work --help` 或对应层级帮助，所有全局参数必须放在操作组之前：

```sh
ao-work capability list
ao-work capability show jira_inspect

ao-work workspace init
ao-work workspace inspect
ao-work workspace preflight

ao-work auth jira list
ao-work auth jira show
ao-work auth jira set
ao-work auth jira remove --field <email|token|all>
ao-work auth jira verify

ao-work jira inspect --issue-key TAP-123
ao-work jira comment plan --issue-key TAP-123 --idempotency-key <key> --category <category> --content-file <path> --plan-file .agentic-ops/tasks/TAP-123/runs/<agentic_run_id>/jira-plans/<name>.json
ao-work jira comment apply --plan-file <managed-path> --confirm-plan-id <plan-id> --authorization-reference <reference>
ao-work jira comment readback --issue-key TAP-123 --idempotency-key <key> --plan-file <managed-path> --confirm-plan-id <plan-id>
ao-work jira description plan --issue-key TAP-123 --idempotency-key <key> --sections-file <path> --plan-file .agentic-ops/tasks/TAP-123/runs/<agentic_run_id>/jira-plans/<name>.json
ao-work jira description apply --plan-file <managed-path> --confirm-plan-id <plan-id> --authorization-reference <reference>
ao-work jira worklog plan --issue-key TAP-123 --idempotency-key <key> --title <中文标题> --details-file <path> --included-work-file <yaml-or-json> --excluded-waiting-category <中文类别> --time-spent-seconds <seconds> --started <timestamp> --exclude-waiting --plan-file .agentic-ops/tasks/TAP-123/runs/<agentic_run_id>/jira-plans/<name>.json
ao-work jira worklog apply --plan-file <managed-path> --confirm-plan-id <plan-id> --authorization-reference <reference>
ao-work jira worklog readback --issue-key TAP-123 --idempotency-key <key> --plan-file <managed-path> --confirm-plan-id <plan-id>
```

### workspace init 非交互全参示例

脚本或 CI 初始化业务项目工作空间必须明确提供身份、Profile 和确认，token 通过安全标准输入传入，不得放入命令行参数。`--workspace-root` 是 `ao-work` 顶层全局参数（默认当前目录 `.`，必须在操作组之前），在目标工作空间目录内运行时可以省略：

```sh
printf '%s\n' "$JIRA_API_TOKEN" | ao-work workspace init \
  --non-interactive \
  --project tapdata \
  --agent-id <agent-id> \
  --source-pool-root <pool-root> \
  --jira-email <jira-account-email> \
  --git-name <git-author-and-committer-name> \
  --git-email <git-author-and-committer-email> \
  --github-login <github-actor-login> \
  --token-stdin \
  --confirm
```

从其它目录初始化指定工作空间时，把 `--workspace-root <路径>` 放在 `ao-work` 之后、`workspace` 之前：

```sh
printf '%s\n' "$JIRA_API_TOKEN" | ao-work --workspace-root /path/to/workspace workspace init \
  --non-interactive \
  --project tapdata \
  --agent-id <agent-id> \
  --source-pool-root <pool-root> \
  --jira-email <jira-account-email> \
  --git-name <git-author-and-committer-name> \
  --git-email <git-author-and-committer-email> \
  --github-login <github-actor-login> \
  --token-stdin \
  --confirm
```

非交互模式必填项：

- `--non-interactive` 与 `--confirm`：确认初始化摘要，缺一不可。
- `--project <profile>`：Project Profile id（如 `tapdata`），来源 `developer/standards/projects/<profile>/profile.yaml`；Jira 站点、Project Key 与默认仓库没有 CLI 参数，全部取自该 Profile。
- `--agent-id <id>`：只允许 `[0-9A-Za-z_-]`。
- `--jira-email` 与 `--token-stdin`：必须成对（`jira_credential_pair_required` 拦截单边）；token 从 stdin 第一行读取。若已用 `ao-work install auth set` 配置安装目录凭证，可省略（安装凭证为优先源）。
- 池根必配：`--source-pool-root` 或 `~/.agentic-ops/user/config.yaml` 的 `source_pool_root` 二选一，否则 `source_pool_root_invalid` 阻断，无兼容回退。池根目录不存在时由 init 自动创建并写入容器 README（preflight 只读校验、不创建）。

可选参数：

- `--source-root`：缺省为池模式（源码语义 = 池根，任务工作树在接管时创建）；显式传入非池根路径则为普通源码模式。
- `--git-name` / `--git-email` / `--github-login`：执行身份三参数，all-or-none；已用 `ao-work install identity set` 配置安装身份时可省略，从安装身份继承。
- `--confirm-existing-config`：已有不同完整配置需覆盖时提供，否则 `existing_config_confirmation_required` 阻断。

> 维护约定：本节示例与 `developer/skills/initialize-project-workspace/SKILL.md` 的非交互示例随 `workspace init` 参数变更同步修正，不得只改实现不改文档。

`jira_inspect` 只输出基础 Jira Issue 事实和凭证配置状态，不读取评论、Custom Field 或旧 `inspect_task` 契约定义的富门禁事实，不判断项目准入，也不绑定 AIAgent。AIAgent 必须把该输出与项目标准资产结合分析。

Jira Comment、Description 和 Worklog 都要求先初始化一致的本地任务身份，并使用 Runtime 管理且绑定当前 Issue/run 的计划文件。Comment 与 Worklog 使用 `plan -> apply -> readback`，readback 仍必须提供原计划文件和 `plan_id`；Description 使用 `plan -> apply`，apply 内部完成写后回读。真实写入前必须获得与当前 Issue、`agentic_run_id` 和 `plan_id` 对应的明确授权，结果不明确时不得重新 apply。

`plan` 会返回可直接使用的 `authorization_user_confirmation_reference`、需要写入 Jira 人工确认评论的 `authorization_comment_marker`，以及 `authorization_jira_comment_reference_format`。允许的引用只有 `user-confirmation:<ISSUE-KEY>:<agentic-run-id>:<plan-id>`，或已回读且正文以独立完整行包含当前 marker 的 `jira-comment:<ISSUE-KEY>:<正整数评论ID>:<plan-id>`。Runtime 必须在本地决策记录和 Jira 副作用之前完成严格校验；空值、任意字符串、其它任务、旧运行、旧计划、无对应评论或 marker 不一致都必须阻断。

Jira Description 保存确认后的稳定任务契约；Jira Comment 保存分析、计划、决策、阻塞和证据轨迹；Jira Custom Field 适配尚未实现，不能自动写入；Worklog 只记录真实投入时间和中文标题总结。`included-work-file` 必须逐项提供中文 `description` 与正整数 `seconds`，总和等于 `time-spent-seconds`；至少一个 `--excluded-waiting-category` 明确列出排除的等待类别。不得用 Worklog 承载计划或人工确认，也不得覆盖已有 Comment 改写历史。

`list_tasks`、`takeover_task`、`resume_takeover`、`release_agent`、PR / CI、分支对齐、反馈包、完成证据和 Custom Field 写入目前都是 `capability_gap`。目标契约可以继续描述验收边界，但 AI 不能构造旧命令或把人工操作表述为 Runtime 已执行。

AI 员工不应直接依赖 Jira 字段名、Jira 状态名或 Jira `transition` 名称做判断。Jira 字段名、状态名、`transition` 名称和 `issue_key` 可以按原始值引用；面向研发工程师的 Jira 文本和 AIAgent 自然语言交互必须使用中文。

## 6. 停止条件

以下情况必须停止并请求人工确认：

- 负责人不匹配。
- Jira `assignee` 不是当前登录用户。
- `agentic_id` 不为空且不等于当前 AIAgent 的 `agent_id`。
- 执行过程中 `assignee` 或 `agentic_id` 发生变化。
- Jira 卡片未进入允许接管范围。
- 无法判断任务分类，或任务分类无法映射到标准流程。
- 卡片不满足项目准入标准、字段映射缺失或权限不足。
- 实际影响范围超出 Jira 已确认边界。
- 需要改变复杂度、风险等级或需求范围。
- 权限不足。
- 测试无法运行。
- 连续修复失败。
- 拉取请求审查意见存在需要取舍的修改。
- 未获得当前动作独立确认或有效工作项级连续执行授权的推送、创建或更新拉取请求、重新提交修复。
- 向 `master`、`main`、`develop`、`release/*` 或其它保护分支推送。
- 合并、发布、Git Tag、直接修改受保护分支、强推、历史改写或线上风险相关动作。

## 7. 证据要求

AI 员工每次任务接管必须能形成证据链：

- `agentic_run_id`
- `issue_key`
- `workspace`
- `task_type`
- `task_class`
- `process_id`
- `agent_id`
- `agentic_id`
- `current_stage`
- `agentic_next_action`
- 接管成功或失败记录
- 变更摘要
- 验证结果
- 残留风险
- 下一步
- 拉取请求链接或阻塞原因

证据不得包含 secrets、tokens、private keys、原始敏感日志、完整 Jira 描述或敏感代码片段。

## 8. 完成行为

AI 员工完成设计或修复计划后，如果尚未取得工作项级连续执行授权，必须停在人工确认点：

```text
设计或修复计划已完成。
已记录修改范围、验证方式、明确非范围和已知风险。
等待研发工程师确认计划并授予工作项级连续执行授权。
```

授权生效后，AI 员工应连续推进到任务分支推送和目标为 `develop` 的拉取请求创建或更新完成，回读 Git、GitHub、CI 和 Jira 事实，输出包含固定 Head SHA、变更摘要、验证结果、CI 事实、Jira 回写引用和残留风险的拉取请求审查包，再暂停等待审查。授权失效、保护分支推送、合并、发布或范围变化必须重新进入人工确认。

AI 员工不得把“代码已修改”视为“任务已完成”。任务完成仍需要研发工程师、CI、拉取请求审查和后续验收流程。

当一个标准流程进入完成、阻塞或交接节点时，AI 员工必须把任务级审计记录写入项目 AI 工作空间的 `.agentic-ops/tasks/<ISSUE-KEY>/` 目录。Issue Key、run id、决策、幂等键和外部引用必须通过 Runtime 的安全标识校验；`agent.json`、profiles、connections、tasks、runs、audit、feedback、handoff、locks、任务目录、报告目录及 workspace-index 的现存祖先和叶子均不得是符号链接，不能用相对跳转或手工路径读写工作空间外。Jira 卡片回写任务级关键结论、状态和稳定引用；后续如果团队配置审计服务，再提交同一份脱敏摘要。本地 `feedback bundle` 和 `feedback report` 只服务诊断与后续分析，不能替代本地任务审计记录。

当标准流程进入完成或交接终态，并且完成表单、审查结论和证据已经写入后，AI 员工必须通过受控操作清理 Jira 任务上的 `agentic_id`。清理失败时必须记录 `agent_release_failed`，说明清理前字段值、当前 `agent_id`、完成证据引用和需要研发工程师判断的动作。
