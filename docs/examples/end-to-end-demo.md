# AgenticOps 真实任务到 PR 全链路测试

## 1. 可验证交付目标

输入一个经确认的真实 Jira 编号，由业务项目工作空间代表的研发员使用真实 Jira、业务仓库、Git 和 GitHub 完成任务实现，并交付一个可审查的真实 PR 与完整结果包。

正式链路的终点固定为 **PR 审查**。测试不执行 merge、不把 Jira 置为 Done、不发布、不创建 tag、不直推保护分支。`offline_fake` 只做离线合同回归，不能代替正式全链路验收。

## 2. 两个工作面的职责

| 工作面 | AI 入口 | 职责 | 禁止事项 |
| --- | --- | --- | --- |
| `maintainer` | 根入口、`ao-maint` 与 `$test-task-to-pr-e2e` | 无副作用能力预检；创建隔离 developer 工作空间；启动相互隔离的 developer/reviewer AI；只读验收结果包 | 不在 maintainer 上下文中执行 Jira/业务 Git/GitHub 写入，不修改业务代码，不把子进程状态继承回源头工作面 |
| `developer` | 业务工作空间入口与 `ao-work` | 执行真实任务、验证、提交、推送、新建 PR，生成审计与复盘 | 不加载维护规则，不修改 AgenticOps 源码，不 merge/Done/release/tag |

两个工作面只通过显式 manifest 和脱敏结果包交接，不能共享 Python import、凭据、状态目录或聊天中的隐含信息。

## 3. 一次性配置与每任务输入

真实全链路首次使用时，maintainer 先创建一次性非敏感配置：

```sh
./maintainer/bin/ao-maint integration prepare-task-to-pr-e2e-config \
  --agent-id harsen-mini-test-bot \
  --project-profile tapdata \
  --expected-confirmer harsen
```

该配置只保存测试研发员身份、Project Profile 和预期确认人，不保存 token，也不是任务授权。运行入口不得从 hostname、系统用户、Git/GitHub 身份、历史聊天或其它本机状态推断或覆盖这些值。变更配置必须走独立审查流程。

正式 manifest 是机器审计合同，不是用户配置表。字段按以下来源解析：

- 一次性工作空间配置：developer 工作空间、`agent_id`、Project Profile、Jira 账户、业务源码和执行身份；
- Project Profile：Jira HTTPS 站点、Project、状态/字段映射、默认仓库和项目策略；
- Jira 卡片：Issue ID、经办人、状态、标题、描述和已配置业务字段；
- Runtime：唯一 `agentic_run_id`、canonical Jira 内容摘要、时间、manifest 摘要和证据路径；
- AI 提议、人工审查：计划、包含/排除范围、任务分支、验证 argv 和本次外部动作权限。

普通研发使用由 `ao-work workspace init` 初始化现有业务工作空间。本文的真实 E2E 测试则由 maintainer Skill 创建全新的隔离工作空间，不要求用户预先初始化；用户只在测试启动时确认真实副作用范围并隐藏输入凭证，结束后审查 PR、结果包和复盘。

之后每次真实测试只指定 Jira key：

```sh
$test-task-to-pr-e2e TAP-12289
```

Skill 在创建工作空间或访问 Jira 前先执行：

```sh
./maintainer/bin/ao-maint integration preflight-task-to-pr-e2e TAP-12289
```

当前预检会安全阻断，并明确返回六个 `capability_gap`：`task_intake_assess`、`solution_gate`、`takeover_task`、`git_commit`、`git_push_task_branch`、`github_pr_create`。因此当前版本还不能满足“全程只依赖 `ao-work` 原子门禁完成真实任务到 PR”的正式运行条件。其中信息准入和方案分级尚只有 Skill 流程，还没有 Runtime digest/变更后重算门禁；Git/Jira 副作用缺口也不得由 AI 直接命令绕过。阻断发生在业务工作空间创建和任何 Jira/业务仓库访问之前，不得把它表述为真实全链路已执行。

