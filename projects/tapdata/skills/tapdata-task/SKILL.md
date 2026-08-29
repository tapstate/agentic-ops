---
name: tapdata-task
description: 以受控流程执行 TapData Jira 研发任务，覆盖接管、准入、设计确认、多仓库实现、PR、CI 和证据回写。
metadata:
  product: agenticops
---

# TapData 受控任务流程

设项目工作空间为 `<project-workspace>`，任务号为 `<issue-key>`，中央产品根为
工作空间 `AGENTS.md` 声明的 `<agenticops-root>`。工具目录为
`<agenticops-root>/workflow`。多个任务 active 时，所有任务命令必须带
`--issue-key <issue-key> --dir <project-workspace>`；不要在各仓库内创建独立状态。

## 开始或恢复

1. 运行 `python3 <agenticops-root>/workflow/task.py list --dir <project-workspace>`。
2. 已注册任务用 `status --issue-key <issue-key>` 从当前阶段恢复，不重复已完成步骤。
3. 新任务先读取 Jira 事实，再执行 `task.py init --issue-key TAP-xxx --task-class
   <defect_fix|feature_change|technical_task> --dir <project-workspace>`；初始化不会停用
   其它 active 任务。
4. 核对负责人和状态映射后进入 `task_intake`。

## 准入、设计和多仓库

- 用 `task.py checklist` 获取机读准入要求，不得凭聊天猜测。
- 缺项时一次列全、生成中文补卡评论并 `task.py block`。
- 每个目标仓库登记仓库、工作分支、基线分支、范围和验证方式。
- 研发工程师确认方案后用 `workflow/authorization.py grant` 签发任务授权。
- 新增仓库或修改分支、范围、验证方式后必须重新确认和授权。

## 实现、PR、CI 和完成

- 每个仓库分别验证并记录提交、PR 和 CI，任务级证据统一汇总。
- 合并、发布、Tag、rebase、强推和保护分支写入不被任务授权覆盖。
- 用 `workflow/evidence.py --issue-key <issue-key> --dir <project-workspace>` 汇总结果，
  确认后再回写 Jira。
- 未迁移能力优先使用 Agent 原生能力；没有安全路径时只暂停当前副作用步骤。
