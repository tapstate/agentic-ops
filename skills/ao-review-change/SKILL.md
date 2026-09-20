---
name: ao-review-change
description: 审查 AgenticOps 源码候选或提交，集中读取故事影响和验收材料，核对设计边界、测试与风险；不用于业务工位的任务验收。
---

# 维护变更审查

在 AgenticOps 源码产品根使用。先读取项目目标与涉及层的现役合同，核对同一 Jira 工作项的范围、授权和当前 Git 候选。已有有效输入不重复索取。

用脚本读取当前材料，避免从旧日志猜测本次验收状态：

```sh
python3 skills/ao-review-change/scripts/review-context.py --change-source staged
python3 skills/ao-review-change/scripts/review-context.py --change-source range --base <base> --head <head>
```

未暂存的开发审查可选 `--change-source worktree`，它不产生正式验收证据。脚本只摘要现有 Story Gate 的结果，保留候选标识、错误、缺失验收、确认事项和风险；非零退出先处理对应问题。需完整报告时直接调用 `internal/bin/story-gate impact` 并传相同参数。摘要不是代码审查通过、提交许可或推送授权。

结合实际 diff 逐项核对规则归属、调用方、失败与恢复路径、配置兼容性和验证覆盖。测试通过不能代替设计审查；发现问题时指出触发条件、影响和可实施修复。候选变化后重新读取材料，旧结论只适用于旧候选。没有新修改、失败或未解决风险时，不重复已经通过的检查。

需要正式验收时，使用[维护指引](../../docs/maintenance-guide.md#5-验证)的现有入口；不另建测试编排或写入门禁证据。计划、评审轮次、结论、阻塞及验收引用更新到当前 Jira 工作项，代码事实由 Git 留存。仅在原授权范围内形成提交；此 Skill 不新增推送、合并、发布、Tag 或历史改写授权。
