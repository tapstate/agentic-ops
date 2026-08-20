# AI 资产入口

> 工作面：`developer`

本文是 AIAgent 执行任务前的资产读取入口。`docs/` 面向人阅读；本文列出的资料面向 AIAgent 执行、恢复、门禁、证据和审计。

## 业务任务读取顺序

AIAgent 执行业务 Jira 任务前按以下顺序读取：

1. [研发员执行指引](agent-guides.md)：确认固定入口、能力查询和工作面边界。
2. [AI 员工手册](handbooks/ai-employee-handbook.md)：理解任务模型、工作原则、停止条件和证据要求。
3. [机器可读能力目录](capabilities/operations.yaml)：确认 Runtime 当前实现状态、可调用命令和能力缺口处理动作。
4. 安装根目录 [shared/integration/](../../shared/integration/)：读取 maintainer 与 developer 唯一共享的纯 JSON 集成协议；它不包含角色规则、可执行代码或外部副作用。
5. [机器可读操作契约](contracts/operations/)：理解操作输入、输出、失败码、副作用和人工门禁。
6. [标准流程定义](contracts/processes/)：读取任务分类、标准流程和阶段标准。
7. 当前 Project Profile：理解工作空间和 Jira / GitHub / 本地源码映射。
8. [公司级硬规定](company/core-hard-rules.md)：读取跨项目通用硬规则和人工门禁。
9. [项目资产包](projects/)：读取当前项目的 profile、规范、运行手册、模板和工具声明。
10. 当前项目 AI 工作空间 `.agentic-ops/profile.local.yaml`：只读取本地 overlay，不要求复制完整全局资源。
11. [策略](policies/)、[运行手册](runbooks/) 和 [模板](templates/)：读取安装后分发给 AIAgent 的门禁、处理步骤和证据格式。

实际执行前先运行：

```sh
ao-work capability list
ao-work capability show <operation>
```

`ao-work` 在读取能力目录或执行任何命令前，只从当前实际加载的 `ao_work` 模块位置反推 developer managed clone，并校验其 Git、origin、sparse checkout、ref 和受管资产完整性。CLI 不提供 `--install-root`，也不从环境变量选择另一安装目录；自定义安装位置由该位置中的已安装 wrapper 和模块自然自定位。

只有目录返回 `status=implemented` 且列出当前命令路径时，才能继续调用。`status=capability_gap`、目录中不存在该操作、目录校验失败或只有 Operation Contract 时必须停止自动化，并按目录中的中文 `next_action` 处理。Operation Contract 保存目标输入、输出、门禁和副作用边界，不是实现状态事实源。

每个 `ao-work` 子命令都是一个原子步骤控制入口。stdout 的唯一 JSON 对象固定包含 `ok`、`operation`、`status` 和结构化 `agentic_next_action`；下一步对象固定给出 `executor`、稳定 `action`、`required_inputs`、`allowed_operations`、`requires_authorization`、`stop_workflow`、`ownership_effect` 与 `retry_gate`。`executor` 只表示当前动作由 Runtime、当前 AI、人、reviewer 或项目工具执行，不是任务转派；当前版本 `ownership_effect` 只允许 `none`。Runtime 必须根据当前操作的实际结果选择下一动作；AI 只能从当前结果的事实/证据字段取齐 required inputs，并调用 allowed operations 中已实现的操作。失败只在 `retry_gate.allowed=true` 时可以按同一 `retry_key` 再试一次，且必须先回读状态、改变输入并记录 retry 事件；相同输入循环、未允许重试或重试耗尽都要停止转人工。自然语言说明只帮助人理解，不能替代这些机器字段或成为放行依据。

任务负责人与步骤执行者必须分开。统一接管输出绑定的 `task_ownership.task_owner` 是当前工作空间代表的研发员，默认从接管到 PR 审查保持不变。Runtime、人工确认、reviewer 和项目工具参与单个步骤都不改变负责人。`task_transfer` 仍是 `capability_gap`；出现转派需求时必须停止并由人决定，身份变更、原授权失效、交接证据和 Jira 所有权变更后续通过独立专题设计，当前不预设放行行为。

`ao-work` 只判定当前原子步骤是否完成，不把“命令成功”扩大为“整个任务成功”。例如统一接管成功只表示 Jira 接管轨迹、执行状态、本地 run 与来源快照已建立；`jira ... apply` 成功后仍要 readback；`task-run finalize` 才能给出本次协议结论，并且真实任务仍停在 PR 审查。

