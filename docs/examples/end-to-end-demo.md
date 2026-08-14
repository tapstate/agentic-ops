# AgenticOps 真实任务到 PR 全链路测试

## 1. 可验证交付目标

输入一个经确认的真实 Jira 编号，由业务项目工作空间代表的研发员使用真实 Jira、业务仓库、Git 和 GitHub 完成任务实现，并交付一个可审查的真实 PR 与完整结果包。

正式链路的终点固定为 **PR 审查**。测试不执行 merge、不把 Jira 置为 Done、不发布、不创建 tag、不直推保护分支。`offline_fake` 只做离线合同回归，不能代替正式全链路验收。

## 2. 两个工作面的职责

| 工作面 | AI 入口 | 职责 | 禁止事项 |
| --- | --- | --- | --- |
| `maintainer` | 根入口与 `ao-maint` | 准备并确认 manifest；运行离线合同回归；只读验收结果包 | 不进入业务工作空间，不执行 Jira/业务 Git/GitHub 写入，不修改业务代码 |
| `developer` | 业务工作空间入口与 `ao-work` | 执行真实任务、验证、提交、推送、新建 PR，生成审计与复盘 | 不加载维护规则，不修改 AgenticOps 源码，不 merge/Done/release/tag |

两个工作面只通过显式 manifest 和脱敏结果包交接，不能共享 Python import、凭据、状态目录或聊天中的隐含信息。

## 3. 测试前显式输入

以 `TAP-12289` 为示例，maintainer 先准备清单：

```sh
./maintainer/bin/ao-maint integration prepare-task-to-pr TAP-12289 \
  --agent-id harsen-mini-test-bot \
  --confirmed-by harsen
```

本次只把用户已经显式给出的三个值预填为 `issue.key=TAP-12289`、`agent.agent_id=harsen-mini-test-bot` 和 `authorization.confirmed_by=harsen`。`confirmed_by` 是当前会话/协议包中的确认声明，不是 maintainer 独立核验的 Jira author 身份。通用命令省略两个可选参数时，对应字段仍为 `REQUIRED`；入口不得从 hostname、系统用户、Git/GitHub 身份、历史聊天或本机状态推断。

测试前必须由用户逐项提供并确认以下正式 manifest 字段，不能从本机自动搜集：

- `workspace.root`：独立 developer 工作空间绝对路径；
- `issue`：Jira key、不可变 issue ID、Project key；
- `jira`：HTTPS 站点、当前 accountId、真实经办 assignee accountId、状态映射、允许的状态分类、可选 `agentic_id` Custom Field；
- `agent`：`agent_id`、Project Profile、本次唯一 `agentic_run_id`；
- `task_binding`：canonical Jira issue 内容 SHA-256、`inputs/` 下批准计划文件、该文件原始 UTF-8 SHA-256；
- `execution_identity`：Git author/committer 姓名邮箱、GitHub actor login；
- `repository`：业务仓库绝对路径、slug、remote 名称、基线/任务/目标/保护分支，且 `base_branch == target_branch`；
- `scope`：包含范围与明确排除范围；
- `verification`：每项固定 ID、argv、工作目录和超时；argv 必须是 Runtime 白名单内的非交互测试/静态检查，不能包含 Shell/`-c`、Git/GitHub/Jira/网络工具、安装/发布/部署、修改模式或受管状态路径；
- `pr_endpoint`：GitHub provider、仓库 slug、目标分支和 CI 策略；
- `permitted_external_actions`：逐项允许的 Jira、Git、GitHub 外部动作；
- `authorization`：确认人声明、确认时间、精确绑定 issue/run/批准计划摘要的授权引用和 canonical manifest SHA-256。

AgenticOps source/ref 只用于确认 `ao-maint` / `ao-work` 来自预期安装版本，是正式测试的安装前提，不是 task-to-PR manifest 字段，也不构成业务任务授权。

当前尚未完成 Jira 授权，所以准备阶段必须停在本地 manifest 生成：不得读取业务工作空间、`.env`、进程凭据、`~/.agentic-ops` 或相邻文件；不得运行 `ao-work auth jira show`、`auth jira verify`、`probe-jira`、`probe-jira-write` 或其它外部 probe；不得访问 `TAP-12289` 或声称 Jira 身份、issue 内容与权限已验证。准备结果必须明确 `host_state_read=false`、`business_workspace_read=false` 和 `credentials_read=false`。

token、私钥和原始敏感响应不得写入 manifest、命令行、日志或结果包。清单确认后有任何变化，都必须重新展示摘要并确认。

## 4. 正式执行

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
