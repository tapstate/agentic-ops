---
name: run-local-integration
description: Prepare and accept an AgenticOps cross-workplane integration protocol, or run the isolated offline contract regression. Use when a maintainer needs to issue a manifest for one explicit Jira task, verify a developer-produced result package, or regression-test bootstrap and Runtime contracts without accessing a real business workspace. Never use this skill to execute the real Jira task, modify business code, push its branch, or create its pull request.
metadata:
  workplane: maintainer
---

# 验收全链路集成测试

只在 `maintainer` 工作面使用。把真实业务执行交给业务项目工作空间中的 `$run-task-to-pr-test`；两个工作面只通过带当前会话/包内用户确认声明的 manifest 和脱敏结果包交接。

## 准备协议

1. 用显式 Jira key 生成新清单：

```sh
ao-maint integration prepare-task-to-pr <ISSUE-KEY> \
  [--agent-id <agent-id>] \
  [--confirmed-by <confirmation-claim>] \
  [--output <path>]
```

`--agent-id` 和 `--confirmed-by` 只预填调用者在本次命令中显式给出的值；省略时仍写入 `REQUIRED`。不得从 hostname、系统用户、Git/GitHub 身份、历史聊天或本机状态推断。

本次真实测试的测试身份不从每任务 manifest 参数设置，而由 `$test-task-to-pr-e2e` 使用的一次性全链路配置提供：

```sh
ao-maint integration prepare-task-to-pr-e2e-config \
  --agent-id harsen-mini-test-bot \
  --project-profile tapdata \
  --expected-confirmer harsen
```

每任务运行只传 Jira key：

```sh
ao-maint integration preflight-task-to-pr-e2e TAP-12289
```

上述配置不包含凭据，也不代替当次任务的准入摘要、方案、授权和真实副作用确认。通用 `prepare-task-to-pr` 仍是显式协议交接入口，不是真实 E2E 的身份配置源。

2. 不让用户逐项填写 manifest。它是机器审计合同，不是配置表。按以下来源解析：

- 工作空间一次性配置：`agent_id`、Project Profile、Jira 账户、业务源码和执行身份；
- Project Profile 默认值：Jira 站点、Project、状态/字段映射、默认仓库、项目流程和固定策略；
- Jira 卡片事实：Issue ID、经办人、状态、标题、描述和已配置业务字段；
- Runtime 自动值：`agentic_run_id`、时间、canonical 内容摘要、manifest 摘要和证据路径；
- AI 提议、人工审查：实施计划、包含/排除范围、任务分支、验证命令和本次外部动作权限。

用户只执行四类动作：首次工作空间配置与隐藏授权、审查任务计划/范围/验证、确认任务级授权、在 PR 等高风险节点确认。确定性字段不得转嫁给用户，也不得因为 manifest 中存在字段就逐项提问。

`$run-task-to-pr-test` 仍必须把所有解析结果写入完整 manifest，并核对 `base_branch == target_branch`、批准计划原始 UTF-8 SHA-256、明确 execution identity 和精确授权引用；任何事实冲突才请求人工决策。

AgenticOps source/ref 只用于确认已经从预期版本安装 `ao-maint` / `ao-work`，是安装前提，不属于正式 task-to-PR manifest，不能混入业务任务授权。

3. 在用户尚未完成 Jira 授权前，只生成后台 manifest 骨架和简化配置指引：

- 不读取业务工作空间、`.env`、进程凭据、`~/.agentic-ops` 或相邻文件；
- 不运行 `ao-work auth jira show`、`auth jira verify`、`probe-jira`、`probe-jira-write` 或其它 Jira/Git/GitHub probe；
- 不访问 `TAP-12289`，不声称 Jira 身份、issue 内容或权限已经验证；
- 输出必须保持 `host_state_read=false`、`business_workspace_read=false`、`credentials_read=false`。

只有用户通过业务工作空间的隐藏输入完成授权，并审阅计划、范围、验证、权限和内容摘要后，才可由 developer 工作面补齐并打开 manifest。用户不直接编辑 `REQUIRED`。

补充边界：

- Profile 状态映射、允许的 Jira 状态分类和可选 `agentic_id` Custom Field 必须来自显式输入；
- `execution_identity` 不从本机用户名、主机名、既有 Git 配置或当前登录继承；
- 禁止动作固定为 merge、Jira Done、release、tag 和保护分支直推，不能用 manifest 放宽。
- 验证环境的 `network_policy=allowlist-only-no-sandbox` 只表示命令白名单、隔离 HOME、最小 PATH 和常见生态 offline/no-index 抑制，不是内核级网络隔离；不得把它表述为项目测试代码绝对无法联网。

凭据只通过业务工作空间认可的隐藏输入或标准输入配置，不写入 manifest、命令行、日志或结果包。确认后字段变化必须重新确认；任意非空授权文本、旧运行或旧计划摘要不能放行。`authorization.confirmed_by`、确认时间和授权引用在当前实现中只是当前会话及协议包内的用户确认声明；没有独立 Jira author readback 等外部证据时，不得称为 maintainer 已独立验证的人工批准。

