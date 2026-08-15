# 共享协议

`shared/` 只保存项目维护工作面与研发工程师工作面共同使用的、版本化的纯 JSON 协议。

- `integration/` 是双方交换任务授权清单、审计事件和脱敏结果包的唯一合同来源。
- 本目录不包含可执行代码，不承载任何 Jira、Git、GitHub 或文件系统副作用。
- 本目录不定义项目维护者或研发工程师的角色规则、入口规则和执行流程；这些内容必须留在各自工作面。
- 两个工作面都必须按这里的 Schema 验证交换产物，不得各自复制、扩展或隐式猜测字段。
- 两个工作面的 Runtime 必须对同一 manifest 保持一致的安全接受边界；长度、唯一性、分支/remote 格式、范围互斥和 Jira 终态限制变更时，必须使用同一变异样本做跨工作面回归。

当前协议：

- `integration/task-to-pr-manifest.schema.json`：用户确认的任务到 PR 审查授权清单。
- `integration/task-to-pr-event.schema.json`：逐步骤审计事件及外部回读事实。
- `integration/task-to-pr-result.schema.json`：`ready_for_pr_review`、`blocked` 或 `failed` 的脱敏结果包。
