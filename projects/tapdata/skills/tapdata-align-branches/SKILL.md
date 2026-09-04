---
name: tapdata-align-branches
description: 不改工作树地解析 TapData 模块根目录中某个 tapdata 主仓分支的多仓分支关系、覆盖状态与提交 SHA。
metadata:
  product: agenticops
---

# TapData 仓库分支对齐

用于确认 `tapdata/tapdata` 某个分支对应的各仓库目标分支、提交 SHA 与覆盖状态。`--version` 始终是 `tapdata/tapdata` 的分支名；不得从 Jira 文本、当前 checkout 或隐式 `main` 猜测关系。

这是不改工作树的分析能力。默认通过通用 `workflow/git_refs.py snapshot` 读取单仓库 GitHub refs 缓存；首次加载、缓存超过阈值或显式刷新时才顺序刷新当前需要的仓库。它不会 checkout、切换、合并、提交、推送或改动工作树文件。任务工作树和用户部署调试环境由各自独立流程处理，不属于本技能。

若外部调用方只需快速分析，应使用带缓存的 `snapshot`；若操作前必须取得当前远端的精确 head 事实，应使用无缓存的 `probe`：

```sh
# 带缓存：缓存属于工作空间，不写入 Product Root 或 Source Pool
python3 <agenticops-root>/workflow/git_refs.py snapshot \
  --repository <git-root> --scope heads \
  --repository-id <owner>/<repo> \
  --cache-file <workspace>/.agenticops/git-ref-cache-v1.json

# 无缓存：直接查询当前远端，不读写缓存
python3 <agenticops-root>/workflow/git_refs.py probe \
  --origin <git-url> --head <branch>
```

## 使用方式

显式指定 TapData 模块根目录时：

```sh
python3 <agenticops-root>/projects/tapdata/scripts/align_branches.py \
  show --tapdata-root <tapdata-root> --version <tapdata-branch> \
  --repository <task-repository> --json
```

`--tapdata-root` 必须直接包含主仓 `<tapdata-root>/tapdata`；它不是通用 Source Pool，也不是 `tapdata/tapdata` 主仓目录。默认缓存写入当前工作空间的 `<workspace>/.agenticops/git-ref-cache-v1.json`，并以 `<owner>/<repo> + canonical origin + scope` 映射；不写入 Product Root 或 Source Pool。脱离工作空间运行时，必须显式传入 `--cache-file`。目录中其它已登记仓库可尚未接入。`--repository` 可重复，表示本次必须核验的任务目标仓库；主仓始终必需。省略它时输出完整目录诊断，但不把全部仓库变成前置条件。

省略 `--tapdata-root` 时，脚本按以下顺序解析 TapData 模块根目录：

1. 从当前执行路径向上找到最近的 `.agenticops/workspace.json`，使用其中的 `repository_pool.root/tapdata`。
2. 未找到工作空间绑定时，使用当前执行目录。

因此 `$tapdata-align-branches release-v4.21.0` 必须被执行为上述 `show --version release-v4.21.0`，不能映射为 `--home $HOME`。如果最终目录不含主仓 `<tapdata-root>/tapdata`，脚本应立即报错停止；不得先把它当作 IDEA 平铺多仓目录，也不得扫描用户主目录。

工作空间的全局 Source Pool 仍采用 `<pool>/tapdata/<repository>` 布局；本工具将其解析为 `<pool>/tapdata` 后再处理。若主仓缺失，脚本在任何远端刷新前停止；若显式指定仓库缺失，结果为 `blocked`。未指定的缺失仓库以 `not_covered` 报告，不阻断其它仓库。输出包含本地状态、目标分支、目标 SHA、推导理由和目标状态；`unchanged` 表示不参与关系推导，而非对本地工作树采取操作。

## 刷新策略与进度

- `--refresh`：顺序强制查询本次推导依赖到的仓库，并更新对应单仓缓存。
- 省略 `--refresh`（默认）：优先读取该仓缓存；首次加载或超过项目阈值时刷新。

JSON 顶层的 `outcome` 为 `complete`、`partial` 或 `blocked`，`scope` 说明本次严格范围，`blockers` 说明不能继续的必需事实。每行的 `target_status` 区分 `verified_exists`、`verified_missing`、`cached_exists`、`absence_unverified`、`not_covered` 与 `unresolved`；远端刷新失败绝不能输出 `verified_missing`。`rows[].refs.error_kind` 会区分 `repository_access_denied`、`ssh_auth_failed`、`network_unreachable`、`fetch_timeout` 与一般刷新失败，`prompt` 给出不含凭证的处理提示；普通表格也显示这两项。`timing_seconds` 分别记录 `fetch`、`local_resolution` 与 `total` 耗时。脚本会在 stderr 实时报告本地检查、每个仓库的刷新/复用和完成耗时；单个 `fetch` 仍在进行时每 10 秒报告一次心跳。

## 分支规则

- `current` 等已确认版本矩阵直接显示矩阵配置，并以缓存快照的 SHA 核验；缓存结果不是操作前的最终基线。
- `main`、`develop`、严格格式的 `release-vX.Y.Z` 使用项目已确认规则；带 `release-v` 前缀但格式不合法的名称直接拒绝。
- 功能分支先在配置列出的仓库找完全同名分支，绝不按 Jira 号模糊猜测。
- `common-lib`、`connectors`、`connectors-enterprise` 找不到同名分支时，从主仓该功能分支的 PluginKit 读取**精确** `release-vX.Y.Z`；目标分支不存在即 `unresolved`，绝不自动升到更高 release，也不再走 Tag 回退。
- 其余配置为 Tag 回退的仓库，使用主仓功能分支 first-parent 最近的产品 Tag：`X.Y.Z-dev` 推导 `develop`，`X.Y.Z` 推导 `release-vX.Y.Z`；Tag 图必须已由受控 Source Pool 刷新流程同步，本工具不会自行 fetch 或修改 Source Pool。缺少本地 Tag 图或目标分支不存在即 `unresolved`。
- 文档仓库被显式标记为 `unchanged`；不参与功能分支推导。

分析结果是分支事实，不授权提交、推送、PR、合并、发布或用户环境切换。
