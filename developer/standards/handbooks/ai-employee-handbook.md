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
- 开发前必须读取项目规则、AI 员工手册、工作流配置和操作契约。
- 开发前必须读取 Standard Process Registry，确认当前 `task_class` 对应的 `process_id` 和阶段标准。
- 开发前必须执行门禁。
- 接管门禁必须先以 Jira `/myself` 读取当前账户并确认 Jira `assignee` 已设置且与其一致，再确认状态可以映射到项目流程；不满足时不得写 Jira、创建本地任务状态或执行 Git 副作用。
- 接管操作自动判断新接管、接纳存量任务或恢复已有运行；后两种必须在 Jira 中文接管评论中明文提示“不是新接管”。
- 接管成功必须写入并回读绑定 `issue_key`、`agentic_run_id`、`agent_id`、工作空间、时间、当前阶段和下一步动作的结构化 Jira 评论，再按 Project Profile 推进状态；developer 不依赖 Jira Agentic 自定义字段。
- 每个执行操作前必须重新检查 `assignee`、Jira 状态和本地运行绑定；任务不再属于当前登录用户或外部事实与本地状态冲突时必须停止并记录。
- 开发前必须输出简短计划、验证方式和风险点。
- 研发工程师确认版本化设计或修复计划时，可以同时授予工作项级连续执行授权；授权事实必须能从 Jira 决策评论或项目配置的等价任务事实源回读。
- 有效授权必须绑定 `issue_key`、`agentic_run_id`、`agent_id`、目标仓库、工作分支、目标分支、计划版本、修改范围和验证方式。
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
- 执行过程必须持续记录 `agent_id`、`agentic_run_id`、`task_type`、`task_class`、`process_id`、`current_stage`、`agentic_next_action`、关键输入、关键输出和阻塞原因。
- AI 处理阶段（task_intake / solution_classification / implementation / ci_validation）进入时必须在任务状态 `stage_timeline` 追加 `{stage_id, begin, end: null}`，准出时闭合对应 `end`；人工环节（waiting_takeover / v1 的 pr_review / completed）不进入时间线。
- 同一 AI 处理阶段在 `stage_timeline` 中出现达到重试门禁上限（默认 2 次）时，`advance_stage` 会阻断并返回 `stage_loop_requires_human`；AIAgent 必须停止自动推进，向研发工程师展示目标阶段、出现次数与时间线全貌，等待人工决策（确认继续 / 调整方案 / 修改流程），不得绕过门禁自行继续。
- 重试只能在当前输入和前序表单仍有效时进行；如果任务范围、项目准入信息、审查结论或风险边界变化，必须按 `redo_from_stage` 重做受影响阶段。
- 完成后必须回写变更摘要、测试结果、残留风险、完成证据和下一步。
- 任务完成或交接结束后，必须写入并回读中文终止评论，关闭本地运行并保留任务审计记录；不写入或清理 Jira Agentic 自定义字段。
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

列出任务必须读取真实 Jira。当前 `list_tasks` 已由 `ao-work jira list` 实现；无编号接管也只读返回同一工作空间名下候选并等待研发工程师选择，不得自动挑选。fake adapter 只允许用于 AgenticOps 本地自动化回归，不能冒充真实候选。

Project Profile 提供 Jira Connection、Project Key 和默认仓库映射；Jira Cloud `base_url` 必须是严格 HTTPS 站点根地址，例如 `https://tapdata.atlassian.net`，不能包含 userinfo、query、fragment 或非根路径。新工作空间初始化前先用同一安装的 `ao-work auth` 配置研发员唯一身份与 Jira 凭据；`workspace init` 生成 schema v4 `agent.json`，只固化项目绑定、源码规范路径和 `install_identity_ref`。后续 effective Profile/Connection overlay、安装身份指纹或登录账户不一致时，必须在发送写请求前阻断。普通 `workspace preflight` 没有重绑权限，只有指导员显式执行并确认 `workspace init` 才能重绑。schema v3 已停止作为运行时授权来源；旧工作空间必须先重新授权，再由指导员明确重新初始化，不得静默读取、复制或删除旧 `.env`。Token 只允许隐藏输入或安全标准输入，保存在当前 developer 安装的 `user/.env`，不得写入工作空间、YAML、日志、事件或聊天。AIAgent 不直接解析或修改 Runtime 管理的配置文件。

