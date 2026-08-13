---
name: jira-task-collaboration
description: Use when an approved business task needs Jira inspection, Chinese analysis or plan reporting, managed Description updates, or real-time Worklog recording through AgenticOps.
allowed_modes:
  - project_execution
---

# Jira 任务协作

本 Skill 只在 `project_execution` 使用。先通过 `task-state` 初始化并核对完整任务身份，再调用 Jira 操作。

## 能力顺序

1. 用 `agentic-cli jira inspect` 读取 Connection、Profile 和任务事实。
2. 用 `agentic-cli report write --kind analysis|plan` 保存本地报告。
3. 对 Comment、Description 或 Worklog 先执行 `plan`，检查 `action`、`plan_id` 和内容摘要。
4. 只有研发工程师确认计划，或有效连续执行授权明确覆盖该写入时，才以同一 `plan_id` 和可追溯的 `--authorization-reference` 执行 `apply`。
5. `apply` 会执行回读并更新 `sync.json`。若返回 `jira_write_result_unknown`，只能执行 `readback`，不能直接重试。

## 硬边界

- 所有 Jira 可见内容使用中文。
- Worklog 标题要总结工作，正文要明确耗时包括哪些处理，并显式排除等待时间。
- Custom Field 只使用 Profile 中状态为 `active` 或 `read_only` 的稳定 field ID。
- 字段映射缺失先修复 Profile；涉及 Jira 字段元数据、Context、Screen、权限、自动化或跨项目语义时另开专题。
- 不得使用 AO 专用状态或字段驱动 Tapdata 任务。
- 不得把 token 写入仓库配置、计划文件、输出或报告。