AgenticOps source/ref 只用于确认 `ao-maint` / `ao-work` 来自预期安装版本，是正式测试的安装前提，不是 task-to-PR manifest 字段，也不构成业务任务授权。

当前尚未完成 Jira 授权，所以 maintainer 准备阶段必须停在后台 manifest 骨架和简化配置指引：不得读取业务工作空间、`.env`、进程凭据、`~/.agentic-ops` 或相邻文件；不得运行 `ao-work auth jira show`、`auth jira verify`、`probe-jira`、`probe-jira-write` 或其它外部 probe；不得访问 `TAP-12289` 或声称 Jira 身份、issue 内容与权限已验证。准备结果必须明确 `host_state_read=false`、`business_workspace_read=false` 和 `credentials_read=false`。

maintainer Skill 在隔离工作空间中完成隐藏授权并明确允许读取当前任务后，developer 的任务入口只有一个 Jira key：

```sh
ao-work task start TAP-12289
```

该入口自动解析 Jira/工作空间/Profile/Runtime 确定性字段，生成或恢复本地 run。AI 必须先分析缺项，再从 Jira、Profile、业务源码和 Runtime 回读中做带来源的自动补全，展示完整准入摘要供用户确认。确认前不形成最终方案。确认后方案分为 L1 直接实施、L2 用户确认后实施、L3 先修改设计并重新分析、L4 停止升级。每个环节只消费 Runtime 基于实际结果返回的结构化 `agentic_next_action`。

从接管到 PR 审查由全链路配置指定的同一 `task_owner` 完成。`agentic_next_action.executor` 只是当前步骤执行者，不是转派；现役 `ownership_effect` 只能为 `none`。人工确认和独立 reviewer 不改变负责人。转派只保留 `task_transfer=capability_gap` 的停止口，必须由人决定，详细设计后续单独推进。

token、私钥和原始敏感响应不得写入 manifest、命令行、日志或结果包。清单确认后有任何变化，都必须重新展示摘要并确认。

## 4. 原子能力闭合后的正式执行

在 manifest 指定的业务项目工作空间进入 developer AI 入口，调用 `$run-task-to-pr-test`：

1. 校验工作面、manifest 摘要、工作空间初始化、Jira 授权、业务源码和 GitHub 权限。
2. 查询能力目录；只调用 `implemented` 的 `ao-work` 操作。能力缺口按 `next_action` 转人工或使用项目认可工具，不能假装 Runtime 已执行。
3. 读取真实 Jira 并核对 Issue ID、Project、经办人、状态、范围与所有权。
4. 建立 `agentic_run_id`，输出并确认任务计划、验证方式、风险和非范围。
5. 在任何写入前记录干净任务分支基线：本地 HEAD 必须等于既有远端任务分支 SHA；远端任务分支不存在时，必须等于远端目标分支 SHA。随后才修改真实业务代码，并持续保留 AI 判断、人工干预、失败与重试记录。
6. 执行项目验证；Runtime 在子进程前校验命令白名单，并以隔离 HOME、最小 PATH、无业务凭据和 offline/no-index 环境运行。该环境会抑制常见联网路径，但不是内核网络沙箱，结果必须如实标记 `network_policy=allowlist-only-no-sandbox`；范围扩大、验证受阻、需新增命令入口或出现专业取舍时停止。
7. 按项目规则提交和推送任务分支，回读远端 SHA，并受控回写 Jira 中文变更总结和真实 Worklog。Comment/Worklog 的 `created=true` 必须同时证明首次计划时 marker 缺失、create 请求前已持久化当前 run 的不可变 attempt，以及同一 attempt 的写后回读；no-op 或缺少 attempt 时不得归因为本 run 写入。
8. 创建真实 PR，回读 PR URL、Head SHA、CI 和审查事实。当前动作归因只支持写前无 open PR 的 create-only proof；若写前已有 PR，因无法可靠证明本轮 update 必须停止并记录能力缺口。
9. 停在 PR 审查，生成结果包；不继续 merge、Done、release 或 tag。

真实外部写入都要经过与当前内容绑定的门禁，并在写后回读。结果不明确时只允许回读，不能盲目重试。

