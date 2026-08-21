# developer 工作面 Jira 写入与回读恢复

## 响应明确成功

Runtime 立即回读相同幂等标记或受管章节。Comment/Worklog 标记固定绑定 `issue_key + agentic_run_id + idempotency_key`；只有当前运行的精确完整标记唯一、内容满足计划时，才保留 `created=true` 事实、更新 `sync.json` 并返回完成。

## 响应不明确

1. 停止自动重试 `apply`。
2. 使用同一 `issue_key`、`agentic_run_id`、`idempotency_key` 和原计划执行 `readback`。
3. 回读唯一的当前运行精确标记时，将外部 ID 和 `created=true` 事实写入 `sync.json`，不再次写入；旧运行相同 key 或正文不能作为本运行结果。
4. 回读不存在时，重新执行 `plan`，由研发工程师确认新 `plan_id` 后再 `apply`。
5. 回读发现重复记录时停止，由人工保留唯一事实并记录处理结论。

Description 写入不使用评论式幂等标记。响应不明确时回读所有受管章节；全部一致视为完成，部分一致或重复标题必须人工处理。

不得在日志、报告或错误信息中输出 Jira token、Authorization header 或原始敏感响应。
