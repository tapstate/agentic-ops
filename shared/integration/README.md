# 跨工作面集成协议

本目录只保存 maintainer 与 developer 共同使用的版本化 JSON 协议，是双方准备 manifest、追加事件和验收结果包的唯一合同源。

边界固定如下：

- 只包含 JSON Schema 和协议字段说明，不包含 Python、Shell 或其它可执行代码。
- 不包含 maintainer 或 developer 的角色规则、AI 入口、授权凭证、项目配置和运行状态。
- 协议本身不产生外部副作用；developer Runtime 依据带当前会话/包内用户确认声明的 manifest 执行 Jira/Git/GitHub 只读 probe 和精确验证 argv，外部写动作仍由受控能力或项目工具执行，maintainer 只读验收结果包。
- Schema 变化必须版本化并同步双方测试；不得在任一工作面复制一份私有变体。

当前 `task_to_pr_review` 协议由以下三个文件闭合：

- `task-to-pr-manifest.schema.json`：当前会话/协议包声明经用户确认的 Jira 账户/状态绑定、任务、工作空间、仓库、范围、精确验证 argv/超时、PR CI 策略和授权清单；同时显式绑定 canonical Jira issue 内容摘要、`inputs/` 下批准计划文件及原始 UTF-8 SHA-256，以及 Git author/committer 姓名邮箱和 GitHub actor login。
- `task-to-pr-event.schema.json`：区分 `imported` 与 `runtime_probe`；关键回读、验证和禁止动作事实只能由 Runtime 生成，并记录等待、失败重试及完整复盘引用。
- `task-to-pr-result.schema.json`：由 hash-chain timeline 派生的脱敏 `ready_for_pr_review`、`blocked` 或 `failed` 结果包。

内容摘要统一使用 UTF-8 canonical JSON：`ensure_ascii=false`、对象键排序、分隔符为 `,` 与 `:`。manifest 计算前将 `authorization.confirmed_manifest_sha256` 置空；result 计算前将 `result_sha256` 置空；journal 首条 `previous_event_sha256` 为 `null`，后续条目引用上一条 `event_sha256`。

跨字段语义由双方 Runtime 共同 fail-closed 校验：`repository.base_branch` 必须等于 `repository.target_branch`，确保范围验证比较的基线就是 PR 目标分支；`agent_id` 与 `project_profile` 最长 128 字符；`remote_name` 必须以字母或数字开头；全部保护分支必须唯一且符合相同的安全分支格式；`scope.included`、`scope.excluded` 各自唯一且彼此不重叠；Jira `allowed_status_categories` 不区分大小写禁止 `Done`。这些限制必须由 developer 执行端、maintainer 验收端和本目录 Schema 同步维护，避免执行端已产生外部写入后才被验收端拒绝。`verification` 摘要覆盖 `command`、`working_directory` 和 `timeout_seconds`。JSON Schema 只表达 argv 的结构，developer Runtime 还在 `Popen` 前执行版本化语义白名单：只接受非交互测试/静态检查，拒绝 Shell/`-c`、Git/GitHub/Jira/网络工具、安装/发布/部署、修改模式和受管状态路径。验证环境使用隔离 HOME、最小 PATH、无业务凭据及常见生态 offline/no-index 设置；协议固定记录 `network_policy=allowlist-only-no-sandbox`，诚实表示它不是内核级无网络沙箱，项目测试代码仍必须是用户确认的可信输入。
非交付结果若在 Jira/Git/PR 可信 probe 前阻塞，五项禁止动作仍逐项记录为 `observed=not_verified`；该值只表示“未核验”，绝不表示“未发生”，并且不能用于 `ready_for_pr_review`。
`ready_for_pr_review` 还要求唯一 `prohibition_baseline` 早于任何外部写入：Runtime 在 manifest 任务分支且工作树/索引干净时记录 Jira 状态、完整 tag refs、release 记录、各保护分支 HEAD、本地 HEAD、可空远端任务分支 SHA 和可空既有 open PR；本地 HEAD 必须等于既有远端任务分支 SHA，远端任务分支不存在时则必须等于远端目标分支 SHA，禁止把运行前预置 commit 归入本轮。最终禁止动作 probe 以集合增量和任务 HEAD 祖先关系核对本运行未 merge、未 Done、未 release/tag、未推进保护分支。ready 必须同时包含真实 Jira Comment 与 Worklog；两者只能由各自专用 `jira_write_readback` 证明 applied 且 `created=true`。幂等标记稳定绑定 `issue_key + agentic_run_id + idempotency_key`，旧运行的相同 key/正文不能充当本运行写入；`created=true` 还必须绑定首次计划的 marker 缺失事实、create 请求前持久化的不可变 attempt 文件、attempt ID/开始时间和写后回读。no-op、计划后并发出现或没有同一 attempt 的回读不能伪装创建；不明确响应只允许持原 attempt 恢复回读。写后事实同时绑定受管计划、plan ID、external ID 和正文摘要。Worklog 另绑定中文标题、详情摘要、started、逐项 `included_work` 与 `excluded_waiting_categories`，且组成秒数之和必须等于真实总耗时。复盘的四个分类各自必须声明 `finding` 或有理由及证据的 `no_finding`；每条 failure、retry、human_intervention 和 waiting 都必须通过至少一个 `outcome=finding` 分类的 `source_event_ids` 被逐项承接。若同时创建 `quality_finding`，对应分类还必须把该 finding 事件列为来源，不能用 finding 对过程事件的单向引用代替分类承接。

交付证据必须证明：写前基线之后产生最终 commit，在该最终 HEAD 上完成全部验证；验证通过后远端任务分支才变化到最终 HEAD，之后新建 PR 并 `probe-pr`。失败修复或整理只要产生新 commit，旧 HEAD 的验证结果即不可复用，必须在新最终 HEAD 上重验。

动作归因只表达可验证区间，不生成或声称 `performed_at`：`git_commit` 由写前本地 HEAD 与最终 HEAD 的非空祖先增量、最终 HEAD 验证事件及后置 Git 回读闭合；`git_push_task_branch` 由写前远端 SHA 为空或旧祖先、后置远端等于最终 HEAD 闭合；PR 当前只支持“写前没有 open PR、写后出现绑定最终 Git 回读的 open PR”的 create-only proof。写前已有 open PR 时，现阶段不能证明本轮 update，Runtime 必须以 capability gap fail closed。probe 时原子追加的 external-action 事件是结果绑定，不代表真实动作发生时间。

信任范围同样是协议的一部分：`probe-pr` 核对的 `github_actor_login` 只证明该 probe 当前 `gh` 会话身份，不证明 Git push actor；maintainer 只读验证 developer 结果包和 Runtime probe 链，不独立访问 Jira、Git 或 GitHub 做第二次外部回读。授权引用必须精确为 `user-confirmation:<ISSUE>:<agentic_run_id>:<approved_plan_sha256>`，任意非空文本、旧运行或旧计划摘要均无效；`authorization.confirmed_by`、确认时间和引用仍是当前会话/协议包内声明，没有独立 Jira author readback 等外部证据时，不能表述为 maintainer 已独立验证的人工批准。