用户任务入口是“接管 <KEY>”，正式 Runtime 命令为 `ao-work takeover <KEY>`；无 KEY 时只读列出候选。统一入口在 run 确定后绑定内部授权摘要，自动判断新接管、接纳存量或恢复，先完成受管 Comment、必要的 Status transition 和本地状态回读。接管后 AI 形成普通 JSON 语义输入，Runtime 依次执行 `ao-work task intake assess` 与 `ao-work task solution classify`：核对 Jira/Profile/Runtime 精确值、源码普通文件摘要和干净 HEAD，自动补齐确定性字段，并按固定优先级给出 L1–L4。L1 进入设计审查，L2 进入逐项风险决策，L3 由 AI 修改设计并重新分析，L4 停止升级；不得增加准入摘要、通用方案摘要或内部 digest 确认。必要信息未补齐时只允许改变输入后重试一次；来源、HEAD、证据、范围或方案变化会使旧分析与设计审查失效。`ao-work jira inspect --issue-key <KEY>` 继续作为只读基础 Jira 事实入口；它不等价于旧 `inspect-task` 富输出。Project Profile 仍由工作空间初始化与 Runtime 内部加载。资产解析优先级固定为：

```text
项目工作空间 overlay
> ~/.agentic-ops/user/
> developer/standards/projects/<project>/
> developer/standards/company/
> ao-work 稳定默认值
```

该顺序只用于配置和 profile 字段来源解析，不等同于规则冲突优先级。规则冲突必须按 `项目规则 > AIAgent 规则 > 公司规则 > 个人规则` 执行；个人层可以提供本机默认值，但不能覆盖更高优先级规则。

真实任务到 PR 测试使用以下本地审计入口：

```sh
ao-work task-run open --manifest <workspace-relative-manifest.json>
ao-work task-run record --manifest <workspace-relative-manifest.json> --event <workspace-relative-event.json>
ao-work task-run probe-prohibition-baseline --manifest <workspace-relative-manifest.json>
ao-work task-run probe-jira --manifest <workspace-relative-manifest.json>
ao-work task-run probe-jira-write --manifest <workspace-relative-manifest.json> --plan-file <managed-plan.json> --confirm-plan-id <plan-id>
ao-work task-run probe-git --manifest <workspace-relative-manifest.json> --bind-action <git_commit|git_push_task_branch>
ao-work task-run probe-pr --manifest <workspace-relative-manifest.json> --bind-action github_pr_create_or_update
ao-work task-run verify --manifest <workspace-relative-manifest.json> --verification-id <id>
ao-work task-run probe-prohibitions --manifest <workspace-relative-manifest.json>
ao-work task-run record-unverified-prohibitions --manifest <workspace-relative-manifest.json>
ao-work task-run finalize --manifest <workspace-relative-manifest.json> --status <ready_for_pr_review|blocked|failed> --next-action <明确下一步>
```

正式 manifest 还必须显式绑定两组不可推断的事实：`task_binding` 记录 canonical Jira issue 内容摘要、`inputs/` 下批准计划文件和该文件原始 UTF-8 SHA-256；`execution_identity` 复用工作空间初始化时已确认并写入 `agent.json` 的 Git author/committer 姓名邮箱及 GitHub actor login。Runtime 不得用操作系统用户名、主机名、全局 Git 配置或当前登录临场补齐这些字段；manifest 与工作空间身份漂移时必须阻断。

可信执行顺序固定为：在干净任务分支执行写前 `probe-prohibition-baseline`；若远端任务分支已存在，本地 HEAD 必须等于它，否则必须等于远端目标分支，预置 commit 直接阻断。随后修改并创建最终 commit，在最终 HEAD 上执行全部 `verify`，验证通过后才 push 并执行 `probe-git`，再新建 PR 和执行 `probe-pr`，最后执行 `probe-prohibitions`。当前 PR 动作归因只支持基线无 open PR 的 create-only proof；基线已有 PR 时因不能证明本轮 update 而 fail closed。Git/PR 动作只证明写前基线至后置回读的区间，不把 probe 事件时间当作真实动作时间。任何失败修复或整理只要产生新 commit，就必须在新最终 HEAD 上重跑全部指定验证；旧 HEAD 的通过结果不能复用。

