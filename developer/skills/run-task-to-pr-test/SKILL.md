---
name: run-task-to-pr-test
description: Coordinate one explicitly authorized real Jira task through a real business repository to a real GitHub pull request review, recording every step in the local task-run audit protocol and producing a truthful ready, blocked, or failed result package. Use in a developer business-project workspace when an integration manifest carrying the current session/package user-confirmation declaration identifies the Jira issue, repository, scope, validation, permissions, and PR review endpoint. Stop at PR review; never merge, move Jira to Done, release, tag, push a protected branch, or modify AgenticOps source.
metadata:
  workplane: developer
---

# 执行真实任务到 PR 测试

只在 `developer` 工作面使用。输入必须是 maintainer 模板准备、当前会话/结果包声明已经用户确认且内容摘要未变化的 manifest。授权引用必须精确为 `user-confirmation:<ISSUE>:<agentic_run_id>:<approved_plan_sha256>`；任意非空文本、旧运行或旧计划摘要均无效。`authorization.confirmed_by`、确认时间和授权引用是当前会话及协议包内声明；在没有独立 Jira author readback 等外部证据时，只能称“manifest 声明的用户确认”，不能称为 maintainer 已独立验证的人工批准。协议唯一事实源是安装根目录 `shared/integration/task-to-pr-*.schema.json`；不要加载 maintainer 规则、维护状态或 AgenticOps 源码。

`ao-work task-run` 同时提供本地审计协议和确定性可信采集入口。外部写动作仍由本 Skill 协调现役 Runtime、项目认可工具、AI 或人工完成；但 Jira、Git、PR、CI、验证和禁止动作事实只能由 Runtime probe 生成，不能用 `record` 导入。

## 授权前停止线

未收到用户对当前业务项目工作空间的明确授权确认前，只输出预检授权清单和安全配置入口；不得读取 `.env`，不得运行 `ao-work auth jira show`、`ao-work auth jira verify` 或任何 Jira probe，也不得从本机、Shell 环境、其它工作空间或历史对话发现凭证。需要配置时，由用户在隐藏输入中执行 `ao-work auth jira set`；确认授权后才能按 manifest 继续真实 Jira 读取或写入。

## 校验输入

1. 核对 manifest 的 Jira key、业务工作空间、`agent_id`、Project Profile、业务仓库、基线/任务/目标分支、保护分支、修改与非范围、验证 argv、允许外部动作、授权引用、PR endpoint 和确认摘要；同时显式核对 `task_binding` 中 Jira issue 内容摘要、`inputs/` 下批准计划文件及其原始 UTF-8 SHA-256，以及 `execution_identity` 中 Git author/committer 姓名邮箱和 GitHub actor login。不得从操作系统用户名、主机名、既有 Git 配置或当前 `gh` 登录临场推断这些身份。
2. 运行 `ao-work capability list|show`。已实现操作才调用 `ao-work`；能力缺口按中文 `next_action` 转用项目认可工具或请求人工，禁止虚构旧命令。
3. 检查工作空间初始化、Jira 授权、源码和 GitHub 权限。任何事实不一致或输入缺失都在副作用前停止。
4. 执行 `ao-work task-run open --manifest <工作空间内相对路径>`。只能传相对普通文件；不得使用绝对路径、越界路径或 symlink。open 失败时不继续外部操作。

## 推进到 PR 审查

