---
name: tapdata-align-branches
description: 解析 TapData 主仓分支对应的完整多仓分支关系，并安全同步用户 IDEA 开发环境；用于任务提交推送后的本地部署和调试。
metadata:
  product: agenticops
---

# TapData 仓库分支对齐

用于将已经推送的 TapData 任务分支组成用户可部署、可在 IDEA 调试的多仓环境。`--version` 始终是 `tapdata/tapdata` 的分支名；不得由 Jira 文本、当前 checkout 或隐式 `main` 猜测关系。

需要按当前任务验证时，先读取 `task.py repository context --issue-key <issue-key> --json`。对每个任务仓库，比较任务 worktree 的 `HEAD` 和远端工作分支 SHA；远端没有该 SHA 时，报告尚未推送，停止用户环境切换。

## 分析与应用

脚本先对本地仓库 fetch 全部 `origin/*` 分支引用，再在本地 ref 列表解析关系；这避免逐个候选查询远端，并确保 PluginKit 读取的是主仓指定分支。

Source Pool 使用 owner/repo 布局，只能用于分析：

```sh
python3 <agenticops-root>/projects/tapdata/scripts/align_branches.py \
  show --source-pool <pool-root> --version <tapdata-branch> --json
```

用户 IDEA 环境使用平铺仓库布局（`<home>/tapdata`、`<home>/tapdata-web` 等），可以分析或应用：

```sh
python3 <agenticops-root>/projects/tapdata/scripts/align_branches.py \
  show --home <idea-home> --version <tapdata-branch> --json

python3 <agenticops-root>/projects/tapdata/scripts/align_branches.py \
  apply --home <idea-home> --version <tapdata-branch>
```

`apply` 先检查全部仓库干净，再确认全部分支均可解析及存在，最后只允许 checkout 和快进到 `origin/<branch>`。它不 reset、不覆盖本地改动、不在 Source Pool checkout。未参与对齐的仓库会标记 `unchanged`，不会被切换。

## 分支规则

- `main`、`develop`、`release-v*` 使用项目已确认规则。
- 普通分支先在每个参与仓库查找**完全同名**的远端分支；不再按 Jira 号模糊匹配。
- 同名分支不存在时，公共库和 connector 从 tapdata 指定分支读取 PluginKit，选择不低于该版本的首个 release 分支。
- `tapdata-application` 固定使用 `main`；`t-layer3-test` 没有同名分支时使用 `develop`；其它独立仓库保持 `unchanged`。

输出必须区分目标分支、目标 SHA、当前分支、当前 SHA、解析理由与动作。分析或切换开发环境不授权提交、推送、PR、合并或发布。
