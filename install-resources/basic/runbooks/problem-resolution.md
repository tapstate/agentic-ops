# 问题处理 Runbook

## 处理顺序

1. 识别 `task_type`、`current_stage`、`next_action`。
2. 查询 操作契约、工作流配置、policy 和 template。
3. 能按标准资产安全处理时，自助处理并记录 evidence。
4. 缺少 Jira 关键信息时，阻断接管并输出补全动作。
5. 标准资产不适配时，生成改进建议。
6. 存在风险、权限不足、标准冲突或连续失败时，转人工。
7. 只有确认问题来自 `agentic-cli` CLI 二进制逻辑错误时，进入二进制修复发布路径。
