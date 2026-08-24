---
name: ao-issue-feedback
description: Use when the user invokes "AO问题反馈" to report a problem with the AgenticOps product; produce a complete sanitized repair report, show it for confirmation, and create an "Agentic 缺陷" issue via ao-work jira create.
metadata:
  workplane: developer
---

# AO问题反馈

本 Skill 只在 `developer` 工作面使用。用户使用「AO问题反馈」说明 AgenticOps 产品问题时触发。

## 流程

1. 授权检查：用 `ao-work auth --show` 查看安装授权状态；未就绪时调用 `configure-authorization` Skill。真实 Jira 身份与项目访问由后续 workspace/task Runtime 入口校验。
2. 事实收集：优先使用当前会话、任务状态和 Runtime 已明确输出的结构化事实；不得猜测或为补齐反馈扫描隐藏状态、凭证和原始敏感日志。
3. 会话整理：按 `ao_problem_feedback/v1` 形成中文 summary 与完整 description，逐节覆盖：
   - 来源绑定；
   - 版本与环境；
   - 问题上下文、实际行为、期望行为；
   - 最小复现、影响范围、情境化外部事实；
   - 证据清单、人工介入、初步判断与候选修复载体；
   - 最小回归、验收标准、缺失事实与补齐动作、脱敏声明；
   - 执行模式：默认「研发模式」（Agentic 缺陷必填字段，经 createmeta 校验）。
   每项必须标记为已提供、不适用或未获取；未获取项同时写明原因、事实源、补齐动作和是否阻断修复，不得静默省略。
4. 就绪判断：来源与版本、实际与期望行为、影响、最小复现或等价证据、最小回归、验收标准和脱敏声明全部满足时标记 `repair_readiness: ready`；否则标记 `needs_information`，在描述开头说明不能独立修复并列出最小补齐动作。
5. 用户确认：把整理后的完整 summary、description、`repair_readiness` 和缺失事实展示给用户，得到明确确认后才建卡；用户可修改内容后重试。
6. 建卡：走 `ao-work jira create` 的 plan → apply → readback 协议，在 Jira AO 项目创建「Agentic 缺陷」任务：
   ```sh
   ao-work jira create plan --project-key AO --issuetype "Agentic 缺陷" \
     --summary "<中文摘要>" --description-file <desc.md> \
     --field customfield_10353=研发模式 --idempotency-key defect-<短标识> \
     --plan-file .agentic-ops/tasks/AO/runs/<run-id>/jira-plans/defect-create.json
   ```
   - 先 `plan` 检查 `action`、`plan_id`、createmeta 必填字段与授权绑定字段；
   - 研发工程师确认当前计划后，用 plan 输出的 `user-confirmation:<PROJECT-KEY>:<agentic-run-id>:<plan-id>` 执行 `apply`；
   - `apply` 返回真实 issue key 后执行 `readback` 确认。
7. 回显与协作：把建卡成功的 issue key 回显给用户；按 `jira-task-collaboration` Skill 的协作约定处理后续评论与状态。

## 硬边界

- 所有 Jira 可见内容使用中文；摘要、描述、评论不得混入英文流程文本。
- 不绕过 Runtime 直调 Jira REST API；建卡必须走 `ao-work jira create` 的 plan/apply/readback 门禁与授权引用。
- 必填字段不得猜测：缺失字段先看 createmeta 声明，用 `--field` 提供；字段 id 不在 createmeta 中时停止并核对。
- 幂等键每次缺陷唯一（如 `defect-<日期>-<序号>`）；结果不明确时只执行 `readback`，不得重复 apply。
- 外发内容不得来自 `.agentic-ops`、`.git`、隐藏文件、凭证/密钥文件、符号链接、硬链接或特殊文件。
- 不得把 token 写入计划文件、输出或报告。
- description 中涉及日志、报错信息必须脱敏（去除 token、密钥、客户信息）。
- 本 Skill 只负责上报，不得在业务项目工作空间、`~/.agentic-ops` 或 developer 工作面会话中修复 AgenticOps 源头；developer 资源只能由独立源头仓库中的 maintainer 工作面维护。
- `feedback_bundle` 未实现时只能从用户确认的安全事实人工整理，不得模拟命令成功或声称已生成反馈包。
