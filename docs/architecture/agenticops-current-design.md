# AgenticOps 当前设计

## 1. 定位

AgenticOps 是面向研发流程的 AI 执行控制体系，用于让 AIAgent 在现有 Jira-centered 研发体系中可控地接管任务、完成开发、运行验证并回写证据。

AgenticOps 不替代 Jira、不替代研发 owner、不替代 PR Review，也不以全自动开发为第一阶段目标。它的核心价值是把 AI 员工从临时聊天助手变成流程内可管理、可追踪、可复盘的执行主体。

一句话定义：

```text
AgenticOps = AI 员工手册 + 项目规则 + 开发风格 + AIAgent 工作规则 + Operation Contract + Workflow Profile + Go CLI Runtime + Evidence Templates + Feedback Loop
```

## 2. 设计目标

第一阶段目标是跑通一条真实、可控、可复用的主链路：

```text
Jira issue 已进入迭代
-> 研发 owner 手动触发 AI
-> AI 拉取 owner 名下待办
-> 研发 owner 选择一个 issue
-> AI 执行任务接管 gate
-> AI 生成 run_id 和接管记录
-> AI 本地开发与验证
-> AI 回写 Jira 证据
-> 研发 owner 确认
-> 授权 push / PR
-> 进入既有 CI / Review / 合入流程
```

第一阶段不阻塞在集成测试完整设计、低风险自动化门槛和 AI Review ROI 上。这些属于后续演进方向。

## 3. 核心原则

- Jira 是任务、需求、owner、迭代、状态、评论和执行证据的事实源。
- Git 仓库是代码、测试、提交和分支的事实源。
- GitHub PR / CI 是 Review、CI、comments 和合入记录的事实源。
- AgenticOps 不创建新的任务管理事实源。
- `run_id` 只追踪一次 AI 执行，不替代 Jira issue key，也不替代 Jira 状态。
- AIAgent 可以推进研发阶段，但不能绕过 workflow profile、gate 和人工确认点。
- 控制规范不能只靠提示词；提示词负责指导，Go CLI Runtime 负责强制检查和结构化输出。

## 4. 仓库与运行边界

当前只有一个公司仓库作为 AgenticOps 的权威源头：

```text
git@github.com:tapstate/agentic-ops.git
```

该仓库统一管理全局通用资料：

```text
docs/          架构、目标定位、用户故事、流程、计划
contracts/     Operation Contract 和 schema
skills/        AgenticOps skills 和 AI 员工工作规则
handbooks/     AI 员工手册
profiles/      workflow profile 示例和默认配置
packages/      agent-task-ops Go CLI runtime
templates/     Jira / PR / evidence 模板
examples/      端到端演示样例
tests/         自动化测试
scripts/       本地和 CI 辅助脚本
```

用户本机默认安装到：

```text
~/.agentic-ops
```

`~/.agentic-ops` 是全局安装和配置目录，不是具体项目或具体任务的运行目录。它可以保存从 release 安装得到的 Go 二进制、安装元数据、全局配置、通用手册、通用 skills、通用 templates 和可安全重建的缓存。

具体项目的运行目录应是项目 AI 工作空间，例如：

```text
tapstate/
tapdata/
```

不同项目 AI 工作空间对应不同 Jira 空间、GitHub 组织/仓库、本地源码目录、workflow profile 和任务执行上下文。

## 5. 资料边界

AgenticOps 必须严格区分两类资料。

项目管理范围资料：

- AgenticOps 源代码
- 项目规则
- AI 员工手册
- skills
- operation contracts
- workflow profiles
- templates
- adapters / CLI / SDK
- 通用文档和示例

具体工作空间产物：

- 业务仓库代码变更
- 任务级分析记录
- 测试结果
- 本地执行日志
- Jira 评论和证据
- GitHub PR、CI、review comments
- `run_id` 对应的 evidence

具体工作空间产物不应混入 `~/.agentic-ops` 的全局安装和配置资料；需要保留时应写入对应 AI 工作空间、目标业务仓库、Jira / PR 证据链，或受控的任务执行记录位置。