1. `record` 只导入 Skill、AI、人工或项目工具产生的非关键过程事件，并设置 `evidence_origin=imported`；不得设置 `actor=runtime`，不得导入 readback、verification 或 prohibition_check。
2. 在任何 Jira/Git/GitHub 写入、提交或推送前，先切到 manifest 任务分支并确保工作树与索引干净，再执行 `ao-work task-run probe-prohibition-baseline --manifest <...>`。Runtime 使用 manifest 明确允许的三类只读权限，记录 Jira 非 Done 状态、完整远端 tag refs、GitHub release 记录、各保护分支 HEAD、本地 HEAD、可空远端任务分支 SHA和可空既有 open PR；若远端任务分支已存在，本地 HEAD 必须与其一致；若不存在，本地 HEAD 必须与远端目标分支一致。这样写前预置 commit 不能被后续微小提交伪装为本运行产出；基线失败或补录过晚必须停止并使用新的运行。
3. 执行 `ao-work task-run probe-jira --manifest <...>`，由 Runtime 使用当前工作空间凭证实时 GET `myself` 和 issue，核对站点、Project、Issue ID、经办人、Profile 状态映射及非 Done 状态。没有稳定 `agentic_id` 映射时记录字段适配缺口和 `formal_takeover_verified=false`，不虚构正式接管。
4. 使用 manifest 中既有 `agentic_run_id`，记录分析、计划、风险、验证和明确非范围。只有与 manifest 绑定且事件中引用相同授权的外部动作才执行写入。
5. 在独立任务分支修改业务代码。持续记录 Skill、Runtime、项目工具、AI 和人工的边界；人工介入、失败、重试和每个质量问题必须使用对应事件类型，不得只藏在自然语言总结。
6. 完成预期代码修改后，先按项目规则创建最终任务提交；此时还不推送。
7. 在该最终提交的 HEAD 上，对 manifest 中每个验证执行 `ao-work task-run verify --manifest <...> --verification-id <id>`。Runtime 只接受固定白名单内的非交互测试/静态检查 argv：拒绝 Shell 与解释器 `-c`、`git`/`gh`/网络工具、`ao-work`/`ao-maint`、安装/发布/部署命令、修改模式，以及指向 `.agentic-ops`、`.git`、`.env` 或 Runtime `.local` 的路径；命令在启动子进程前不满足语义白名单即阻断。执行使用临时隔离 HOME、最小 PATH、无 Jira/GitHub/SSH 凭据的环境，并令常见包工具进入 offline/no-index 模式；该边界是 fail-closed 命令白名单和环境抑制，不是操作系统网络沙箱，不能把它表述为项目测试代码绝对无法联网。只有用户确认的可信项目测试可进入 manifest；需要新增入口时先评审并版本化 Runtime 白名单。Runtime 同时限制工作目录、超时和输出，把 `head_sha` 绑定到结果；结果只保存退出码、时长、输出大小摘要、`network_policy=allowlist-only-no-sandbox` 与 digest。失败修复后可以对同一 id 重测，最终以最新可信尝试为准，但每次被取代的失败都必须有更早 failure 与后续 `retry(outcome=succeeded)` 并纳入复盘。失败修复、整理或其它动作只要产生新 commit，就必须在新的最终 HEAD 上重新执行全部指定验证；不得沿用旧 HEAD 的通过结果。
8. 全部最终 HEAD 验证通过后，推送任务分支；随后执行 `ao-work task-run probe-git --manifest <...> --bind-action git_commit --bind-action git_push_task_branch`，由 Runtime 核对 raw/effective fetch/push URL、精确 GitHub owner/repository、无 Git URL rewrite、当前分支/提交、远端 SHA、干净工作树和变更范围。`git_commit` 归因要求写前本地 HEAD 是不同的最终 HEAD 祖先、增量提交身份符合 manifest，并绑定全部最终 HEAD 验证事件；`git_push_task_branch` 归因要求写前远端为空或为最终 HEAD 的旧祖先，写后远端变化到最终 HEAD。这里只证明基线至后置回读的区间，不生成或声称准确动作时间；probe 时追加的 external-action 事件也不等于真实动作时刻。
9. 当前只对本运行新建 PR 提供可靠动作归因：先确认写前基线没有 open PR，创建目标为 manifest 指定分支的真实 PR；随后执行 `ao-work task-run probe-pr --manifest <...> --bind-action github_pr_create_or_update`，强制写后 PR open、非 draft、未合并，且 head/base/SHA 与可信 Git probe 一致，并按 manifest `ci_policy` 核对 CI。若写前已有 open PR，现阶段无法证明本轮 update，必须以 capability gap fail closed，不能绑定动作。Runtime 对 `github_actor_login` 的核对只证明执行 `probe-pr` 时当前 `gh` 会话的登录身份，不证明远端任务分支由同一 actor 推送，也不是 Git push actor attestation；结果包和总结不得扩大这一结论。`not_required` 只放宽 CI 门禁，不改写 Runtime 实际观察到的 `ci_status`；未知非空终态按 failed 处理。
10. 在 PR 审查节点停止并执行 `ao-work task-run probe-prohibitions --manifest <...>`。Runtime 将 tag/release/保护分支完整快照与写前基线比较，并核对保护分支是否包含任务 HEAD；不得 merge、将 Jira 置为 Done、release、创建 tag、直推保护分支或清理尚需审查的业务事实。

