# 更新与回退

以下命令在安装目录的使用工作面执行，不需要进入业务工作空间：

```sh
~/.agentic-ops/agenticops update
~/.agentic-ops/agenticops rollback
```

`update` 仅 fast-forward 到安装时记录的分支；`rollback` 回到最近一次更新前的提交。安装目录有本地修改、HEAD 偏离安装记录或远端历史异常时，命令会停止，不会覆盖现场。

产品使用单调递增的 `workspace_state_epoch` 管理工作空间持久化兼容性。`update` 和 `rollback` 在切换产品提交前读取目标兼容性清单；当前与目标 epoch 相同，或目标明确支持当前 epoch 时，不要求结束任务。若目标不支持当前 epoch，命令会检查全部已登记工作空间并失败关闭，列出 workspace、issue、status、run 和旧状态残留。

遇到不兼容提示时，留在当前版本依次完成或停用任务、清理 linked worktree，并显式 purge 本地任务状态；脏 worktree、未合并分支、不可访问工作空间或无法识别的旧状态必须人工处理。需要保留的历史材料先移出 `.agenticops/` 归档，再重新执行 `update`。这些操作只处理本地状态，不修改 Jira。任务仅进入 `completed` 并不代表已经清理。

升级协议必须先通过兼容的过渡版本进入安装：过渡版本提升 Updater 能力但保持目标最低协议要求不变，后续版本才提升 `minimum_updater_protocol_version` 和 epoch。若命令提示目标需要更新的升级协议，先安装官方指定的过渡版本；早于该协议的安装不能直接跨越不兼容版本。空工作空间跨 epoch 更新后，在 `repair` 或 `start` 写入目标代际前，任务状态写入口会失败关闭。

工作空间无法启动或 `doctor` 报接线漂移时，使用已绑定的产品根目录修复：

```sh
~/.agentic-ops/agenticops doctor --workspace <项目工作空间>
~/.agentic-ops/agenticops repair --workspace <项目工作空间>
```

`doctor` 只读检查；`repair` 只重建可再生接线。跨 epoch 时只有已经清空任务和旧状态残留的工作空间才能采用新代际，不执行在线数据迁移。更新后，已启动的 Agent 需重启。

不要用 `rollback` 管理 AgenticOps 源码仓库；源码维护使用 Git 流程，见[维护指引](../maintenance-guide.md)。