`workspace preflight` 返回初始化不完整时，AIAgent 不得把 Profile 可解析视为初始化成功，应引导公司员工指导员在业务项目工作空间重新运行 `ao-work workspace init`。它是诊断工具，不是接管任务的前置步骤；`ao-work takeover` 会自行重新校验工作空间、安装身份和 Jira 事实。相同候选配置允许修复半初始化状态，覆盖不同完整配置仍需明确确认。业务 Git remote 只接受精确 `github.com/<owner>/<repository>` 的 SCP、SSH 或 HTTPS 形式；raw/effective fetch/push 必须全部匹配，任何 `url.*.insteadOf` 或 `pushInsteadOf` 都会在 clone、`ls-remote` 或可信 probe 前阻断。池模式下，无权限（403/404/denied/认证失败等权限类错误）的源码仓库会在初始化预检与池成员准备阶段跳过并明确提示，结果 `skipped_repositories` 列出被跳过的仓库，其余仓库正常完成；网络类错误（超时/DNS/连接失败等）仍阻断初始化。

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

ao-work auth
ao-work auth --show
ao-work workspace init
ao-work workspace inspect
ao-work workspace preflight

ao-work jira list [--max-results <n>]
ao-work jira inspect --issue-key TAP-123
ao-work takeover TAP-123
ao-work takeover   # 无编号：只读列出候选供研发工程师选择
ao-work task resume [--issue-key TAP-123 | --agentic-run-id <run-id>]
ao-work jira comment plan --issue-key TAP-123 --idempotency-key <key> --category <category> --content-file <path> --plan-file .agentic-ops/tasks/TAP-123/runs/<agentic_run_id>/jira-plans/<name>.json
ao-work jira comment apply --plan-file <managed-path> --confirm-plan-id <plan-id> --authorization-reference <reference>
ao-work jira comment readback --issue-key TAP-123 --idempotency-key <key> --plan-file <managed-path> --confirm-plan-id <plan-id>
ao-work jira description plan --issue-key TAP-123 --idempotency-key <key> --sections-file <path> --plan-file .agentic-ops/tasks/TAP-123/runs/<agentic_run_id>/jira-plans/<name>.json
ao-work jira description apply --plan-file <managed-path> --confirm-plan-id <plan-id> --authorization-reference <reference>
ao-work jira worklog plan --issue-key TAP-123 --idempotency-key <key> --title <中文标题> --details-file <path> --included-work-file <yaml-or-json> --excluded-waiting-category <中文类别> --time-spent-seconds <seconds> --started <timestamp> --exclude-waiting --plan-file .agentic-ops/tasks/TAP-123/runs/<agentic_run_id>/jira-plans/<name>.json
ao-work jira worklog apply --plan-file <managed-path> --confirm-plan-id <plan-id> --authorization-reference <reference>
ao-work jira worklog readback --issue-key TAP-123 --idempotency-key <key> --plan-file <managed-path> --confirm-plan-id <plan-id>
ao-work jira transition plan --issue-key TAP-123 --idempotency-key <key> --target-status <状态名> --plan-file .agentic-ops/tasks/TAP-123/runs/<agentic_run_id>/jira-plans/<name>.json
ao-work jira transition plan --issue-key TAP-123 --idempotency-key <key> --target-transition <profile-key> --plan-file .agentic-ops/tasks/TAP-123/runs/<agentic_run_id>/jira-plans/<name>.json
ao-work jira transition apply --plan-file <managed-path> --confirm-plan-id <plan-id> --authorization-reference <reference>
ao-work jira transition readback --issue-key TAP-123 --idempotency-key <key> --plan-file <managed-path> --confirm-plan-id <plan-id>
```

任务接管发生在信息分析和具体流程选择之前。`ao-work takeover <KEY>` 将研发工程师明确的接管指令绑定为当前 run 的内部授权摘要，不要求研发工程师确认或复制内部参数。接管成功后，信息分析和方案分级连续推进，只在设计审查、代码审查或风险决策暂停。

### 安装身份与 workspace init 非交互示例

脚本或 CI 必须先给当前 developer 安装配置研发员唯一身份，再初始化业务项目工作空间。Token 只在安装身份步骤通过安全标准输入传入，不得放入命令行参数：

```sh
printf '%s\n' "$JIRA_API_TOKEN" | ao-work auth \
  --agent-id <agent-id> \
  --jira-email <jira-account-email> \
  --git-name <git-author-and-committer-name> \
  --git-email <git-author-and-committer-email> \
  --github-login <github-actor-login> \
  --token-stdin \
  --non-interactive