`open`、`record`、`record-unverified-prohibitions` 和 `finalize` 只处理本地受管协议状态。`probe-jira`、`probe-jira-write`、`probe-git`、`probe-pr`、`probe-prohibition-baseline` 和 `probe-prohibitions` 会在 manifest 显式只读权限门禁通过后访问 Jira、Git 远端或 GitHub；`verify` 只执行 manifest 中通过版本化语义白名单的精确 argv，并在子进程前拒绝 Shell/`-c`、外部系统与网络工具、安装/发布/部署、修改模式和受管状态路径。执行环境使用隔离 HOME、最小 PATH、无业务凭据及常见生态 offline/no-index 设置；`network_policy=allowlist-only-no-sandbox` 明确表示这不是内核级网络沙箱。它们不执行 Jira 写入、Git push、PR 创建、合并、发布或 Tag；真实外部写动作由 Skill 在 manifest 授权范围内协调现役 Runtime、项目认可工具、AI 或人工完成，再由对应 Runtime probe 原子绑定后置回读。`ready_for_pr_review` 只有在写入前禁止动作基线、Jira、本运行各一条 `created=true` 的 Comment 与 Worklog 专用写后回读、真实提交、远端任务分支、真实 PR、全部指定验证、五项增量/祖先禁止动作和完整复盘均闭合时才成立。Comment/Worklog 的精确幂等标记同时绑定 issue、run 与 key，旧运行记录不能复用；`created=true` 还必须绑定计划时标记缺失、请求前不可变 create attempt 及同一 attempt 写后回读，no-op 或缺少 attempt 的历史记录不能冒充本运行创建。Worklog 的机器证据必须逐项列出计入处理及秒数、保证总和等于真实总耗时，并列出排除的等待类别；复盘四类各自必须有 finding，或带理由和证据的 no_finding，每条 failure/retry/human_intervention/waiting 都必须被至少一个 finding 分类的 `source_event_ids` 逐项承接；`quality_finding` 对过程事件的引用不能替代该承接。阻塞或失败也必须显式选择对应状态并生成不伪装通过的总结。任一禁止动作已经发生，说明本次执行违反授权边界，最终状态只能输出 `failed`，并保留越权证据与人工处置动作；`blocked` 只表示尚未实施禁止动作、但因前置条件或人工门禁无法继续。

`probe-pr` 核对的 `github_actor_login` 只证明该 probe 调用时当前 `gh` 会话身份，不证明 Git 远端 push actor；任何结果不得把它表述为 push 身份证明。manifest 的授权引用必须精确为 `user-confirmation:<ISSUE>:<agentic_run_id>:<approved_plan_sha256>`，任意非空文本、旧运行或旧计划摘要无效；其中 `authorization.confirmed_by`、确认时间及引用仍只是当前会话/协议包内声明，没有独立 Jira author readback 等外部证据时，不得称为 maintainer 独立验证的人工批准。最终 maintainer 只读验收 developer 结果包及其 Runtime probe 链，不会独立访问 Jira、Git 或 GitHub 做外部回读。

## 使用边界

- AIAgent 不应把 README 或 `docs/` 当作执行事实源；README 和 `docs/` 主要帮助人理解项目。
- AIAgent 执行具体业务 Jira 任务时，以 AI 员工手册、操作契约、标准流程、工作流配置、项目级规范、策略、运行手册和模板为准。
- AIAgent 收到“按当前业务项目工作空间 `AGENTS.md` 启用 AgenticOps。”时，应从当前工作空间 `AGENTS.md`、`.agentic-ops/agent.json` 和本入口初始化；不得引用 developer-only 安装中不存在的根文件，也不得要求读取研发工程师个人 wiki、个人长期记忆或上一段聊天上下文。
- `ao-work workspace init` 会把受信 `developer/AGENTS.md` 与 `developer/rules/ai-execution.md` 的内容复制进业务工作空间 `AGENTS.md` 的受管区块。运行时 AI 以这个可自动发现的入口启动，再按本清单读取资产；不得只保留一个指向尚未加载绝对路径的引用。
- AI 工作空间与业务源码仓库必须硬分离，不能相同或互相嵌套；源码目录创建或 clone 后必须再次阻断 AgenticOps 源头标记，`workspace inspect` 也不得接受该类源码目录。新工作空间从 developer 安装的 `user/identity.yaml` 与 `user/.env` 继承身份和凭据，并用 schema v4 `install_identity_ref` 防错装；工作空间 `.agentic-ops/.env` 仅是 schema v3 存量迁移路径。所有受管目录与凭据文件均不得是符号链接或进入 Git 跟踪；工作空间位于其它 Git 仓库时，Runtime 必须先把 `.agentic-ops/` 写入该仓库本地 `.git/info/exclude` 并验证生效。
- AIAgent 不得临场猜测 Jira 字段、状态流转或人工门禁。
- AIAgent 不得从契约文件、历史文档或命令名称推断实现状态；能力目录是 developer Runtime 可调用性的唯一机器事实源。
- 卡片不满足项目准入标准时，AIAgent 必须先读取项目准入资产和目标代码，形成结构化分析与补卡建议；按项目规定写回 Jira 后结束当前接管，不能用会话内推断绕过重新检查。
- 字段映射缺失、权限不足或无法安全更新 Jira 事实时，必须停止并请求研发工程师或流程负责人处理。

本入口只加载 developer 资产，不得跳转或回退读取 maintainer 工作面的规则、Skill、授权、配置或状态。
