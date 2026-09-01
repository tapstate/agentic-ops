# 自定义 Source Pool

Source Pool 是业务仓库主工作树的统一根目录，目录结构为 `<pool>/<owner>/<repo>`。任务实际修改的是工作空间 `.agenticops/worktrees/` 下的 linked worktree，而不是 Pool 中的主工作树。

默认安装已经设置 `~/.agentic-ops-repos` 和 `auto-clone`。接管任务时，Workflow 只会按项目仓库目录下载缺失仓库，并在任务 worktree 中工作；仍须确保 Git SSH 权限和项目仓库映射正确。只有要复用已有业务仓库、隔离缓存，或禁止自动下载时，才需要覆盖默认值。

安装时可在[gh 一键安装](gh-one-click-install.md)的最后一行改为：

```sh
printf '%s\n' "$bootstrap" | bash -s -- \
  --repository-pool <Source-Pool-目录>
```

需要禁止自动下载、要求预先准备干净主工作树时，再增加：

```sh
--repository-provisioning manual
```

`auto-clone` 会在准备任务时按项目目录下载仓库；`manual` 要求你预先按 `<owner>/<repo>` 布局放入仓库，并保持主工作树在基线分支且洁净。Pool 必须可读、可写、可进入，且不能位于 Product Root 或项目工作空间内，也不能与它们互相嵌套。

工作空间首次初始化时会继承安装配置。仅当该工作空间必须独立使用另一个 Pool 时，在首次初始化增加 `--repository-pool <目录>`。该绑定会写入 `.agenticops/workspace.json`；之后改安装默认值不会静默重绑，不能手改该文件。迁移前先清理任务 worktree，再重新初始化或等待受控迁移能力。
