# 工作流配置

## 1. 目的

Workflow Profile 把 AgenticOps 的通用 operation 映射到具体项目流程。

AgenticOps 核心绑定研发流程语义，不绑定某一套具体 Jira workflow。

## 2. 配置范围

一个 workflow profile 至少应描述：

- 项目 AI 工作空间名称。
- Jira 空间和查询规则。
- Jira 字段映射。
- Jira 状态和 transition 映射。
- GitHub organization 和 repo 映射。
- 本地源码目录。
- 允许的写操作。
- 人工确认点。
- evidence 模板。
- 事件日志位置。

## 3. 概念结构

```yaml
workspace: tapstate

jira:
  project: TAP
  task_query: "assignee = currentUser() AND status in (...)"
  fields:
    owner: assignee
    acceptance_criteria: customfield_acceptance
    target_repo: customfield_target_repo
    risk: customfield_risk

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

templates:
  takeover_success: templates/evidence/takeover-success.md
  takeover_failed: templates/evidence/takeover-failed.md
  blocked: templates/evidence/blocked.md
  development_completed: templates/evidence/development-completed.md
```

## 4. 配置规则

- Profile 可以绑定具体 Jira workflow，但核心 operation 不能依赖某个固定 Jira 状态名。
- Profile 必须能被 `agent-task-ops preflight` 校验。
- Profile 不得包含 secrets、tokens 或 private keys。
- Profile 中的 repo 映射必须能解释任务如何定位目标源码。
- Profile 缺字段时，AIAgent 不能自行猜测，应请求研发 owner 补充。

## 5. 第一批默认配置

第一阶段建议优先设计：

- `tapstate`
- `tapdata`

这两个 profile 可以共享 Operation Contract，但拥有不同 Jira 空间、GitHub 仓库、本地源码和任务执行上下文。