## 5. 结果包与完整复盘

developer 必须从原始执行记录生成脱敏结果包。除交付证据外，必须真实记录测试中所有摩擦，而不是只写成功摘要：

- Jira、仓库、分支、提交、远端 SHA、PR URL、Head SHA、CI 与授权引用；
- 修改范围、验证结果、残留风险和当前 PR 审查状态；
- 自动化无法完成、需要人工干预、重复操作、失败重试和等待点；
- 输出质量不高、信息不足、流程不合理或容易误用的环节；
- Skill、Runtime、Shell、项目工具、AI 判断与人工动作各自承担了什么；
- 每个问题的证据、影响、根因假设、可复现条件、处理方式和耗时影响；
- 可固化的优化候选，建议落点为 Skill、Python Runtime、Rule、模板、Profile 或固定测试，并标注收益、风险和复现频率。

maintainer 使用显式输入只读验收这份结果包，不改写 developer 原始审计，也不进入 manifest 声明的业务工作空间：

```sh
./maintainer/bin/ao-maint integration accept-task-to-pr TAP-12289 \
  --manifest <manifest.json> \
  --result <result.json>
```

优化候选必须经过人工筛选，再进入独立 AgenticOps 维护任务；测试本身不直接修改共享标准。

## 6. 验收结论

只有以下事实同时成立且由 developer Runtime 的实时 probe 采集、在结果包中形成完整 hash chain，协议级全链路交付才通过：

- 输入和授权完整且内容摘要匹配；
- Jira 账户/经办人/状态映射、Git origin/基线/HEAD/变更范围、GitHub PR/CI 均由 Runtime 回读并与 manifest 绑定；
- manifest 指定验证通过；
- 任务分支已推送，真实 PR 已创建并停在审查；Git/PR 动作只给出写前基线至后置回读的可验证归因区间，不声称精确动作时刻；
- 没有 merge、Jira Done、release、tag 或保护分支直推；
- developer 原始审计、完整摩擦复盘和优化候选齐全且已脱敏。

`accept-task-to-pr` 严格验证字段闭合、canonical digest、事件 hash chain、逐事件授权、`runtime + runtime_probe` 来源、Jira/Git/PR/验证绑定、CI 策略、五项禁止动作、等待点和四类完整复盘。只有可信结果状态为 `ready_for_pr_review` 时输出 `delivery_passed=true`；`blocked` / `failed` 的完整结果包仍只表示“结果包可验收”，不表示交付通过。

验收输出的证据基础是 `developer_runtime_probe_result_package`。maintainer 本身不访问 Jira、Git 或 GitHub，因此同时明确 `independent_external_readback=false`、`cryptographic_remote_attestation=false`；这是对 developer Runtime 实时证据链的协议验收，不得表述为 maintainer 独立外部回读或密码学远程证明。若 Jira Profile 尚未适配 `agentic_id`，可以交付到 PR 审查，但必须报告 `formal_takeover_verified=false`，并以绑定 Jira probe 的 `automation_gap` 和残留风险说明不能声称正式接管。

`blocked` 或 `failed` 结果包在摘要、审计和复盘完整时可以显示 `package_status=accepted`；这只说明结果包可验收，不表示任务交付通过。任一事实缺失时，结论只能是阻塞或未通过，不能把本地代码修改、离线 fixture、结果包自报状态或 PR 准备状态表述为正式全链路完成。

## 7. 离线合同回归

先生成并人工确认离线清单，再运行离线回归：

```sh
./maintainer/bin/ao-maint integration prepare-offline TAP-12289
./maintainer/bin/ao-maint integration run-offline TAP-12289 --manifest <offline-manifest.json>
```

`offline_fake` adapter 只验证隔离 Bootstrap、工作空间初始化、合成任务状态、固定验证与 Fake Jira 评论回读，输出固定为 `offline_fixture_completed`。它不访问 `TAP-12289` 的真实 Jira 卡片，不验证真实业务修改、推送、PR、CI 或审查，也不产生正式验收结论。不存在含混的 `integration prepare` / `integration run` 兼容入口。
