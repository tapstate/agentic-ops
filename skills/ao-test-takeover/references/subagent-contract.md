# 子代理接管验证契约

主会话在启动子代理前提供：

- 测试绑定：Jira 项目、Jira key、测试目录、新工作空间绝对路径、维护面产品根和测试标识；
- 本次范围是“验证接管能力，不实现业务代码”；
- 当前用户已明确授权的副作用、预设门禁回复及其适用条件，以及必须回主会话决定的事项；
- 要求子代理遵守工作空间生成的 `AGENTS.md` 和当前项目 Profile。

子代理先核对 Jira 项目、Jira Key 与 Profile 是否一致；不一致或测试工作空间不在已确认测试目录下时，立即返回 `blocked`，不尝试接管或更换目标。预设门禁回复只在适用条件逐项匹配时使用；每次使用都必须在回传中列出。不能把它解释为实现、外部写入、删除或 Git 操作的授权。

子代理执行时应保留同一任务 run，并在每个真实停止点返回以下内容：

```text
result: needs_human_decision | blocked | completed
run_id: <run id or unavailable>
stage: <workflow stage or unavailable>
summary: <one-paragraph fact summary>
completed_evidence:
  - <Jira/Profile/workspace/worktree evidence>
decision:
  question: <only when needs_human_decision>
  options: <choice, impact, recommendation>
  required_authorization: <exact scope, baseline and verification if applicable>
blocker:
  reason: <only when blocked>
  safe_recovery: <one concrete retry or manual preparation path>
artifacts:
  workspace: <absolute path>
  task_state: <absolute path>
  worktrees: <absolute paths and SHA>
external_writes:
  - <actual Jira transition/comment, or none>
non_actions:
  - <code, commit, push, PR, CI, release actions not performed>
flow_findings:
  - <expected human gate or reproducible flow defect with evidence>
used_preset_gate_replies:
  - <reply id, condition, actual use; or none>
```

`needs_human_decision` 不结束测试：主会话取得用户答复后，必须将答复和当前 run ID 回传同一子代理继续。`blocked` 仅在没有安全替代路径、且已给出最小恢复输入时结束。`completed` 必须说明对应完成证据；单个接管命令、初始化或 worktree 创建不能作为完成结论。

测试完成或阻断后，保留工作空间。只有用户明确要求清理时，才按当前 run ID 进行 worktree cleanup/purge；先检查洁净度，绝不强删未合并分支或未提交修改。

若发现流程缺陷，子代理只回传最小复现和证据，不自行修复维护面代码。主会话必须取得单独的修复授权；修复后的验证使用新的测试工作空间和新的 run，不能复用本次现场作为“已修复”证据。