## 6. AI 员工手册

AgenticOps 必须包含 AI 员工手册，作为 AIAgent 在研发流程中工作的核心交付物之一。

AI 员工手册同时服务两个对象：

- AIAgent：明确任务类型、当前阶段、下一步动作、工具、流程、gate、证据和停止条件。
- 研发 owner：提供快捷操作方式，让研发能用自然语言或 CLI 指挥 AI 完成任务。

AI 员工手册应覆盖：

- 任务类型：安装、工作空间初始化、AIAgent 初始化、新任务接管、恢复接管、PR comments 修复、工作日志上报、AgenticOps 改进建议。
- 阶段模型：已接收、预检中、等待接管、分析中、开发中、验证中、证据回写中、等待人工确认、阻塞、已交接。
- 下一步动作：由 operation contract、workspace profile、当前 evidence 和人工门禁共同决定。
- 工作入口：拉待办、任务接管、继续失败任务、修复 PR comments、回写证据。
- 行为边界：不自动 push、不自动创建 PR、不自动 merge、不扩大需求范围、不泄露敏感信息。
- 停止条件：需求不清、风险扩大、权限不足、测试无法运行、连续修复失败、需要人工判断。
- 交付要求：代码 diff、测试结果、风险说明、Jira / PR evidence、下一步建议。

AI 员工手册不是普通说明文档，而是 skills、operation contracts、workflow profiles、CLI 命令和 evidence templates 的行为依据。

## 7. 操作契约

Operation Contract 是 AgenticOps 的操作契约层，用于屏蔽 Jira / GitHub / Git 的底层事实差异，向 AIAgent 暴露稳定、统一、可验证的任务操作输入输出规范。

AIAgent 不应直接理解 Jira 字段、状态、transition 或 comment 格式。AIAgent 应理解 AgenticOps 暴露的 operation：

```text
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

每个 operation 至少定义：

- `operation`：操作名。
- `version`：契约版本。
- `purpose`：操作意图。
- `task_type`：适用的任务类型。
- `allowed_stages`：允许执行该 operation 的阶段。
- `input`：结构化输入。
- `preconditions`：前置 gate。
- `output`：结构化输出。
- `failure`：稳定错误码和人工动作。
- `side_effects`：是否可能写 Jira、创建记录、push、创建 PR。
- `human_gate`：是否需要人工确认。

示例：

```yaml
operation: takeover_task
version: 1
purpose: 研发 owner 授权 AIAgent 接管一个已进入迭代的任务。

task_type: task_takeover

allowed_stages:
  - waiting_takeover
  - takeover_gate

input:
  issue_key:
    type: string
    required: true
  workspace:
    type: string
    required: true
  owner:
    type: string
    required: true

preconditions:
  - current_user_must_match_owner
  - issue_must_be_in_allowed_project
  - issue_must_have_acceptance_criteria
  - issue_must_have_target_repo
  - issue_must_have_verification_method

output:
  run_id:
    type: string
  current_stage:
    enum:
      - takeover_started
      - blocked
      - waiting_owner_confirmation
  target_repo:
    type: string
  next_action:
    enum:
      - proceed
      - ask_owner
      - blocked

failure:
  code:
    enum:
      - owner_mismatch
      - missing_acceptance_criteria
      - missing_target_repo
      - missing_permission
      - workflow_transition_not_allowed
  message:
    type: string
  required_human_action:
    type: string

side_effects:
  - may_write_jira_comment
  - may_create_takeover_record
  - must_not_modify_code
  - must_not_create_pr
