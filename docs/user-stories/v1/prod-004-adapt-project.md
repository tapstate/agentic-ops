# PROD-004 低成本适配产品项目

流程负责人通过独立项目 Profile、准入规则和 Runbook 适配 TapData 及后续产品，不修改公共 Gate、Workflow 或 Agent Adapter。

### 验收标准

- 项目 Jira 映射、准入表单和验证规则集中维护；`repositories.json` 唯一维护 `owner/repo`、origin、基线/开发分支和仓库域。
- 机读规则和人读生成视图保持一致。

### 保护行为

- 未登记仓库或未知流程事实不得由 Agent 猜测。
- 用户自备仓库只有在目录布局、origin、Git 根目录和基线分支通过目录合同校验后才能接入。
- 不同产品项目的规则不能混写或渗入公共 Policy。

### 验收证据

- Profile 解析、准入缺项和生成视图一致性测试结果。
- 新项目适配不修改公共内核的代码审查证据。
