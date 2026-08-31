# PROD-003 推进并恢复多仓库研发任务

一个项目工作空间可以同时接管该项目下多个 Jira 任务，每个任务可以绑定多个 Git
仓库；Agent 中断后从各自阶段和 pending 原因恢复，并汇总各仓 PR、CI 与验证证据。

### 验收标准

- 阶段不可跳跃，准入缺项和缺少授权会阻断受影响步骤。
- `.agenticops/tasks/index.json` 统一注册多个任务及 active/inactive/completed
  状态，任务事实、授权、事件和 CI 记录按 issue key 隔离。
- 多个任务可同时 active；Workflow 显式绑定 issue key，Hook 按 Jira 任务号或仓库
  与工作分支唯一解析，歧义时失败关闭。
- 一个授权绑定多个仓库，每仓独立校验 origin、分支、范围和验证方式。
- 多个工作空间共享 `<pool>/<owner>/<repo>` 主工作树；任务修改只落在
  `<workspace>/.agenticops/worktrees/<issue-key>/<run-id>/<owner>/<repo>`，准备前同步远端
  并固化 `base_sha`。
- 同 run 恢复幂等；reset 创建新 run。清理拒绝脏 worktree，并与工作空间 purge 联动。

### 保护行为

- 新增仓库或修改稳定绑定后旧授权失效。
- 主工作树、其它任务 worktree 和整个 Source Pool 不进入当前任务 Agent 的可写目录。
- 本地任务分支默认保留；显式删除只使用安全的 `git branch -d`，残留分支不静默复用。
- PR/CI 结果不改变授权指纹；合并和发布不被任务授权覆盖。

### 验收证据

- 双 active 任务隔离、双仓库门禁放行与未授权仓库拒绝测试结果。
- 阶段、授权、验证、CI 预算和证据敏感内容测试结果。
- 双工作空间共享主工作树、worktree 准备/清理/重做、残留分支和动态 Agent 目录测试结果。
