---
name: tapdata-align-branches
description: 解析 TapData 模块根目录中某个 tapdata 主仓分支的多仓分支关系、覆盖状态与提交 SHA，并按已确认计划安全同步用户开发环境。
metadata:
  product: agenticops
---

# TapData 仓库分支对齐

用于确认 `tapdata/tapdata` 某个分支对应的各仓库目标分支、提交 SHA 与覆盖状态。`--version` 始终是 `tapdata/tapdata` 的分支名；不得从 Jira 文本、当前 checkout 或隐式 `main` 猜测关系。

`show` 是不改工作树的分析能力。默认通过通用 `workflow/git_refs.py snapshot` 读取单仓库 GitHub refs 缓存；首次加载、缓存超过阈值或显式刷新时才顺序刷新当前需要的仓库。它不会 checkout、切换、合并、提交、推送或改动工作树文件。需要 PluginKit 时，脚本按已核验的主仓 SHA 优先读取已有本地对象；对象不存在时只在临时 Git 对象库中获取并核对远端分支，不写入 TapData 模块仓库。

`apply` 仅用于把明确指定的独立开发目录同步到已确认的 `show` 计划。目标目录位于已绑定工位中时，无论工位空闲或占用均禁止 apply；判断依据是目标目录的绑定，不是调用会话或当前执行目录。工位工程由 task.py takeover 准备，show 只提供分析，不替代接管，也不通过 purge 绕过限制。

若外部调用方只需快速分析，应使用带缓存的 `snapshot`；若操作前必须取得当前远端的精确 head 事实，应使用无缓存的 `probe`：

```sh
# 带缓存：缓存属于工位，不写入 Product Root 或其它工位
python3 <agenticops-root>/workflow/git_refs.py snapshot \
  --repository <git-root> --scope heads \
  --repository-id <owner>/<repo> \
  --cache-file <station>/.agenticops/git-ref-cache-v2.json \
  --cache-root <tapdata-root> --max-age 3600

# 无缓存：直接查询当前远端，不读写缓存
python3 <agenticops-root>/workflow/git_refs.py probe \
  --origin <git-url> --head <branch>
```

## 使用方式

在已绑定工位中分析工程时，使用 `show`，显式指定工位模块根目录和本工位缓存：

```sh
python3 <agenticops-root>/projects/tapdata/scripts/align_branches.py \
  show --tapdata-root <station>/source/tapdata --version <tapdata-branch> \
  --repository <owner/repo> \
  --cache-file <station>/.agenticops/git-ref-cache-v2.json --json
```

在独立开发目录中，先运行下列 `show`，核对其 `plan_digest`、`apply.ready`、`apply.blockers`、各仓库当前分支/SHA、目标分支/SHA 和预期动作，再按已确认计划执行 `apply`：

```sh
python3 <agenticops-root>/projects/tapdata/scripts/align_branches.py \
  show --tapdata-root <independent-module-root> --version <tapdata-branch> \
  --repository <owner/repo> --cache-file <absolute-cache-file> --json

python3 <agenticops-root>/projects/tapdata/scripts/align_branches.py \
  apply --tapdata-root <independent-module-root> --version <tapdata-branch> \
  --repository <owner/repo> --cache-file <absolute-cache-file> \
  --expected-plan-digest <confirmed-show-plan-digest> --json
```

两条命令中的占位符替换为相同实际值：同一个绝对模块根目录、同一主仓分支、完全一致的全部 `--repository` 参数及同一个显式 `--cache-file`。多仓时两条命令都逐项重复 `--repository`。缓存使用调用者授权可写的位置，不放入 Product Root 或其它工位；缓存路径是执行输入，不是计划摘要绑定字段。`show` 可能联网并更新缓存，不是严格离线或完全无写入的操作。

`apply` 必须显式提供 `--tapdata-root`，不允许从工位绑定、当前目录或用户主目录猜测写入目标。计划摘要同时绑定规范化模块根目录、仓库路径、处理范围、当前状态和目标状态，不能跨另一套目录或范围复用。它始终重新核验远端 refs；刷新后摘要变化时，重新运行同范围 `show`、展示差异并核对原确认是否仍适用，范围或风险变化时补充确认；不得直接抄取错误中的新摘要重试。指定 `--repository` 时只应用主仓和明确列出的仓库；未指定时应用本地已接入且参与分支关系的仓库。

`--tapdata-root` 必须直接包含主仓 `<tapdata-root>/tapdata`；它不是产品根，也不是 `tapdata/tapdata` 主仓目录。工位分析须显式将缓存指定为本工位的 `<station>/.agenticops/git-ref-cache-v2.json`，以规范化绝对 `<tapdata-root>` 分区，再按 `<owner>/<repo> + canonical origin + scope` 映射；不写入 Product Root 或其它工位。旧 `git-ref-cache-v1.json` 由用户自行处理，现役路径不读取、迁移或删除它。脱离工位运行时，必须显式传入 `--cache-file`。目录中其它已登记仓库可尚未接入。`--repository` 可重复，表示本次必须核验的任务目标仓库；主仓始终必需。省略它时输出完整目录诊断，但不把全部仓库变成前置条件。

