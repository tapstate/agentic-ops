---
name: tapdata-align-branches
description: 按 TapData 已确认的产品版本或产品线，汇总全部受控仓库的目标分支并只读核验远程存在性；用于版本排查、联调准备和发布前分支事实确认。
metadata:
  product: agenticops
---

# TapData 仓库分支对齐

用于回答“某个 TapData 产品版本（或产品线）在所有受控仓库应使用什么分支，且这些分支目前是否可从远程读取”。这是只读事实汇总，不创建、切换、合并、推送或删除分支。

输入必须明确给出版本键，例如 `current`、已登记的发布版本或发布产品线；也要说明是“全部仓库”还是某个 `domains` 子集。版本键不是当前 Git checkout，也不能由 Jira 文本、分支名称或隐式 `main` 猜出。

## 事实源与解析

运行现役项目脚本，而非手工拼凑分支规则：

```sh
python3 <agenticops-root>/projects/tapdata/scripts/align_branches.py \
  plan <product-branch-or-version-key> --json
```

脚本读取 `version-branch-alignments.json` 与 `repositories.json`，输出全部受控仓库的目标分支、推导原因和远程状态。`current` 使用已确认的静态开发线矩阵；`main`、`develop`、`release-vX.Y.Z` 和任务分支迁移自 v0.7 的项目规则：enterprise/web 同名或显式覆盖，公共库和 connector 从主仓 `origin/<branch>` 的 PluginKit 版本各自推导 release 分支，无法解析即输出 `unresolved`，不猜测。

release 或任务分支需要读取主仓的受控 Source Pool 时，额外传入 `--source-pool <pool-root>`；脚本只读 `origin/<branch>`，不会 fetch 或改动 Source Pool。缺少 Pool 时只有依赖 PluginKit 的仓库为 `unresolved`，其它仓库仍照实输出。新增经确认的发布版本可写入 `versions` 精确矩阵；矩阵必须覆盖全部仓库。

## 只读核验与报告

对每个已解析出的目标分支，脚本使用 catalog 中的 origin 执行：

```sh
git ls-remote --exit-code --heads <origin> refs/heads/<target-branch>
```

逐仓库记录退出结果：`0` 为“远程存在”；`2` 为“远程未见该 ref”；其它退出结果为“未核验”，并保留简短错误摘要（如网络或权限问题）。只有 `0` 才能声称分支已由远程核验；不要把“未核验”写成“缺失”。`keep_current` 与 `independent` 仓库会明确标为不参与产品版本对齐，而不是伪造一个目标分支。

输出一张矩阵，至少包含：版本键、仓库、domains、目标分支、远程状态、核验时间、证据（ref/SHA 或错误类别）。结论需区分：

- 对齐：矩阵完整且所有目标分支远程存在；
- 待处理：某些目标分支远程未见；
- 未完成核验：矩阵完整但存在网络、权限或远程错误；
- 无法解析：版本映射未登记或矩阵不完整。

报告是当前一次读取的证据，不授权 checkout、fetch、branch、merge、push、PR 或发布。若后续任务要修改仓库，仍须通过 TapData 任务 Skill 登记仓库、准备受控 worktree 并取得对应授权。
