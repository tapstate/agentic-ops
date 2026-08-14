# 工作流配置

> 本文定义目标配置语义，不代表所有对应 Runtime 命令已经实现。现役可调用性只以 `ao-work capability list|show` 为准；`profile_resolve`、`update_task_form` 和 Jira transition 当前为 `capability_gap` 时必须停止或转人工。

## 1. 目的

工作流配置把 AgenticOps 的通用操作映射到具体项目流程。

AgenticOps 核心绑定研发流程语义，不绑定某一套具体 Jira 工作流。

## 2. 配置范围

一个工作流配置以项目配置项命名，例如 `tapdata` 或 `tapstate`，源头位于 `developer/standards/projects/<project>/profile.yaml`。研发工程师初始化时只选择项目配置项；Jira project、代码仓库、本地路径、流程和策略映射由该 profile 定义。

运行时 effective profile 按以下顺序解析：

```text
项目工作空间 overlay
> ~/.agentic-ops/user/
> developer/standards/projects/<project>/
> developer/standards/company/
> ao_work 固定兜底
```

该顺序只用于配置和 profile 字段来源解析，不等同于规则冲突优先级。规则冲突必须按 `项目规则 > AIAgent 规则 > 公司规则 > 个人规则` 执行；个人层可以提供本机默认值，但不能覆盖更高优先级规则。

项目 AI 工作空间只保存 `.agentic-ops/profile.local.yaml` 这类本地 overlay，不复制完整全局项目 profile。`~/.agentic-ops` 的 developer-only 安装更新后，现有命令会按工作空间绑定的 Project Profile 读取最新项目包；独立 `profile_resolve` 操作仍是目标能力，当前目录标记为 gap 时不得构造命令。

一个工作流配置至少应描述：

- 项目配置项名称。
- Jira 空间和查询规则；初始化时写入当前 Jira 用户。
- Jira Form Mapping，把 AgenticOps 标准字段映射到具体 Jira 字段、描述模板、评论模板或工作空间配置。
- 任务分类映射，把 Jira 卡片类型、标签、组件、自定义字段或描述模板映射到 AgenticOps `task_class`。
- 标准流程映射，把 `task_class` 映射到 Standard Process Registry 中的 `process_id`。
- Jira 状态和 `transition` 映射。
- 专业审查节点和对应角色映射。
- Jira 空间到代码仓库的映射：一个 Jira 空间可以对应若干 GitHub 仓库，必须说明默认仓库和匹配规则；本地源码目录由工作空间初始化生成，可使用共享 profile 的占位默认值。
- 允许的写操作。
- 人工确认点。
- 重试和重做规则。
- 证据模板。
- 事件日志位置。

## 3. 概念结构

```yaml
workspace: tapstate

jira:
  user: dev@example.com
  project: TAP
  task_query: "assignee = currentUser() AND status in (...)"

jira_form_mapping:
  fields:
    owner:
      source: jira_field
      jira_field: assignee
    acceptance_criteria:
      source: jira_field
      jira_field: customfield_acceptance
      writable: true
    target_repo:
      source: jira_field
      jira_field: customfield_target_repo
    risk_level:
      source: jira_field
      jira_field: customfield_risk
    agentic_id:
      source: jira_field
      jira_field: customfield_agentic_id
    agentic_run_id:
      source: jira_field
      jira_field: customfield_agentic_run_id
    agentic_takeover_at:
      source: jira_field
      jira_field: customfield_agentic_takeover_at
    agentic_next_action:
      source: jira_field
      jira_field: customfield_agentic_next_action
    agentic_completion_evidence:
      source: jira_field
      jira_field: customfield_agentic_completion_evidence
    agentic_heartbeat_at:
      source: jira_field
      jira_field: customfield_agentic_heartbeat_at

task_class_mapping:
  issue_types:
    Story: feature_change
    Bug: bug_fix
    Task: technical_task
  labels:
    investigation: investigation
    agenticops-improvement: process_improvement

standard_process_mapping:
  feature_change: development_change_v1
  bug_fix: development_change_v1
  technical_task: development_change_v1
  investigation: investigation_v1
  process_improvement: agenticops_improvement_v1

github:
  organization: tapstate
  repositories:
    default: tapstate/example-repo
    by_component:
      api: tapstate/tap-api
      web: tapstate/tap-web
    by_label:
      cli: tapstate/agentic-ops

local:
  workspace_root: "<project-ai-workspace>"
  source_root: "<project-ai-workspace>/src"
  tasks_dir: "<project-ai-workspace>/.agentic-ops/tasks"
  runs_dir: "<project-ai-workspace>/.agentic-ops/tasks/<ISSUE-KEY>/runs"
  run_logs_dir: "<project-ai-workspace>/.agentic-ops/tasks/<ISSUE-KEY>/runs/<agentic_run_id>"
  feedback_dir: "<project-ai-workspace>/.agentic-ops/tasks/<ISSUE-KEY>/feedback"
  audit_dir: "<project-ai-workspace>/.agentic-ops/tasks/<ISSUE-KEY>/audit"
  handoff_dir: "<project-ai-workspace>/.agentic-ops/tasks/<ISSUE-KEY>/handoff"

human_gates:
  - push
  - create_pr
  - merge
  - scope_change

review_gates:
  pr_review:
    role: reviewer
    decision_field: reviewer_decision
    returned_next_action: fix_and_verify
  qa_verification:
    role: qa
    decision_field: reviewer_decision
    returned_next_action: redo_previous_stage

retry_redo:
  verification_failed:
    retry: true
    max_attempts: 3
    redo_from_stage: null
  scope_changed:
    retry: false
    redo_from_stage: takeover_gate

templates:
  takeover_success: templates/evidence/takeover-success.md
  takeover_failed: templates/evidence/takeover-failed.md
  blocked: templates/evidence/blocked.md
  development_completed: templates/evidence/development-completed.md
```

