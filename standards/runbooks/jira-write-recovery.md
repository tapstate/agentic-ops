# Jira 写入与回读恢复

## 响应明确成功

Runtime 立即回读相同幂等标记或受管章节。只有回读唯一且内容满足计划时，才更新 `sync.json` 并返回完成。

## 响应不明确

1. 停止自动重试 `apply`。
2. 使用同一 `issue_key` 与 `idempotency_key` 执行 `readback`。
3. 回读唯一记录时，将外部 ID 写入 `sync.json`，不再次写入。
4. 回读不存在时，重新执行 `plan`，由研发工程师确认新 `plan_id` 后再 `apply`。
5. 回读发现重复记录时停止，由人工保留唯一事实并记录处理结论。

Description 写入不使用评论式幂等标记。响应不明确时回读所有受管章节；全部一致视为完成，部分一致或重复标题必须人工处理。

不得在日志、报告或错误信息中输出 Jira token、Authorization header 或原始敏感响应。
