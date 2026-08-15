# 集成验收标准边界

正式“真实任务到 PR”交接只使用 `shared/integration/task-to-pr-*.schema.json`。`ao-maint integration prepare-task-to-pr` 按共享 manifest Schema 生成带 `REQUIRED` 的待确认模板，`accept-task-to-pr` 只读核对显式提供的 manifest 与结果包。manifest 必须显式包含 `task_binding`（canonical Jira issue 内容摘要、`inputs/` 下批准计划文件及原始 UTF-8 SHA-256）和 `execution_identity`（Git author/committer 姓名邮箱、GitHub actor login），不得由本机环境或既有登录推断。

`offline-manifest.schema.json` 和 `offline-manifest.template.json` 只服务 `prepare-offline` / `run-offline` 的隔离合同回归。它们不访问真实 Jira、业务仓库或 GitHub，也不能产生正式全链路验收结论。

正式结果包的证据基础是 `developer_runtime_probe_result_package`。maintainer 必须严格验证 canonical digest、完整事件 hash chain、逐事件授权、关键事实的 `actor=runtime` / `evidence_origin=runtime_probe`、Jira/Git/PR/验证绑定、CI 策略、五项禁止动作和完整复盘；还必须确保最终 commit 后的最终 HEAD 完成全部验证，随后才 push/probe-git 和创建/probe PR，任何产生新 commit 的失败修复均在新最终 HEAD 上重验。只有协议状态为 `ready_for_pr_review` 且全部语义门禁通过时，才输出 `delivery_passed=true`。

maintainer 只做结果包只读协议验收，不独立访问 Jira、Git 或 GitHub，也不提供密码学远程证明，所以验收输出必须同时声明 `independent_external_readback=false` 和 `cryptographic_remote_attestation=false`。`probe-pr` 的 `github_actor_login` 仅证明当时 `gh` 会话身份，不证明远端 Git push actor，输出不得扩大为 push 身份证明。授权引用只接受精确的 `user-confirmation:<ISSUE>:<agentic_run_id>:<approved_plan_sha256>`，任意非空文本、旧运行或旧计划摘要无效；`authorization.confirmed_by`、确认时间和引用仍是当前会话/协议包内声明，没有独立 Jira author readback 等外部证据时，不得表述为 maintainer 独立验证的人工批准。`blocked` / `failed` 可以因结果包完整而 `package_status=accepted`，但 `delivery_passed` 必须为 false。Jira Profile 未适配 `agentic_id` 时不得声称正式接管，必须保留与 Jira probe 绑定的 `automation_gap` 和残留风险。