## 选择执行路径

- 正式全链路自动化：交给 maintainer 的 `$test-task-to-pr-e2e`；它先运行 `preflight-task-to-pr-e2e`，只有所有必需 `ao-work` 原子能力均为 `implemented` 才能创建隔离 developer 工作空间并启动独立 AI。maintainer 不 import developer Runtime，也不在当前上下文执行真实 Jira、业务 Git 或 GitHub 写操作。
- 显式协议交接：已有独立 developer 工作空间时，可把 manifest 路径交给该工作面的 `$run-task-to-pr-test`，但这不等于维护端自动化测试已经创建或驱动了研发员。
- 离线合同回归：先运行 `ao-maint integration prepare-offline <ISSUE-KEY>`，确认离线清单后运行 `ao-maint integration run-offline <ISSUE-KEY> --manifest <path>`。adapter 必须为 `offline_fake`；结果只能是 `offline_fixture_completed`。
- 不提供 `prepare`、`run` 或网络 Jira adapter 等含混兼容入口。不得让 maintainer AI 代替 developer AI 手工跑业务任务。

## 只读验收

运行以下命令只读验收 developer 生成的结果包，不修改原始审计：

```sh
ao-maint integration accept-task-to-pr <ISSUE-KEY> \
  --manifest <manifest.json> \
  --result <result.json>
```

核对以下包内声明与绑定：

- `issue_key`、`agent_id`、`agentic_run_id`、仓库、分支和 manifest 摘要一致；
- 每个事件绑定同一授权；Jira、远端 Git、GitHub PR、验证和禁止动作事实均来自 `actor=runtime`、`evidence_origin=runtime_probe`；
- Jira 账户、经办人、状态映射、可选正式接管字段和 issue 内容摘要一致；批准计划文件摘要贯穿 Jira/Git/PR 可信绑定；Git origin、基线、HEAD、全部提交 author/committer 身份、变更范围和干净工作树一致；
- 写前基线在干净任务分支记录本地 HEAD、可空远端任务分支 SHA 与可空 open PR；本地 HEAD 必须等于既有远端任务分支 SHA，若远端任务分支不存在则必须等于远端目标分支 SHA，不能携带运行前预置 commit。业务验证由 Runtime 在最终 commit 的 HEAD 上精确执行并通过，随后任务分支才被推送和 probe。Git 动作只按基线、最终 HEAD 验证和后置回读形成归因区间，不把 probe 事件 sequence 伪称真实动作时刻；任何产生新 commit 的失败修复都必须在新最终 HEAD 上重新验证；
- 当前 PR 动作仅接受 create-only proof：基线无 open PR，后置出现绑定最终 Git 回读的 open PR。基线已有 PR 时不能证明本轮 update，必须 fail closed 并报告 capability gap；
- `probe-pr` 的 GitHub login 只证明当时 `gh` 会话身份，不证明远端 Git push actor，验收结论不得扩大为 push 身份证明；
- 没有 merge、Jira Done、release、tag、保护分支直推或其它越界副作用；
- Jira Comment/Worklog 的幂等标记绑定当前 issue/run/key，专用回读各唯一一条且 `created=true`；每条还必须绑定首次 plan 的 marker 缺失、create 请求前持久化的不可变 attempt 及同一 attempt 回读。no-op、缺 attempt 或计划后并发出现的对象不能冒充本运行写入；
- developer 已输出完整复盘，覆盖人工干预、失败/重试、能力缺口、输出质量、不合理点、证据及优化候选；每条 failure/retry/human_intervention/waiting 都必须由至少一个 finding 分类的 `source_event_ids` 逐项承接，不能只依赖 quality_finding 的单向引用。

证据不足、外部结果不明确、越界或复盘缺失时必须 fail closed，并只报告缺口。`blocked` / `failed` 结果包可因结构、摘要和复盘完整而显示 `package_status=accepted`，但 `delivery_passed` 必须为 false。

`ready_for_pr_review` 只有在 canonical 摘要、完整 hash chain、逐事件授权、Runtime probe 来源、Jira/Git/PR/验证绑定、CI 策略、五项禁止动作和完整复盘全部通过时，才输出 `delivery_passed=true`。`agentic_id` 尚未适配时不得声称正式接管，必须输出 `formal_takeover_verified=false`，并由与 Jira probe 绑定的 `automation_gap`、完整复盘引用和残留风险明确承接。

证据基础准确标为 `developer_runtime_probe_result_package`。它证明结果包内由 developer Runtime 采集的实时 probe 证据链符合协议，但 maintainer 只执行结果包只读协议验收，不独立访问 Jira、Git 或 GitHub，也不提供密码学远程证明；输出同时固定声明 `independent_external_readback=false` 和 `cryptographic_remote_attestation=false`，不得把它表述成 maintainer 独立回读。

把确认后的优化候选另行进入 AgenticOps 维护流程，不能从测试结果包直接修改标准资产。
