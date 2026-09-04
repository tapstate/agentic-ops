---
name: tapdata-align-branches
description: 不改工作树地解析 TapData 模块根目录中某个 tapdata 主仓分支的多仓分支关系、覆盖状态与提交 SHA。
metadata:
  product: agenticops
---

# TapData 仓库分支对齐

用于确认 `tapdata/tapdata` 某个分支对应的各仓库目标分支、提交 SHA 与覆盖状态。`--version` 始终是 `tapdata/tapdata` 的分支名；不得从 Jira 文本、当前 checkout 或隐式 `main` 猜测关系。

这是不改工作树的分析能力：脚本会对 TapData 模块根目录中各仓库执行 `git fetch --prune origin +refs/heads/*:refs/remotes/origin/*`，再从本地 `origin/*` 引用解析。`fetch` 会更新 Git 远端跟踪引用，但不会 checkout、切换、合并、提交、推送或改动工作树文件。任务工作树和用户部署调试环境由各自独立流程处理，不属于本技能。

## 使用方式

显式指定 TapData 模块根目录时：

```sh
python3 <agenticops-root>/projects/tapdata/scripts/align_branches.py \
  show --tapdata-root <tapdata-root> --refresh never --version <tapdata-branch> \
  --repository <task-repository> --json
```

`--tapdata-root` 必须直接包含主仓 `<tapdata-root>/tapdata`；它不是通用 Source Pool，也不是 `tapdata/tapdata` 主仓目录。目录中其它已登记仓库可尚未接入。`--repository` 可重复，表示本次必须核验的任务目标仓库；主仓始终必需。省略它时输出完整目录诊断，但不把全部仓库变成前置条件。

省略 `--tapdata-root` 时，脚本按以下顺序解析 TapData 模块根目录：

1. 从当前执行路径向上找到最近的 `.agenticops/workspace.json`，使用其中的 `repository_pool.root/tapdata`。
2. 未找到工作空间绑定时，使用当前执行目录。

因此 `$tapdata-align-branches release-v4.21.0` 必须被执行为上述 `show --version release-v4.21.0`，不能映射为 `--home $HOME`。如果最终目录不含主仓 `<tapdata-root>/tapdata`，脚本应立即报错停止；不得先把它当作 IDEA 平铺多仓目录，也不得扫描用户主目录。

工作空间的全局 Source Pool 仍采用 `<pool>/tapdata/<repository>` 布局；本工具将其解析为 `<pool>/tapdata` 后再处理。若主仓缺失，脚本在任何远端刷新前停止；若显式指定仓库缺失，结果为 `blocked`。未指定的缺失仓库以 `not_covered` 报告，不阻断其它仓库。输出包含本地状态、目标分支、目标 SHA、推导理由和目标状态；`unchanged` 表示不参与关系推导，而非对本地工作树采取操作。

## 刷新策略与进度

- `--refresh always`：对每个本地可用仓库执行 `git fetch`，结果中的 refs 标记为本次已刷新。
- `--refresh auto`（默认）：本地可用仓库没有 `FETCH_HEAD`，或其本地修改时间超过 5 分钟时执行 `fetch`。
- `--refresh never`：绝不访问网络，只解析现有 `origin/*`；通常很快完成，但结果必须视为缓存。

JSON 顶层的 `outcome` 为 `complete`、`partial` 或 `blocked`，`scope` 说明本次严格范围，`blockers` 说明不能继续的必需事实。每行的 `target_status` 区分 `verified_exists`、`verified_missing`、`cached_exists`、`absence_unverified`、`not_covered` 与 `unresolved`；远端刷新失败绝不能输出 `verified_missing`。`rows[].refs.error_kind` 会区分 `repository_access_denied`、`ssh_auth_failed`、`network_unreachable`、`fetch_timeout` 与一般刷新失败，`prompt` 给出不含凭证的处理提示；普通表格也显示这两项。`timing_seconds` 分别记录 `fetch`、`local_resolution` 与 `total` 耗时。脚本会在 stderr 实时报告本地检查、每个仓库的刷新/复用和完成耗时；单个 `fetch` 仍在进行时每 10 秒报告一次心跳。

## 分支规则

- `current` 等已确认版本矩阵直接显示矩阵配置，并以本地 `origin/*` ref 核验 SHA。
- `main`、`develop`、`release-v*` 使用项目已确认规则。
- 普通分支仅在适用仓库中做完全同名匹配，绝不按 Jira 号模糊猜测。
- 公共库和 connector 沿用 PluginKit 策略：从 `tapdata` 指定分支读取 PluginKit，选择不低于该版本的首个 release 分支。
- `tapdata-application` 显示为 `main`；`hazelcast` 固定参与并显示为 `release-v5.5.0`；`t-layer3-test` 无同名分支时显示为 `develop`；其它独立仓库显示为 `unchanged`。

分析结果是分支事实，不授权提交、推送、PR、合并、发布或用户环境切换。