当前脚本的工位默认目录与缓存解析仍只识别旧 station schema，不能据此判断现役工位需要重建。因此工位分析必须同时显式传入 `--tapdata-root` 和 `--cache-file`，不要依赖省略参数的默认解析，也不要因该默认解析错误执行 purge。

`$tapdata-align-branches release-v4.21.0` 应展开为上述 `show --version release-v4.21.0`，并按实际工位或独立目录补齐显式模块根目录、仓库范围和缓存路径，不能映射为 `--home $HOME`。如果最终目录不含主仓 `<tapdata-root>/tapdata`，脚本应立即报错停止；不得先把它当作 IDEA 平铺多仓目录，也不得扫描用户主目录。

工位固定源码位于 source/tapdata/<repository>。若主仓缺失，脚本在任何远端刷新前停止；若显式指定仓库缺失，结果为 `blocked`。未指定的缺失仓库以 `not_covered` 报告，不阻断其它仓库。输出包含本地状态、目标分支、目标 SHA、推导理由和目标状态；`unchanged` 表示不参与关系推导，而非对本地工作树采取操作。

## 刷新策略与进度

- `--refresh`：顺序强制查询本次推导依赖到的仓库，并更新对应单仓缓存。显式指定 `--repository` 时范围为主仓和指定仓库；未指定时处理完整目录。
- 省略 `--refresh`（默认）：优先读取该仓缓存；首次加载或缓存超过 1 小时时刷新。`show --json` 是分支分析结果，不是严格离线缓存查看：缓存过期时仍会联网并更新 v2 缓存。

JSON 顶层的 `outcome` 为 `complete`、`partial` 或 `blocked`，`scope` 说明本次严格范围，`blockers` 说明不能继续的必需事实。每行的 `target_status` 区分 `verified_exists`、`verified_missing`、`cached_exists`、`absence_unverified`、`not_covered` 与 `unresolved`；远端刷新失败绝不能输出 `verified_missing`。`rows[].refs.error_kind` 会区分 `repository_access_denied`、`ssh_auth_failed`、`network_unreachable`、`fetch_timeout` 与一般刷新失败，`prompt` 给出不含凭证的处理提示；普通表格也显示这两项。`timing_seconds` 分别记录 `fetch`、`local_resolution` 与 `total` 耗时。脚本会在 stderr 实时报告本地检查、每个仓库的刷新/复用和完成耗时；单个 `fetch` 仍在进行时每 10 秒报告一次心跳。

## 应用安全边界

- `apply` 在任何工作树切换前检查计划摘要、完整目标、远端 SHA、脏状态、detached HEAD、本地目标分支能否快进，以及目标分支是否被其它 worktree 占用。
- `apply` 不自动 stash、不 reset、不覆盖本地领先或分叉提交，不切换缺失或未核验的目标分支。
- 预检会把目标分支获取到对应仓库的 `origin/*`；获取完成后再次核对所有候选工作树，并在每个仓库切换前重读其分支、SHA 和脏状态。全部预检通过后才开始逐仓切换。Git 不提供跨仓事务，若运行期意外失败，`apply_result` 必须列出已应用、失败和待处理仓库及切换前 SHA，不得声称整体成功或自动执行有损回滚。
- `apply` 不提交、不推送、不写 Jira/GitHub，也不授权后续业务代码操作。

## 分支规则

- `current` 等已确认版本矩阵直接显示矩阵配置，并以缓存快照的 SHA 核验；缓存结果不是操作前的最终基线。
- `main`、`develop`、严格格式的 `release-vX.Y.Z` 使用项目已确认规则；带 `release-v` 前缀但格式不合法的名称直接拒绝。
- 功能分支先在配置列出的仓库找完全同名分支，绝不按 Jira 号模糊猜测。
- `common-lib`、`connectors`、`connectors-enterprise` 找不到同名分支时，从主仓该功能分支的 PluginKit 读取**精确** `release-vX.Y.Z`；目标分支不存在即 `unresolved`，绝不自动升到更高 release，也不再走 Tag 回退。
- 其余配置为 Tag 回退的仓库，使用主仓功能分支 first-parent 最近的产品 Tag：`X.Y.Z-dev` 推导 `develop`，`X.Y.Z` 推导 `release-vX.Y.Z`；Tag 图必须已由当前原生 Git 获取流程同步，`show` 不会为此修改模块仓库。缺少本地 Tag 图或目标分支不存在即 `unresolved`；显式 `apply` 只获取已经解析出的目标分支。
- docs/docs-en 不在活动目录；t-layer3-test 是可选验证依赖。

分析结果是分支事实；只有携带匹配 `plan_digest` 的显式 `apply` 才允许同步该用户开发环境。二者都不授权提交、推送、PR、合并或发布。