```

## 8. 工作流配置

AgenticOps 核心绑定研发流程语义，不绑定某一套具体 Jira workflow。

Workflow Profile 负责把 Operation Contract 映射到具体团队流程：

- Jira base URL、project、issue type、JQL。
- Jira 字段映射，例如 owner、sprint、acceptance criteria、target repo、risk。
- Jira 状态和 transition 映射。
- GitHub organization、repo 映射。
- 本地项目 AI 工作空间路径。
- 允许的写操作。
- 人工确认点。
- evidence 模板。

TapData / TapState 的方案 C 是第一套默认 profile，但不能硬编码进核心模型。

## 9. 控制层运行时

第一阶段控制层采用本地优先的 Go CLI Runtime，不做常驻 daemon，也不先做 Web 平台。

shell 只用于 `curl | bash` 安装引导。业务逻辑、operation、policy、adapter、事件日志和反馈分析由 Go CLI 承载。

推荐形态：

```text
packages/agent-task-ops/
  cmd/
    agent-task-ops/
  internal/
    cli/
    config/
    contract/
    feedback/
    git/
    github/
    jira/
    policy/
    workspace/
  testdata/
```

Operation Contract 的机器可读源头在仓库顶层 `contracts/operations/`。Go CLI 可以在构建或运行时读取这些契约，但不在 package 内维护第二份契约源头。

AIAgent 始终调用统一入口：

```sh
agent-task-ops list-tasks --workspace tapstate
agent-task-ops takeover-task TAP-123 --workspace tapstate
agent-task-ops write-evidence --run-id ...
agent-task-ops prepare-pr --run-id ...
```

Go CLI Runtime 的要求：

- stdout 输出结构化 JSON。
- stderr 输出人类诊断日志。
- 所有失败返回稳定 `code`。
- 退出码有固定语义。
- 写操作必须检查 policy、gate 和 confirmation。
- secrets 不允许出现在 stdout、stderr 或事件日志中。
- Linux (linux-amd64 / linux-arm64)、macOS Intel (darwin-amd64) 和 macOS Apple Silicon (darwin-arm64) 都应通过对应平台二进制运行。
- 发布流程必须支持快速构建、发布和自更新。

第一阶段主 CLI 发布目标：

```text
darwin-arm64
darwin-amd64
linux-amd64
linux-arm64
```

安装 bootstrap 允许依赖 `bash`、`curl` 和系统解压工具。`agent-task-ops` 运行时不得依赖 `jq` 或本地 Python 环境。

`agent-task-ops preflight` 应检查 OS、CPU 架构、GitHub CLI、GitHub 登录状态、Jira 凭证、workspace profile 和当前业务仓库匹配关系。

## 10. Git 和 GitHub 边界

Jira 需要强封装，因为 Jira workflow、字段、状态和空间差异较大。

GitHub / Git 不需要做可替换平台级封装，因为当前不会替换。但需要做安全操作级封装，用于控制 AI 员工能怎么使用它们。

允许 AIAgent 直接读取的 Git 操作包括：

```text
git status
git diff
git log
git show
```

高风险动作应通过 AgenticOps operation 或 CLI guard 管控：

```text
git commit
git push
git merge
git rebase
git clean
gh pr create
gh pr edit
```

建议由 Go CLI guard 管控的内部能力。这些能力不直接作为第一阶段 operation 暴露给 AIAgent，AIAgent 仍应调用 Operation Contract 中定义的 operation：

```text
inspect_workspace
summarize_diff
run_verification
prepare_commit
record_commit
prepare_pr
read_pr_comments
classify_pr_comments
fix_pr_comments
check_ci_status
write_pr_evidence
```

## 11. 反馈闭环

AgenticOps 应包含 AIAgent 反馈通道，用于每天分析执行日志并优化 AgenticOps。

反馈闭环：

```text
Go CLI 执行 operation
-> 产生结构化事件日志
-> 每天按 workspace 汇总
-> AIAgent 分析失败、卡点、重复人工确认、规则缺口
-> 生成改进建议
-> 人确认后更新 AgenticOps 规则 / 手册 / contracts / Go CLI
```

第一阶段反馈通道只做分析和建议，不允许 AIAgent 根据日志自动修改 AgenticOps 源头规则。

运行日志应放在具体项目 AI 工作空间：

```text
<project-ai-workspace>/
  .agentic-ops/
    runs/
      2026-07-21/
        TAP-123-takeover-20260721103012-a8f3/
          events.ndjson
          summary.json
          evidence.md
    feedback/
      daily/
        2026-07-21.md
        2026-07-21.json
