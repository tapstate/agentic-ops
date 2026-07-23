# 工作流配置

## 1. 目的

工作流配置把 AgenticOps 的通用 operation 映射到具体项目流程。

AgenticOps 核心绑定研发流程语义，不绑定某一套具体 Jira workflow。

## 2. 配置范围

一个 工作流配置至少应描述：

- 项目 AI 工作空间名称。
- Jira 空间和查询规则。
- Jira Form Mapping，把 AgenticOps 标准字段映射到具体 Jira 字段、描述模板、评论模板或工作空间配置。
- 任务分类映射，把 Jira issue type、label、component、custom field 或描述模板映射到 AgenticOps `task_class`。
- 标准流程映射，把 `task_class` 映射到 Standard Process Registry 中的 `process_id`。
- Jira 状态和 transition 映射。
- 专业审查节点和对应角色映射。
- GitHub organization 和 repo 映射。
- 本地源码目录。
- 允许的写操作。
- 人工确认点。
- 重试和重做规则。
- evidence 模板。
- 事件日志位置。

## 3. 概念结构

```yaml
workspace: tapstate

jira:
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

local:
  source_root: "<project-ai-workspace>/src"
  runs_dir: "<project-ai-workspace>/.agentic-ops/runs"
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

- Profile 可以绑定具体 Jira workflow，但核心 operation 不能依赖某个固定 Jira 状态名。
- Profile 必须适配 Task Form Standard；AIAgent 只消费标准字段，不直接消费 Jira custom field。
- Profile 必须适配 Standard Process Registry；AIAgent 先识别 `task_class`，再选择 `process_id`。
- Profile 必须说明关键专业审查节点如何映射到标准字段、Jira 状态、PR 审查、CI 或人工确认。
- Profile 必须说明失败后允许重试还是必须重做前序阶段。
- Profile 必须能被 `agentic-cli preflight` 校验。
- Profile 不得包含 secrets、tokens 或 private keys。
- Profile 中的 repo 映射必须能解释任务如何定位目标源码。
- Profile 缺字段时，AIAgent 不能自行猜测，应请求研发负责人补充。
- Profile 缺任务分类、流程映射、Jira 状态或 transition 时，AIAgent 必须输出 gap 并请求流程负责人决策。
- `transition_mapping` 只表达标准推进动作到标准流程阶段的关系；真实 Jira workflow 的 transition id/name 必须放在 `jira_transition_mapping`，避免把标准流程语义和项目私有 Jira 配置混在一起。

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

Jira Transition Mapping 负责把标准推进动作映射到具体 Jira workflow 的 transition id 或 transition name。`id` 优先；只有没有 `id` 时，AgenticCLI 才会读取 Jira transitions 并按 `name` 查找 id。

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

如果 Jira workflow、字段或描述模板无法适配标准字段，profile validation 必须返回稳定 gap，例如 `missing_form_field`、`unmapped_jira_field`、`lifecycle_mapping_gap`、`transition_mapping_gap`、`jira_transition_mapping_gap` 或 `task_class_mapping_gap`。

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
      reviewer_required_action: "按 review comments 修复并重新验证"

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

当审查节点、重试规则或重做边界无法映射时，profile validation 必须返回 `review_gate_mapping_gap` 或 `retry_redo_policy_gap`，并要求流程负责人决策。

## 7. 所有权字段映射

工作流配置必须声明 `current_agent_id` 和 `takeover_at` 如何落到 Jira 或稳定描述模板。接管 gate 依赖这些字段防止多个 AIAgent 同时处理同一任务。

规则：

- `current_agent_id` 为空时，当前 AIAgent 可以在接管成功后写入自己的 `agent_id`。
- `current_agent_id` 等于当前 AIAgent 的 `agent_id` 时，允许恢复同一代理的执行。
- `current_agent_id` 不为空且不等于当前 AIAgent 的 `agent_id` 时，必须返回 `agent_ownership_conflict`。
- 任务完成或交接结束后，必须清理 Jira 上的 `current_agent_id`，并记录 `current_agent_id_cleared=true`。
- assignee 不是当前登录用户时，必须返回 `assignee_mismatch` 或 `assignee_changed`，不得自动接管或自动释放代理绑定。

## 8. 第一批默认配置

第一阶段建议优先设计：

- `tapstate`
- `tapdata`

这两个 profile 可以共享 操作契约，但拥有不同 Jira 空间、GitHub 仓库、本地源码和任务执行上下文。
