---
name: defect-feedback
description: Use when the user reports "反馈 AO 缺陷" or asks to log a defect against the AgenticOps product in Jira; organize the current session into a Chinese defect summary and create an "Agentic 缺陷" issue via ao-work jira create with user confirmation.
metadata:
  workplane: developer
---

# 反馈 AO 缺陷

本 Skill 只在 `developer` 工作面使用。用户表达「反馈 AO 缺陷 / 报一个 AO 的 bug / 记录这个 Agentic 缺陷」时触发。

## 流程

1. 授权检查：用 `ao-work auth jira show` 查看授权状态；未就绪时调用 `configure-authorization` Skill，完成后执行 `auth jira verify`。
2. 会话整理：把当前会话中的缺陷事实整理为中文 summary 与 description，必须覆盖：
   - 缺陷现象（发生了什么、用户看到什么）
   - 复现步骤 / 证据（触发路径、报错、日志位置，脱敏）
   - 影响（谁受影响、影响面）
   - 期望行为（应该怎样）
   - 执行模式：默认「研发模式」（Agentic 缺陷必填字段，经 createmeta 校验）
3. 用户确认：把整理后的 summary 与 description 展示给用户，得到明确确认后才建卡；用户可修改内容后重试。
4. 建卡：走 `ao-work jira create` 的 plan → apply → readback 协议，在 Jira AO 项目创建「Agentic 缺陷」任务：
   ```sh
   ao-work jira create plan --project-key AO --issuetype "Agentic 缺陷" \
     --summary "<中文摘要>" --description-file <desc.md> \
     --field customfield_10353=研发模式 --idempotency-key defect-<短标识> \
     --plan-file .agentic-ops/tasks/AO/runs/<run-id>/jira-plans/defect-create.json
   ```
   - 先 `plan` 检查 `action`、`plan_id`、createmeta 必填字段与授权绑定字段；
   - 研发工程师确认当前计划后，用 plan 输出的 `user-confirmation:<PROJECT-KEY>:<agentic-run-id>:<plan-id>` 执行 `apply`；
   - `apply` 返回真实 issue key 后执行 `readback` 确认。
5. 回显与协作：把建卡成功的 issue key 回显给用户；按 `jira-task-collaboration` Skill 的协作约定处理后续评论与状态。

## 硬边界

- 所有 Jira 可见内容使用中文；摘要、描述、评论不得混入英文流程文本。
- 不绕过 Runtime 直调 Jira REST API；建卡必须走 `ao-work jira create` 的 plan/apply/readback 门禁与授权引用。
- 必填字段不得猜测：缺失字段先看 createmeta 声明，用 `--field` 提供；字段 id 不在 createmeta 中时停止并核对。
- 幂等键每次缺陷唯一（如 `defect-<日期>-<序号>`）；结果不明确时只执行 `readback`，不得重复 apply。
- 外发内容不得来自 `.agentic-ops`、`.git`、隐藏文件、凭证/密钥文件、符号链接、硬链接或特殊文件。
- 不得把 token 写入计划文件、输出或报告。
- description 中涉及日志、报错信息必须脱敏（去除 token、密钥、客户信息）。