ao-work workspace init \
  --non-interactive \
  --project tapdata \
  --source-pool-root <pool-root>
```

从其它目录初始化指定工作空间时，把 `--workspace-root <路径>` 放在 `ao-work` 之后、`workspace` 之前：

```sh
ao-work --workspace-root /path/to/workspace workspace init \
  --non-interactive \
  --project tapdata \
  --source-pool-root <pool-root>
```

非交互模式必填项：

- `--non-interactive`：不读取终端输入；普通显式参数不需要额外确认。
- `--project <profile>`：Project Profile id（如 `tapdata`），来源 `developer/standards/projects/<profile>/profile.yaml`；Jira 站点、Project Key 与默认仓库没有 CLI 参数，全部取自该 Profile。
- 安装身份必须已通过 `ao-work auth` 配置并包含 Jira 凭据；新工作空间从安装目录继承，不接收或保存工作空间级身份与凭据。
- 池根必配：`--source-pool-root` 或 `~/.agentic-ops/user/config.yaml` 的 `source_pool_root` 二选一，否则 `source_pool_root_invalid` 阻断，无兼容回退。池根目录不存在时由 init 自动创建并写入容器 README（preflight 只读校验、不创建）。

可选参数：

- `--source-root`：缺省为池模式（源码语义 = 池根，任务工作树在接管时创建）；显式传入非池根路径则为普通源码模式。
- `--confirm-existing-config`：仅在已有不同完整配置将被覆盖时提供，否则 `existing_config_confirmation_required` 阻断。交互模式先显示字段差异并只询问一次；新建、半初始化修复和相同配置不确认。

> 维护约定：本节示例与 `developer/skills/initialize-project-workspace/SKILL.md` 的非交互示例随 `workspace init` 参数变更同步修正，不得只改实现不改文档。

`jira_inspect` 只输出基础 Jira Issue 事实和凭证配置状态，不读取评论、Custom Field 或旧 `inspect_task` 契约定义的富门禁事实，不判断项目准入，也不绑定 AIAgent。AIAgent 必须把该输出与项目标准资产结合分析。

Jira Comment、Description 和 Worklog 都要求先初始化一致的本地任务身份，并使用 Runtime 管理且绑定当前 Issue/run 的计划文件。Comment 与 Worklog 使用 `plan -> apply -> readback`，readback 仍必须提供原计划文件和 `plan_id`；Description 使用 `plan -> apply`，apply 内部完成写后回读。真实写入前必须获得与当前 Issue、`agentic_run_id` 和 `plan_id` 对应的明确授权，结果不明确时不得重新 apply。

`plan` 会返回可直接使用的 `authorization_user_confirmation_reference`、需要写入 Jira 人工确认评论的 `authorization_comment_marker`，以及 `authorization_jira_comment_reference_format`。允许的引用只有 `user-confirmation:<ISSUE-KEY>:<agentic-run-id>:<plan-id>`，或已回读且正文以独立完整行包含当前 marker 的 `jira-comment:<ISSUE-KEY>:<正整数评论ID>:<plan-id>`。Runtime 必须在本地决策记录和 Jira 副作用之前完成严格校验；空值、任意字符串、其它任务、旧运行、旧计划、无对应评论或 marker 不一致都必须阻断。

Jira Description 保存确认后的稳定任务契约；Jira Comment 保存接管、恢复、分析、计划、决策、阻塞、证据和终止轨迹；developer 不把 Agentic 运行信息映射到 Jira Custom Field；Worklog 只记录真实投入时间和中文标题总结。`included-work-file` 必须逐项提供中文 `description` 与正整数 `seconds`，总和等于 `time-spent-seconds`；至少一个 `--excluded-waiting-category` 明确列出排除的等待类别。不得用 Worklog 承载计划或人工确认，也不得覆盖已有 Comment 改写历史。

`list_tasks`、`takeover_task`、`resume_takeover` 和 v2 CI 原子能力已由能力目录声明为 `implemented`；`release_agent`、分支对齐、反馈包和其它完成证据聚合仍可能是 `capability_gap`，执行前必须逐项查询能力目录。developer 不提供 Agentic Custom Field 写入能力。

AI 员工不应直接依赖 Jira 字段名、Jira 状态名或 Jira `transition` 名称做判断。Jira 字段名、状态名、`transition` 名称和 `issue_key` 可以按原始值引用；面向研发工程师的 Jira 文本和 AIAgent 自然语言交互必须使用中文。

## 6. 停止条件

以下情况必须停止并请求人工确认：

- 负责人不匹配。
- Jira `assignee` 不是当前登录用户。
- 执行过程中 `assignee` 发生变化，或 Jira 状态、受管评论与本地运行事实冲突。
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

授权生效后，AI 员工应连续推进到任务分支推送和目标为 `develop` 的拉取请求创建或更新完成。`development_change_v1` 回读事实并输出拉取请求审查包后暂停；显式启用 `development_change_v2` 时继续进入 `ci_validation`，先绑定 GitHub PR Head/Base 并从 Base Workflow 事实自动判定是否需要 CI。无需 CI 时生成完成证据；需要时按当前 Head 的 5 分钟 CI 启动截止时间、执行后 10 分钟完成截止时间和 15 秒间隔观察必需检查。只有明确分类为业务代码缺陷时，才在最多三次授权内修复预算中完成全量本地复验、提交、推送与新 Head 回读；其它失败、判定未知或任一超时必须人工介入且不得自动修复。CI 通过或明确无需 CI 后关闭本地运行，不进入 developer 内置代码审查。授权失效、报告不可信、保护分支推送、合并、发布或范围变化必须进入风险决策。

AI 员工不得把“代码已修改”视为“任务已完成”。v1 仍需要拉取请求审查；v2 只有最终 Head/Base 的 GitHub CI 要求判定闭合，且 `required` 时全部必需检查严格为 `SUCCESS`、`not_required` 时没有伪造检查或运行事实，才表示本次 AIAgent 开发运行完成。两者都不代表 PR 已合并、Jira 已 Done，也不能替代项目明确要求的后续专业验收。

当一个标准流程进入完成、阻塞或交接节点时，AI 员工必须把任务级审计记录写入项目 AI 工作空间的 `.agentic-ops/tasks/<ISSUE-KEY>/` 目录。Issue Key、run id、决策、幂等键和外部引用必须通过 Runtime 的安全标识校验；`agent.json`、profiles、connections、tasks、runs、audit、feedback、handoff、locks、任务目录、报告目录及 workspace-index 的现存祖先和叶子均不得是符号链接，不能用相对跳转或手工路径读写工作空间外。Jira 卡片回写任务级关键结论、状态和稳定引用；后续如果团队配置审计服务，再提交同一份脱敏摘要。本地 `feedback bundle` 和 `feedback report` 只服务诊断与后续分析，不能替代本地任务审计记录。

当标准流程进入完成或交接终态，并且完成表单、审查结论和证据已经写入后，AI 员工必须通过受控操作写入并回读中文终止评论，随后关闭本地运行。评论或本地收口失败时必须记录稳定失败码、完成证据引用和需要研发工程师判断的动作；不得用 Agentic Custom Field 充当锁或完成事实。
