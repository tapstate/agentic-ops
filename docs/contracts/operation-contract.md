# 操作契约

## 1. 目的

Operation Contract 是 AgenticOps 的操作契约层，用于屏蔽 Jira / GitHub / Git 的底层事实差异，向 AIAgent 暴露稳定、统一、可验证的任务操作输入输出规范。

AIAgent 面向 operation 工作，不直接面对 Jira 字段、Jira 状态、Jira transition 或 Jira comment 模板。

## 2. 契约原则

- 每个 operation 必须有稳定输入、输出、失败码和副作用说明。
- 写操作必须声明副作用。
- 需要人工确认的 operation 必须声明 `human_gate`。
- CLI stdout 必须输出结构化 JSON。
- stderr 只输出人类诊断日志。
- secrets 不得出现在 stdout、stderr 或事件日志中。

## 3. 第一阶段操作

| Operation | Purpose |
| --- | --- |
| `install` | 安装 AgenticOps 到 `~/.agentic-ops`。 |
| `assets_install` | 安装或更新 AI 员工手册、契约、profile、policy、runbook 和 template 等运行资产。 |
| `workspace_init` | 初始化项目 AI 工作空间。 |
| `agent_init` | 初始化 AIAgent 能力。 |
| `list_tasks` | 列出当前 owner 可处理任务。 |
| `takeover_task` | 接管一个新的 Jira issue。 |
| `resume_takeover` | 恢复已有 `run_id` 的接管任务。 |
| `read_task_context` | 读取任务上下文摘要。 |
| `write_evidence` | 写入 Jira / PR evidence。 |
| `mark_blocked` | 记录阻塞原因和人工动作。 |
| `request_owner_confirmation` | 请求研发 owner 确认。 |
| `prepare_pr` | 准备 PR，不绕过人工确认。 |
| `fix_pr_comments` | 按 PR comments 修复。 |
| `feedback_collect` | 收集工作空间事件日志。 |
| `feedback_analyze` | 分析执行失败、阻塞和重复问题。 |
| `feedback_report` | 生成每日反馈报告。 |
| `feedback_propose` | 生成改进建议。 |

当前 `contracts/operations/` 只维护第一阶段本地 fake flow 已落地或直接需要的机器可读 YAML。未进入当前可运行闭环的 operation 先保留在本文档中作为后续契约范围，不视为已实现 CLI 命令。

## 4. 契约结构

每个 operation contract 至少包含：

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

human_gate:
  required: false
```

## 5. 错误模型

错误必须稳定、可聚合、可反馈分析。

建议错误字段：

```json
{
  "ok": false,
  "operation": "takeover_task",
  "code": "missing_target_repo",
  "message": "Jira issue 缺少目标仓库信息",
  "required_human_action": "请补充 target_repo 或 workspace repo 映射"
}
```

错误码命名使用 lowercase snake_case。

## 6. 副作用规则

Operation 必须明确副作用：

- 是否写 Jira。
- 是否写 PR。
- 是否写本地事件日志。
- 是否修改代码。
- 是否创建 commit。
- 是否 push。
- 是否创建 PR。

任何涉及 `git commit`、`git push`、`gh pr create`、`gh pr edit`、merge 或发布的 operation 必须要求人工确认。
