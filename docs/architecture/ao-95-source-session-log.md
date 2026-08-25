# AO-95 来源会话日志

## 1. 记录边界

- 来源会话：`codex://threads/01a03728-c948-70b3-b599-21349fbe9ec9`
- 来源任务：`TAPSTATE-87`
- 来源运行：`run-TAPSTATE-87-ae83ef`
- 记录方式：通过 Codex `read_thread` 回读已完成轮次，并与来源工作空间 Runtime journal 中的任务阶段、错误码和运行标识交叉核对。
- 完整性声明：Codex URI 是完整会话入口；下文保留用户消息原文和与 AO-95 直接相关的 Runtime/交互事实，不复制推理内容、凭证、绝对业务路径或大段工具输出，因此是脱敏的可核验会话索引，不冒充底层 JSONL 逐字导出。

## 2. 用户消息日志

按来源会话时间顺序：

1. `请为 ./.agentic-ops/bin/ao-work 申请持久命令前缀授权，覆盖其所有子命令。`
2. `接管 TAPSTATE-87`
3. `确认`
4. `这两个分支改下：tapdata/tapdata-connectors develop；tapdata/tapdata-connectors-enterprise develop`
5. `确认`
6. `下一步`
7. `确认`
8. `为什么会缺？要怎么补`
9. `我不知道要放哪，写什么。`
10. `这些不需要我单独确认`
11. `确认`
12. `AO问题反馈，需要我决策的东西太多，还有在执行过程我看到报错了。`
13. `确认`
14. `按纠正路径重试`
15. `将当前会话完整记录导出到文件，我需要上报问题。`

## 3. Runtime 与交互事实日志

1. `takeover` 成功创建 `run-TAPSTATE-87-ae83ef`，Jira 状态进入实现阶段并回读接管评论。
2. 仓库分析展示 8 个仓库的完整关系；用户把两个 connector 仓库的建议分支从 `main` 修正为 `develop`，随后确认关系表。
3. 只为 `tapdata/tapdata-connectors` 准备任务工作树；准入通过，方案被分为 L1。
4. 用户确认 L1 设计后，Skill 没有可调用的 manifest 生成入口，要求用户提供已确认 manifest 的相对路径。
5. 用户明确“不知道放哪、写什么”后，Skill 仍需自行拼装批准计划和 manifest，并额外询问提交、推送和 PR 授权。
6. 用户表示这些内容不需要单独确认后，首次 `task-run open` 在启动任何验证子进程前返回 `verification_command_forbidden`：Maven argv 缺少 batch/offline 参数。
7. Skill 把内部 Maven 安全参数变化翻译成新的用户确认；用户确认后重建执行包。
8. 第二次 `task-run open` 返回 `manifest_digest_mismatch`：调用层使用通用 JSON 摘要，未复用 Runtime 的 `manifest_digest(...)`。
9. Skill 再次把内部摘要不一致翻译成“重新审阅完整 manifest”的用户确认，业务代码仍未开始修改。
10. 用户触发 `AO问题反馈` 后，Skill 先整理报告并请求一次内容确认；确认后又声明拿到 plan id 还要再确认 Jira 写入。
11. `jira create plan` 示例手工使用 `.agentic-ops/tasks/AO/runs/<run-id>/...`，但没有传 `--run-id`；CLI 内部生成了另一个随机 run，返回 `jira_plan_path_not_bound`。
12. 错误提示要求改为当前业务任务 run 的路径；用户选择“按纠正路径重试”后仍返回同一错误，AO 缺陷卡没有创建。
13. 来源会话最后在业务工作空间生成了脱敏上报记录，但没有完成 Jira `plan -> apply -> readback` 闭环。

## 4. 归纳结论

门禁数量增加来自三段独立能力没有共享同一授权事实：仓库范围确认、L1 设计审查和 task-run manifest 授权各自停顿；其中前两项是业务语义决策，第三项的路径、Maven 参数和摘要本应由 Runtime 生成。调用层未区分语义变化与内部载体错误，又把每个 fail-closed 结果转成用户确认，形成无效交互。

AO 反馈链另有重复授权和参数合同缺陷：完整报告确认没有覆盖同内容的 plan/apply，且随机 run 与调用者手拼路径无法一致。AO-95 因此需要同时修复 task-run 执行包生命周期、反馈建卡路径和 Skill 的事实/日志优先表达。
