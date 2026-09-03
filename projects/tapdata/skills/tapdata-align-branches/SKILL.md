---
name: tapdata-align-branches
description: 只读解析 TapData Source Pool 中某个 tapdata 主仓分支的完整多仓分支关系与提交 SHA。
metadata:
  product: agenticops
---

# TapData 仓库分支对齐

用于确认 `tapdata/tapdata` 某个分支在完整 TapData Source Pool 中对应的各仓库目标分支和提交 SHA。`--version` 始终是 `tapdata/tapdata` 的分支名；不得从 Jira 文本、当前 checkout 或隐式 `main` 猜测关系。

这是只读分析能力：脚本会对 Source Pool 中各仓库执行 `git fetch --prune origin +refs/heads/*:refs/remotes/origin/*`，然后只从本地 `origin/*` 引用解析。它不会 checkout、切换、合并、提交、推送或修改用户的 IDEA 开发环境。任务工作树和用户部署调试环境由各自独立流程处理，不属于本技能。

## 使用方式

显式指定 Source Pool 时：

```sh
python3 <agenticops-root>/projects/tapdata/scripts/align_branches.py \
  show --source-pool <source-pool-root> --version <tapdata-branch> --json
```

省略 `--source-pool` 时，脚本按以下顺序解析根目录：

1. 从当前执行路径向上找到最近的 `.agenticops/workspace.json`，使用其中的 `repository_pool.root`。
2. 未找到工作空间绑定时，使用当前执行目录。

因此 `$tapdata-align-branches release-v4.21.0` 必须被执行为上述 `show --version release-v4.21.0`，不能映射为 `--home $HOME`。如果最终目录不含 `tapdata/tapdata`，脚本应立即报错停止；不得先把它当作 IDEA 平铺多仓目录，也不得扫描用户主目录。

Source Pool 必须采用 `<pool>/tapdata/<repository>` 布局，且每个纳入目录的仓库都必须存在。输出包含目标分支、目标 SHA、推导理由和目标状态；`unchanged` 表示不参与关系推导，而非对本地工作树采取操作。

## 分支规则

- `current` 等已确认版本矩阵直接显示矩阵配置，并以本地 `origin/*` ref 核验 SHA。
- `main`、`develop`、`release-v*` 使用项目已确认规则。
- 普通分支仅在适用仓库中做完全同名匹配，绝不按 Jira 号模糊猜测。
- 公共库和 connector 沿用 PluginKit 策略：从 `tapdata` 指定分支读取 PluginKit，选择不低于该版本的首个 release 分支。
- `tapdata-application` 显示为 `main`；`t-layer3-test` 无同名分支时显示为 `develop`；其它独立仓库显示为 `unchanged`。

分析结果是分支事实，不授权提交、推送、PR、合并、发布或用户环境切换。
