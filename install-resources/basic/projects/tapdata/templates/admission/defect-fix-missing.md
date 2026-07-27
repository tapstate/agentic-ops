# TapData 缺陷修复准入处理

Jira 卡片不满足缺陷准入要求时，AIAgent 不得调用 `takeover-task`，也不得只把缺失字段转述给研发工程师。

## 必须完成

1. 一次性列出问题分支、修复分支、问题现象、复现路径和验收标准中的全部缺失或冲突项。
2. 结合 Jira 事实、候选仓库和目标分支代码进行初步分析，说明依据和不确定性。
3. 使用 `templates/defect/admission-analysis-comment.md` 形成“准入分析与补卡建议”。
4. 研发工程师确认真实写入后，使用 `add-task-comment --category analysis` 写入 Jira。
5. 写入成功后结束本次接管，不得继续绑定任务。

## 补卡确认

研发工程师确认补卡内容后：

1. 使用 `templates/defect/description-sections.yaml` 形成确认后的 Description 章节。
2. 使用 `update-task-description-sections` 更新 Jira 稳定任务契约。
3. 使用 `templates/defect/admission-decision-comment.md` 和 `add-task-comment --category decision` 记录确认结果。
4. 结束本次接管。

下一次执行必须重新运行 `inspect-task`，以 Jira 当前事实重新判断准入。AIAgent 给出的候选分支、代码定位和验收建议都必须由研发工程师确认，不得自行升级为任务事实。
