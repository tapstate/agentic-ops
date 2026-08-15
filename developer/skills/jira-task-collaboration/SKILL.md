---
name: jira-task-collaboration
description: Use when an approved business task needs Jira inspection, Chinese analysis or plan reporting, managed Description updates, or real-time Worklog recording through AgenticOps.
metadata:
  workplane: developer
---

# Jira 任务协作

本 Skill 只在 `developer` 工作面使用。先通过 `task-state` 初始化并核对完整任务身份，再调用 Jira 操作。

## 能力顺序

1. 用 `ao-work auth jira show` 查看授权状态；未就绪时调用 `configure-authorization` Skill，完成后执行 `auth jira verify`。
2. 用 `ao-work jira inspect` 读取 Connection、Profile 和任务事实。
3. 用 `ao-work report write --kind analysis|plan` 保存本地报告。
4. 对 Comment、Description 或 Worklog 先执行 `plan`，检查 `action`、`plan_id`、内容摘要和 Runtime 返回的授权绑定字段。
5. 只有研发工程师确认当前计划时，才以同一 `plan_id` 和 plan 输出的 `user-confirmation:<ISSUE-KEY>:<agentic-run-id>:<plan-id>` 执行 `apply`。若确认已记录在 Jira，评论正文必须以独立完整行包含 plan 输出的 `authorization_comment_marker`，再使用 `jira-comment:<ISSUE-KEY>:<正整数评论ID>:<plan-id>`；任意非空字符串、旧运行或旧计划引用都无效。
6. `apply` 会执行回读并更新 `sync.json`。若返回 `jira_write_result_unknown`，只能用同一 `plan_file`、`plan_id`、Issue 和幂等键执行 `readback`，不能直接重试。
7. Comment 与 Worklog 的幂等检查和回读必须遍历 Jira 全部分页；不能把前 100 条未命中当作不存在。任何 HTTP redirect 都视为请求失败，Runtime 不会把 Authorization 转发到第二端点。

## 硬边界

- 所有 Jira 可见内容使用中文。
- Worklog 标题要总结工作，正文要明确耗时包括哪些处理，并显式排除等待时间。
- Custom Field 只使用 Profile 中状态为 `active` 或 `read_only` 的稳定 field ID。
- 字段映射缺失先修复 Profile；涉及 Jira 字段元数据、Context、Screen、权限、自动化或跨项目语义时另开专题。
- 不得使用 AO 专用状态或字段驱动 Tapdata 任务。
- 不得把 token 写入仓库配置、计划文件、输出或报告。
- 外发内容不得来自 `.agentic-ops`、`.git`、隐藏文件、凭证/密钥文件、符号链接、硬链接或特殊文件；计划只允许新建在 `.agentic-ops/tasks/<ISSUE>/runs/<agentic_run_id>/jira-plans/`，不得覆盖。
- Runtime 必须先验证任务、计划和授权引用，随后才能记录本地决策或执行 Jira 写入；授权验证失败时不得留下决策记录。