外部写入使用 `plan -> apply -> readback` 或项目认可的等价门禁。结果不明确时只回读，不能盲目重试。
正式 `ready_for_pr_review` 必须同时完成一条真实中文 Jira Comment 和一条真实 Worklog；两者必须先用现役 Jira `plan -> apply -> readback` 完成受控写入，再分别立即执行 `ao-work task-run probe-jira-write --manifest <...> --plan-file <apply 使用的受管计划> --confirm-plan-id <plan-id>`。Comment/Worklog 幂等标记固定绑定 `issue_key + agentic_run_id + idempotency_key`，旧运行即使 key 和正文完全相同也不能复用。`plan` 必须先证明精确标记不存在；`apply` 在真正 create 请求前原子写入不可变 attempt 文件，绑定 plan/run/授权与开始时间。响应不明确时只能持原 plan 和原 attempt 做 readback，不能重试写入；没有同一 attempt 的回读不能声明 `created=true`，计划时已存在或并发出现的对象只能 `created=false` 或阻断。该 probe 在外部请求前检查 `jira_read` 和对应写权限，实时回读并原子绑定外部动作、issue/run、受管 plan/attempt 路径、`plan_id`、幂等键、`external_id`、写前缺失、attempt ID/时间、`created` 事实及计划/正文摘要；只有两类都完成“marker absent -> create attempt -> readback”且 `created=true` 才可 ready。Worklog 必须用 `included_work` 逐项记录中文处理说明和正整数秒数，且总和等于 `time_spent_seconds`；`excluded_waiting_categories` 必须明确列出未计入的等待类别，不能用单一布尔值替代。通用 issue `probe-jira` 不能替代两类写后回读；不得用 `record` 手工声明 applied。未到达 ready 的 blocked/failed 只如实记录已经发生的写入，不能补造 Comment 或 Worklog。

## 生成结果包与复盘

先逐项审查 `automation_gap`、`manual_friction`、`output_quality`、`unreasonable_process` 四类问题。唯一 `retrospective.category_reviews` 必须为每类明确写 `outcome=finding` 或 `outcome=no_finding`；两种结论都必须提供具体 `rationale` 和至少一条 `evidence_references`，并用 `source_event_ids` 明确列出支撑该分类结论的过程事件。存在该类 `quality_finding` 时必须选择 `finding` 并把全部对应 finding 事件列为来源；不得用“已审查”笼统掩盖空结论。每一条 `failure`、`retry`、`human_intervention` 和 `waiting` 都必须被至少一个 `outcome=finding` 的分类复盘列入 `source_event_ids`，不能只汇总在顶层 ID 数组，也不能只由 `quality_finding.evidence_reference` 单向引用。发现的问题逐条记录 `quality_finding`，包括证据、影响、根因假设、复现、脱敏样例、改进候选、建议载体、收益、风险和频率。复盘还必须完整引用本次所有人工介入、失败、重试和改进候选，并对候选排序。

最后必须显式选择真实结论，不能默认猜测：

```sh
ao-work task-run finalize --manifest <工作空间内相对路径> --status ready_for_pr_review --next-action <等待研发工程师审查>
ao-work task-run finalize --manifest <工作空间内相对路径> --status blocked --next-action <解除阻塞所需人工动作>
ao-work task-run finalize --manifest <工作空间内相对路径> --status failed --next-action <失败后的明确处置>
```

只有 Runtime 可信 Jira/Git/PR/CI probe、真实 Jira Comment 与 Worklog 专用写后回读、真实 `git_commit`、全部 Runtime 验证、五项禁止动作和复盘都闭合，才可选择 `ready_for_pr_review`。到达 PR 前阻塞或失败时仍要记录已经发生的外部动作与回读、禁止动作审计、完整分类复盘和下一步，再生成 `blocked` 或 `failed` 结果；不得补造缺失事实或伪装通过。任一禁止动作 `observed=true` 都是事故，只能定性为 `failed` 并保留越权证据，不能输出 `ready_for_pr_review` 或普通 `blocked`。

脱敏结果包至少包含：

- manifest 摘要、Jira 身份、`agent_id`、`agentic_run_id`；
- 仓库、任务/目标分支、提交、远端 SHA、PR URL、Head SHA 与 CI 事实；
- 修改摘要、验证结果、Jira/Git/GitHub 授权及回读引用、残留风险；
- 每次人工干预的触发点、原因、处理与耗时影响；
- 自动化失败、重复步骤、等待、能力缺口、输出质量问题和不合理流程；
- 哪些处理由 Skill/Runtime/脚本完成，哪些由 AI 判断或人工完成；
- 每个问题的证据、影响、根因假设、可复现条件和去敏后的最小样例；
- 优化候选及建议载体（Skill、Python Runtime、Rule、模板、Profile 或测试），按收益、风险和复现频率排序。

不要为了测试“顺利”隐藏摩擦，也不要在 developer 工作面直接实施优化。结果包由 Runtime 原子写入当前任务运行目录，并带 manifest/result 内容摘要及 hash-chain timeline。maintainer 只对显式提供的结果包执行只读协议验收，不会据此独立访问 Jira、Git 或 GitHub 做第二次外部回读；经人工确认的候选另行进入 AgenticOps 维护流程。
