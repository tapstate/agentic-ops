# 工作流配置

## 1. 目的

工作流配置把 AgenticOps 的通用操作映射到具体项目流程。

AgenticOps 核心绑定研发流程语义，不绑定某一套具体 Jira 工作流。

## 2. 配置范围

一个工作流配置以项目配置项命名，例如 `tapdata` 或 `tapstate`，文件位于 `install-resources/basic/profiles/<project>.yaml`。研发负责人初始化时只选择项目配置项；Jira project、代码仓库、本地路径、流程和策略映射由该 profile 定义。

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
    target_repo:
      source: jira_field
      jira_field: customfield_target_repo
    risk_level:
      source: jira_field
      jira_field: customfield_risk
    current_agent_id:
      source: jira_field
      jira_field: customfield_current_agent_id
    takeover_at:
      source: jira_field
      jira_field: customfield_takeover_at

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
  runs_dir: "<project-ai-workspace>/.agentic-ops/runs"
  run_logs_dir: "<project-ai-workspace>/.agentic-ops/run-logs"
  feedback_dir: "<project-ai-workspace>/.agentic-ops/feedback"

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
- `Profile.jira.project` 是该项目配置项绑定的 Jira project；快速开始初始化不要求研发负责人重复输入。
- `Profile.jira.user` 在共享 profile 中只能使用默认占位值；`workspace init` 会使用研发负责人提供的 Jira 用户写入项目 AI 工作空间中的本地 profile。
- `Profile.local.*` 在共享 profile 中只能使用 `<project-ai-workspace>` 这类占位值；`workspace init` 会把它们物化为当前项目 AI 工作空间中的本地路径。源码目录默认是 `<project-ai-workspace>/repos/<project>`，研发负责人可以通过 `--source-root` 显式确认其它目录。
- 如果项目 AI 工作空间已有 `.agentic-ops/agent.json`、`.agentic-ops/profiles/<project>.yaml` 或 AgenticOps 管理的 `AGENTS.md` 配置块，`workspace init` 必须停止并要求研发负责人确认；确认覆盖时使用 `--confirm-existing-config`。
- `Profile` 必须说明关键专业审查节点如何映射到标准字段、Jira 状态、拉取请求审查、CI 或人工确认。
- `Profile` 必须说明失败后允许重试还是必须重做前序阶段。
- `Profile` 必须能被 `agentic-cli preflight` 校验。
- `Profile` 不得包含 secrets、tokens 或 private keys。
- `Profile` 中的 `repo` 映射必须能解释任务如何定位目标源码。
- `Profile` 缺字段时，AIAgent 不能自行猜测，应请求研发负责人补充。
- `Profile` 缺任务分类、流程映射、Jira 状态或 `transition` 时，AIAgent 必须输出 `gap` 并请求流程负责人决策。
- `transition_mapping` 只表达标准推进动作到标准流程阶段的关系；真实 Jira 工作流的 `transition id` / `transition name` 必须放在 `jira_transition_mapping`，避免把标准流程语义和项目私有 Jira 配置混在一起。

## 5. Jira Form Mapping

Jira Form Mapping 负责解释标准字段如何从具体 Jira project 中取得。

概念结构：

```yaml
jira_form_mapping:
  fields:
    acceptance_criteria:
      source: jira_field
      jira_field: customfield_acceptance
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

Jira `transition` 映射负责把标准推进动作映射到具体 Jira 工作流的 `transition id` 或 `transition name`。`id` 优先；只有没有 `id` 时，AgenticCLI 才会读取 Jira `transition` 并按 `name` 查找 `id`。

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
    next_action: fix_and_verify
  missing_target_repo:
    retry: false
    redo_from_stage: takeover_gate
    next_action: ask_owner
```

当审查节点、重试规则或重做边界无法映射时，工作流配置校验必须返回 `review_gate_mapping_gap` 或 `retry_redo_policy_gap`，并要求流程负责人决策。

## 7. 所有权字段映射

工作流配置必须声明 `current_agent_id` 和 `takeover_at` 如何落到 Jira 或稳定描述模板。`current_agent_id` 是任务当前绑定的 `agent_id`，不是新的身份字段；接管门禁依赖这些字段防止多个 AIAgent 同时处理同一任务。

规则：

- `current_agent_id` 为空时，当前 AIAgent 可以在接管成功后写入自己的 `agent_id`。
- `current_agent_id` 等于当前 AIAgent 的 `agent_id` 时，允许恢复同一代理的执行。
- `current_agent_id` 不为空且不等于当前 AIAgent 的 `agent_id` 时，必须返回 `agent_ownership_conflict`。
- 任务完成或交接结束后，必须清理 Jira 上的 `current_agent_id`，并记录 `current_agent_id_cleared=true`。
- `assignee` 不是当前登录用户时，必须返回 `assignee_mismatch` 或 `assignee_changed`，不得自动接管或自动释放代理绑定。

## 8. 第一批默认配置

第一阶段建议优先设计：

- `tapstate`
- `tapdata`

这两个工作流配置可以共享操作契约，但拥有不同 Jira 空间、GitHub 仓库、本地源码和任务执行上下文。