## 4. 配置规则

- 工作流配置可以绑定具体 Jira 工作流，但核心操作不能依赖某个固定 Jira 状态名。
- 工作流配置必须适配 Task Form Standard；AIAgent 只消费标准字段，不直接消费 Jira 自定义字段。
- `Profile` 必须适配 Standard Process Registry；AIAgent 先识别 `task_class`，再选择 `process_id`。
- `Profile.workspace` 必须与 profile 文件名中的项目配置项一致，例如 `tapdata.yaml` 对应 `workspace: tapdata`。
- `Profile.jira.project` 是该项目配置项绑定的 Jira project；快速开始初始化不要求研发工程师重复输入。
- `Profile.jira.user` 在共享 profile 中只能使用默认占位值；`workspace init` 会使用研发工程师提供的 Jira 用户写入 `.agentic-ops/profile.local.yaml`。
- `Profile.jira.base_url` 可以提供项目默认 Jira 地址；Jira Cloud 使用站点根地址，例如 `https://tapdata.atlassian.net`，不包含 `/jira`。真实账户只保存在当前业务项目工作空间的受保护凭证文件中；进程环境默认不作为凭证来源。外部脚本和 AIAgent 通过 `ao-work` 的受控配置与授权入口读取脱敏状态。
- `Profile.local.*` 在共享 profile 中只能使用 `<project-ai-workspace>` 这类占位值；`workspace init` 会把本地路径写入 `.agentic-ops/profile.local.yaml`。源码目录默认是 `<project-ai-workspace>/repos/<project>`，目录不存在或为空时初始化会从 `github.repositories.default` 下载项目代码；目录已存在且非空时直接复用。研发工程师可以通过 `--source-root` 显式确认其它目录。
- 如果项目 AI 工作空间已有完整的 `.agentic-ops/agent.json`、`.agentic-ops/profile.local.yaml` 和 AgenticOps 管理的 `AGENTS.md` 配置块，`workspace init` 必须停止并要求研发工程师确认；确认覆盖时使用 `--confirm-existing-config`。只存在部分受管文件时视为上次初始化未完成，允许同项目初始化自动修复。
- `workspace init` 必须先持久化已确认的 Jira 本机配置，再执行源码下载，最后写入 workspace overlay、`agent.json` 和 `AGENTS.md` 管理块。源码下载失败时不得丢失用户已输入的 Jira token，也不得新建表示初始化完成的 overlay。
- `Profile` 必须说明关键专业审查节点如何映射到标准字段、Jira 状态、拉取请求审查、CI 或人工确认。
- `Profile` 必须说明失败后允许重试还是必须重做前序阶段。
- `Profile` 必须能被 `ao-work` 前置检查校验。
- `Profile` 不得包含 secrets、tokens 或 private keys。
- `Profile` 中的 `repo` 映射必须能解释任务如何定位目标源码。
- `Profile` 缺字段时，AIAgent 不能自行猜测，应请求研发工程师补充。
- `Profile` 缺任务分类、流程映射、Jira 状态或 `transition` 时，AIAgent 必须输出 `gap` 并请求流程负责人决策。
- `transition_mapping` 只表达标准推进动作到标准流程阶段的关系；真实 Jira 工作流的 `transition id` / `transition name` 必须放在 `jira_transition_mapping`，避免把标准流程语义和项目私有 Jira 配置混在一起。

