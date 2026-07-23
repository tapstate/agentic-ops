# 研发负责人故事

## 1. 范围

本文是 AgenticOps 研发负责人故事索引。研发负责人是在具体业务项目中使用 AgenticOps 管理 AIAgent 执行 Jira 任务的人，负责安装、初始化、配置工作空间、触发任务接管、确认人工门禁并验收任务证据。

详细故事按单文件维护，便于逐条审核、评论和变更追踪。本文只维护索引，不记录实施计划、checkbox、当前完成度或剩余工作。

## 2. 故事索引

| 编号 | 故事 | 审核重点 | 文件 |
| --- | --- | --- | --- |
| DL-001 | 安装 AgenticOps | `~/.agentic-ops` 只作为全局安装目录 | [dl-001-install.md](development-lead/dl-001-install.md) |
| DL-002 | 初始化项目 AI 工作空间 | 工作空间绑定 Jira、仓库、本地目录和 workflow profile | [dl-002-workspace-init.md](development-lead/dl-002-workspace-init.md) |
| DL-003 | 初始化 AIAgent 能力 | AIAgent 知道手册、契约、门禁和停止条件 | [dl-003-agent-init.md](development-lead/dl-003-agent-init.md) |
| DL-004 | 新任务接管 | 接管门禁校验负责人、验收标准、目标仓库和验证方式 | [dl-004-takeover-task.md](development-lead/dl-004-takeover-task.md) |
| DL-005 | 恢复接管任务 | 恢复使用同一 `run_id`，不重新开始或混淆执行记录 | [dl-005-resume-takeover.md](development-lead/dl-005-resume-takeover.md) |
| DL-006 | 任务完成审计与反馈分析 | 提交任务级审计，不把反馈报告当作主链路事实源 | [dl-006-task-audit-feedback.md](development-lead/dl-006-task-audit-feedback.md) |

## 3. 审核方式

审核每个故事文件时，重点检查：

- 保护行为是否足够明确，能防止已确认功能被随意改坏。
- 审核问题是否覆盖角色、事实源、权限、门禁和失败路径风险。
- 验收证据是否能证明故事成立。
- 关联设计是否指向真实存在的设计、规则、契约、配置、模板或运行资产。
