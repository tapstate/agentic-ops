# 缺陷补卡确认结果

<!-- workplane: developer -->

- Jira 卡片：`<issue_key>`
- 决策：`<approved|rejected|changes_requested>`
- 决策人：`<development_engineer>`
- 决策时间：`<decided_at>`
- 关联分析评论：`<analysis_comment_reference>`

## 已确认内容

<confirmed_description_sections>

## 调整或保留意见

<decision_notes>

Description 更新完成后结束本次接管。下一次执行必须重新运行 `ao-work jira inspect --issue-key <issue_key>`，并补齐读取 Jira Description、Comment 与 Custom Field 当前事实，不得直接沿用本次准入判断。
