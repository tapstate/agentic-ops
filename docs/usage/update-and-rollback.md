# 更新与回退

以下命令在安装目录的使用工作面执行，不需要进入业务工作空间：

```sh
~/.agentic-ops/agenticops update
~/.agentic-ops/agenticops rollback
```

`update` 仅 fast-forward 到安装时记录的分支；`rollback` 回到最近一次更新前的提交。安装目录有本地修改、HEAD 偏离安装记录或远端历史异常时，命令会停止，不会覆盖现场。

工作空间无法启动或 `doctor` 报接线漂移时，使用已绑定的产品根目录修复：

```sh
~/.agentic-ops/agenticops doctor --workspace <项目工作空间>
~/.agentic-ops/agenticops repair --workspace <项目工作空间>
```

`doctor` 只读检查；`repair` 只重建可再生接线并迁移旧工作空间状态，不改变任务语义。更新后，已启动的 Agent 需重启。

不要用 `rollback` 管理 AgenticOps 源码仓库；源码维护使用 Git 流程，见[维护指引](../maintenance-guide.md)。
