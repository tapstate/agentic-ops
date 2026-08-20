---
name: maintain-ao-task
description: Take over, resume, or adopt an AgenticOps AO Jira maintenance task and progress it under the maintainer workplane. Use whenever the user asks to 接管、处理、继续、恢复 or推进 an AO-* work item. Never use Atlassian Connector or direct Jira REST writes.
metadata:
  workplane: maintainer
---

# 接管并推进 AO 维护任务

只在 `maintainer` 工作面使用。开始前完整读取根 `AGENTS.md`、`maintainer/AGENTS.md`、`maintainer/rules/source-maintenance.md`；涉及设计、流程或项目演进时还必须读取 `docs/strategy/project-goals.md`。

用户操作统一为“接管 `<AO-KEY>`”。公开入口只有：

```sh
ao-maint takeover <AO-KEY>
```

不得把内部 Jira plan/apply/readback 子操作暴露为用户需要理解的接管步骤，不得调用 Atlassian Connector、直接 Jira REST API 或 Shell 网络请求写 Jira。

## 消费接管结果

- `mode=new`：Runtime 必须已经完成中文开始评论、流转“正在进行”和逐项回读。
- `mode=resume`：向用户明文说明恢复已有运行；不得重复开始评论或状态流转。
- `mode=adopt`：向用户明文说明接纳存量任务，按输出摘要进入风险决策确认。
- `mode=blocked` 或失败码：停止，不得跨状态或跨所有权继续。

## 设计审查与连续推进

AI 完成源码与 Jira 事实分析后，把当前设计写入 maintainer 本地受管输入文件，并调用：

```sh
ao-maint takeover <AO-KEY> --design-file <FILE>
```

向公司员工指导员展示完整设计与 `design_digest`。确认后调用同一入口：

```sh
ao-maint takeover <AO-KEY> --confirm <DESIGN-DIGEST>
```

Runtime 返回 `work_authorization` 后，在绑定范围内连续完成分析、实现、验证和必要 Jira 进度回写；必要评论、Worklog、状态流转仍由 `ao-maint jira` 执行 plan → apply → readback，并使用该工作项连续授权引用。不得把连续授权用于建卡、整体替换任务描述或任何独立保护操作。

## 固定暂停点

只在以下节点暂停：

1. 设计审查。
2. 提交前确认：精确 staged 内容、故事影响、验证结果、提交信息和推送目标必须一起展示。
3. 风险决策：所有权、范围、仓库、分支、验证或外部事实变化，外部写入结果不明确，或连续失败。

`main`、合并、发布、Git Tag、强推和历史改写始终单独确认。其它正常步骤连续推进。

提交前使用 `$guard-story-quality`，不得把工作项连续授权当作故事内容指纹确认。合并代码后通过 Runtime 写中文结果评论并流转“已完成”；未合并不得提前完成 Jira 任务。
