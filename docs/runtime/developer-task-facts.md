# developer 任务富事实读取

`ao-work task facts --issue-key <KEY>` 在当前 developer 工作空间中只读 Jira Description 与评论，输出设计和准入所需的脱敏结构化事实。

- Description 优先提供任务目标、问题版本、异常摘要和验收线索；未使用标准标题时仅使用受限的概览文本补足任务目标。
- 评论仅提供补充线索，并保留评论 ID、作者、创建时间和来源；它不能替代 Jira 字段、Description 的正式映射或仓库/分支人工确认。
- 仓库/分支始终以 `proposal_only` 输出，仍须经过既有 `task repositories assess` 与确认流程。
- Runtime 不输出或持久化原始 Description、评论正文、令牌、Cookie、认证值、完整 SQL 或原始堆栈日志。

该能力没有 Jira 或业务源码写副作用。读取权限、格式或任务所有权不满足时返回稳定失败码和人工动作。