## 5. Jira Form Mapping

Jira Form Mapping 负责解释标准字段如何从具体 Jira project 中取得。

`writable: true` 是目标 `update_task_form` 能力的显式写入白名单。该能力当前仍是 `capability_gap`，不能自动写 Custom Field；即使未来实现，未声明该字段的映射也只能读取，`owner`、`assignee`、所有权字段、Description 章节和 Comment 映射不得通过它写入。

概念结构：

```yaml
jira_form_mapping:
  fields:
    acceptance_criteria:
      source: jira_field
      jira_field: customfield_acceptance
      writable: true
      required_from_stage: iteration_ready
    target_repo:
      source: jira_field
      jira_field: customfield_target_repo
      fallback: workspace_repo_mapping
      required_from_stage: takeover_gate
    verification_method:
      source: jira_description_section
      section: 验证方式
      required_from_stage: takeover_gate
```

## 6. Jira Transition Mapping

Jira `transition` 映射负责把标准推进动作映射到具体 Jira 工作流的 `transition id` 或 `transition name`。目标裁决是 `id` 优先，缺少 `id` 时只允许按唯一 `name` 查找；当前 Runtime 尚未实现 transition 执行与解析，不得据此推断 `ao-work` 已能流转状态。

概念结构：

```yaml
transition_mapping:
  start_progress: implementation
  complete: completed

jira_transition_mapping:
  start_progress:
    name: Start Progress
  complete:
    id: "31"
```

如果 Jira 工作流、字段或描述模板无法适配标准字段，工作流配置校验必须返回稳定缺口，例如 `missing_form_field`、`unmapped_jira_field`、`lifecycle_mapping_gap`、`transition_mapping_gap`、`jira_transition_mapping_gap` 或 `task_class_mapping_gap`。

## 7. 审查、重试和重做映射

工作流配置必须把专业审查节点映射为 AgenticOps 可理解的结果。

概念结构：

```yaml
review_gates:
  pr_review:
    source: github_pr_review
    role: reviewer
    accepted_values:
      - approved
      - changes_requested
      - blocked
    output_fields:
      reviewer_decision: changes_requested
      reviewer_required_action: "按审查意见修复并重新验证"

retry_redo:
  verification_failed:
    retry: true
    max_attempts: 3
    agentic_next_action: fix_and_verify
  missing_target_repo:
    retry: false
    redo_from_stage: takeover_gate
    agentic_next_action: ask_owner
```

当审查节点、重试规则或重做边界无法映射时，工作流配置校验必须返回 `review_gate_mapping_gap` 或 `retry_redo_policy_gap`，并要求流程负责人决策。

## 7. 所有权字段映射

工作流配置必须声明 `agentic_id`、`agentic_run_id`、`agentic_takeover_at`、`agentic_next_action`、`agentic_completion_evidence` 和 `agentic_heartbeat_at` 如何映射到 Jira。`agentic_id` 是任务当前绑定的 `agent_id`，不是新的身份字段；接管门禁依赖状态转换和这些字段防止多个 AIAgent 同时处理同一任务。

规则：

- `agentic_id` 为空时，当前 AIAgent 可以在接管成功后写入自己的 `agent_id`。
- `agentic_id` 等于当前 AIAgent 的 `agent_id` 时，允许恢复同一代理的执行。
- `agentic_id` 不为空且不等于当前 AIAgent 的 `agent_id` 时，必须返回 `agent_ownership_conflict`。
- 任务完成或交接结束后，必须清理 Jira 上的 `agentic_id`，并记录 `agentic_id_cleared=true`。
- `assignee` 不是当前登录用户时，必须返回 `assignee_mismatch` 或 `assignee_changed`，不得自动接管或自动释放代理绑定。

## 8. 第一批默认配置

第一阶段建议优先设计：

- `tapstate`
- `tapdata`

这两个工作流配置可以共享操作契约，但拥有不同 Jira 空间、GitHub 仓库、本地源码和任务执行上下文。