```

事件日志使用 NDJSON，每条事件只记录安全摘要，不记录 secrets、原始敏感日志、完整 Jira 描述或敏感代码片段。

示例事件：

```json
{
  "timestamp": "2026-07-21T10:30:12+08:00",
  "workspace": "tapstate",
  "run_id": "TAP-123-takeover-20260721103012-a8f3",
  "issue_key": "TAP-123",
  "task_type": "task_takeover",
  "operation": "takeover_task",
  "current_stage": "takeover_gate",
  "next_action": "ask_owner",
  "ok": false,
  "code": "missing_target_repo",
  "duration_ms": 842,
  "human_gate": false,
  "requires_human_action": true,
  "safe_message": "Jira issue 缺少目标仓库信息"
}
```

建议提供反馈命令：

```sh
agent-task-ops feedback collect --workspace tapstate --date 2026-07-21
agent-task-ops feedback analyze --workspace tapstate --date 2026-07-21
agent-task-ops feedback report --workspace tapstate --date 2026-07-21
agent-task-ops feedback propose --workspace tapstate --date 2026-07-21
```

反馈进入 AgenticOps 源头规则前必须经过：

```text
Observation -> Proposal -> Accepted Change
```

## 12. 人工门禁

以下动作必须暂停并等待人工确认：

- 任务接管前 owner 不匹配。
- 需求范围、验收标准、目标仓库或验证方式缺失。
- 实际影响范围超出 Jira 已确认边界。
- 需要改变复杂度、风险等级或需求范围。
- AI 连续修复失败或无法解释失败原因。
- push、创建 PR、重新提交修复。
- PR Review comments 存在需要取舍的修改。
- 合入、发布、线上风险相关动作。

## 13. 安装与工作空间流程

初始化安装目标入口：

```sh
curl -fsSL https://raw.githubusercontent.com/tapstate/agentic-ops/init.sh | bash
```

默认安装到：

```text
~/.agentic-ops
```

项目 AI 工作空间初始化：

```sh
agent-task-ops workspace init --workspace tapstate
```

典型使用：

```sh
agent-task-ops preflight --workspace tapstate
agent-task-ops list-tasks --workspace tapstate
agent-task-ops takeover-task TAP-123 --workspace tapstate
agent-task-ops write-evidence --run-id ...
```

以上命令是第一阶段目标接口，不表示当前仓库已经实现对应脚本、CLI 或配置。

## 14. 第一阶段交付物

第一阶段建议交付：

- 目标定位文档：`docs/strategy/positioning.md`。
- 设计审阅清单：`docs/review-checklist.md`。
- 设计决策记录：`docs/decision-log.md`。
- 项目规则文档：`docs/project-rules.md`。
- 项目开发风格文档：`docs/development-style.md`。
- AIAgent 工作规则文档：`docs/ai-working-rules.md`。
- 用户故事文档：`docs/user-stories/agenticops-user-stories.md`。
- AI 员工手册：`handbooks/ai-employee-handbook.md`。
- Operation Contract 文档：`docs/contracts/operation-contract.md`。
- TapData / TapState workflow profile 草案：`docs/profiles/workflow-profile.md`。
- Go `agent-task-ops` CLI Runtime 设计：`docs/runtime/cli-runtime.md`。
- Jira / GitHub / Git 关键操作 guard 设计：`docs/runtime/cli-runtime.md`。
- Evidence templates 设计：`docs/templates/evidence-templates.md`。
- Feedback Loop 事件日志规范和日报命令：`docs/workflows/feedback-loop.md`。
- 一个端到端 demo issue 脚本：`docs/examples/end-to-end-demo.md`。

第一阶段验收标准：

- 研发 owner 能完成初始化。
- AI 能列出 owner 名下 Jira 待办。
- AI 能接管一个 issue，并执行 gate。
- 接管成功或失败都能写入结构化 Jira 评论。
- AI 能完成一次真实或接近真实的代码修改。
- AI 能运行最小验证并回写结果。
- AI 完成后停在人工确认点。
- 研发 owner 确认后再 push / PR。
- 每次 operation 都有结构化事件日志。
- 每天能生成 feedback report 和改进建议。
