# AgenticOps 源码发布工作流设计

## 1. 目标与范围

本文定义 `tapstate/agentic-ops` 源头仓库进入正式研发后的分支、版本、验证、发布和紧急修复流程。

本文中的“发布”是指代码完成验证后，通过拉取请求合入稳定分支 `main`，并按版本规则管理 Git tag。它不表示创建 GitHub Release，也不改变安装后 AIAgent 执行业务 Jira 任务的运行规范。

## 2. 分支职责

- `main` 是稳定主分支，也是 GitHub 默认分支和安装脚本读取的分支。
- `develop` 是日常开发分支。正常发布必须以完成验证的 `develop` HEAD 为来源，通过拉取请求合入 `main`。
- `release/vX.Y` 是软门禁模式从已验证 `develop` HEAD 创建的固定发布分支，用于避免等待人工合并期间 `develop` 的后续提交改变发布内容。
- Hotfix 不创建修复分支；它直接把已同步 `develop` 生成 Jira key 绑定的 Merge commit。
- `main` 禁止直接提交和普通直接推送；Hotfix 脚本是唯一受控直推例外。强制推送和删除始终禁止。
- 所有合入 `main` 的拉取请求使用 Merge commit，不要求 GitHub CI 或代码审查批准。
- Hotfix 原子推送同一 Merge commit 到远端 `main` 与 `develop`，再同步本地 `develop`；任一远端引用不能更新时整体失败。

## 3. 版本与 Tag

### 3.1 运行版本

现役 Python 交付物不构建 AgenticOps 自有平台二进制。运行版本由固定 Git ref、`vX.Y` 版本线和 commit 标识追溯；旧 `TYPE-vX.Y.COMMIT_NUM-COMMIT` 只保留在冻结的 Go 版本号设计中，不作为 `ao-maint` / `ao-work` 的新构建协议。

### 3.2 Git tag

- Git tag 只允许二段式 `vX.Y`，例如 `v0.3`。
- `vX.Y` 是该版本线的编译基线，只创建一次，不代表该版本线中的每次交付。
- `prepare` 固定发布候选；软门禁模式创建或核验本地 `release/vX.Y` 分支必须指向该已验证 HEAD。
- annotated `vX.Y` tag 只在发布 PR 合入 `main` 后创建，并指向经过核验的 Merge commit。
- 远端 tag 不移动、不覆盖、不删除、不强制推送。
- Hotfix 不读取版本基线，也不创建、移动或推送 tag；已有 `vX.Y` 继续作为 `main` 历史版本线事实。

## 4. 用户入口与组件边界

用户入口只保留两个脚本：

```text
maintainer/scripts/release.sh
maintainer/scripts/hotfix.sh
```

内部公共实现放在：

```text
maintainer/scripts/lib/release-common.sh
maintainer/scripts/lib/development-workflow.sh
```

- `release.sh` 负责 `develop` 正常发布的 `prepare` 和 `publish`。
- `hotfix.sh` 只负责 `publish --jira-id <KEY>`，不创建分支、PR、Tag，不调用 Jira 或 `gh`，也不设置额外人工门禁。
- `release-common.sh` 负责参数、仓库、版本、验证、确认、拉取请求、等待和审计公共逻辑。
- `development-workflow.sh` 负责本地 Hooks、`develop` 和 GitHub `main` 保护的检查与幂等配置。
- 源头仓库维护脚本可以编排 `git`、`gh` 和固定验证命令。该例外只适用于 AgenticOps 源头仓库维护，不允许把安装后 AIAgent 的 Jira、GitHub、Git、策略或证据业务逻辑迁回 Shell。

版本化本地 Hooks：

```text
.githooks/pre-commit
.githooks/pre-push
```

- `pre-commit` 阻止在 `main` 直接提交。
- `pre-push` 阻止直接向远端 `main` 推送。

`.githooks` 只保存版本化策略源。`development-workflow.sh` 在 Git common directory 安装带版本标记的 trusted launcher，并把 `core.hooksPath` 指向该目录；launcher 从当前已接受 `HEAD` 提取并执行 Hook，不能直接执行 candidate 工作树文件。pre-commit 再用 `HEAD` Runtime 检查隔离的 index 快照。

AO-43 安装后，pre-commit 只校验故事映射、候选快照安全、固定验收证据和信任根，不要求人工批准先于 commit；pre-push 再按版本化分支策略检查 commit 审查或允许任务分支形成 PR。AO-43 安装提交的旧 `HEAD` 仍执行旧版 Hook，因此该笔候选按旧基线展示一次完整 staged 报告，由公司员工指导员确认报告资源、变更点和风险后内部完成旧版批准与验收，再通过正常 Hook 提交。该迁移不要求用户确认内部 `impact_id`，不使用 `--no-verify`，也不能在新基线进入 `HEAD` 后重复使用。信任根首次进入受保护 `main` 仍必须由独立人工审查 PR 安装。

## 5. 正常发布流程

### 5.1 准备版本

命令：

```bash
maintainer/scripts/release.sh prepare --version v0.3
```

顺序：

1. 检查目标仓库、`git`、`gh` 和 GitHub 登录状态。
2. 检查 GitHub 默认分支为 `main`。
3. 检查本地 Hooks、远端 `develop` 和 `main` PR-only 保护；缺失时展示配置动作并逐项取得确认后修复。
4. 要求当前分支为 `develop`、工作区干净且本地没有落后或分叉。
5. 校验版本参数符合 `^v[0-9]+\.[0-9]+$`。
6. 在临时 worktree 对固定 HEAD 执行四项完整验证，覆盖 Python 锁文件、两个工作面边界、developer Skill / Rule / 标准资产、Shell Bootstrap、developer-only sparse 安装、`ao-work`、更新和回滚。
7. 验证本地和远端均不存在同名 tag；正常发布不复用合入前的 tag。
8. 软门禁模式创建或核验本地 `release/vX.Y` 指向固定 HEAD，暂不推送。
9. 记录固定 HEAD、固定发布分支、验证时间和验证清单。
10. 输出待发布提交与验证清单，停止在研发工程师审查和提交点。

`prepare` 不暂存、不提交、不推送代码。验证失败时不得创建发布分支或 tag；修复后以同一版本重新执行。

### 5.2 发布版本

命令：

```bash
maintainer/scripts/release.sh publish --version v0.3
```

GitHub Free 私有仓库无法使用所需 Ruleset 和 Auto-merge 时，必须显式启用软门禁：

```bash
maintainer/scripts/release.sh publish --version v0.3 --allow-soft-gate
```

顺序：

1. 重复执行仓库和研发流程门禁。
2. 要求当前分支为 `develop`、工作区干净且全部变更已提交。
3. fetch 远端；本地 `develop` 可以领先 `origin/develop`，但不得落后或分叉。
4. 校验本地和远端都没有 `vX.Y`；软门禁模式只允许使用 `prepare` 固定的发布分支，不能由执行时的 `develop` HEAD 替换候选。
5. 以刷新后的 `origin/main` 与固定候选的 merge-base 作为故事门禁范围基线，并在临时 Git worktree 中执行完整验证。
6. 展示版本、目标仓库、源分支、目标分支、待推送提交和验证结果。
7. 交互取得最终人工确认；非交互环境必须显式传入 `--confirm-release`。
8. 推送 `develop`。
9. 硬门禁模式创建或复用唯一的开放 `develop → main` 拉取请求，使用 Merge commit 启用 Auto-merge 并等待合并。
10. 软门禁模式从固定发布 HEAD 创建或复用 `release/vX.Y`，推送后创建或复用 `release/vX.Y → main` 拉取请求。
11. 软门禁首次执行在创建 PR 后每 5 秒查询一次状态，最多等待 30 分钟；研发工程师仍须在 GitHub 页面选择 Merge commit 人工合并，脚本检测到合并后在同一进程继续。
12. 若需要非阻塞地保留人工续跑，传入 `--no-wait-for-merge`；脚本写入等待审计并返回状态码 `2`，人工合并后重新执行同一条 `publish` 命令。
13. 自动续跑或人工续跑都必须再次执行完整验证；脚本使用固定发布分支恢复上下文，验证固定 HEAD 未漂移。
14. fetch `origin/main`，确认 PR 已合并、合并结果保留固定发布 HEAD 的提交历史且该 HEAD 已包含在 `origin/main`；Squash 或 Rebase 合并必须停止 Tag 发布。
15. 确认 PR 使用保留固定候选的 Merge commit 后，将 `develop` 快进到已验证的 `origin/main`；若远端或本地 `develop` 不能快进，立即失败，不得普通 merge、rebase 或改写历史。
16. 在该 Merge commit 创建 annotated `vX.Y` tag 并推送。
17. 写入结构化发布审计并输出发布结果，其中包含已同步的 `develop` 提交。

`release/vX.Y` 不自动删除。本地或远端已存在同名分支时，只有其目标与第一次验证的固定发布 HEAD 完全一致才允许恢复执行。

## 6. Hotfix 流程

### 6.1 发布修复

命令：

```bash
maintainer/scripts/hotfix.sh publish --jira-id AO-123
```

顺序：

1. 校验 Jira key 格式；该 key 只用于 Git Merge commit，不触发 Jira 读取或写入。
2. 要求当前分支为 `develop` 且工作区干净。
3. 刷新 `origin/main` 与 `origin/develop`，要求本地 `develop` 与远端完全一致。
4. 若两条远端分支已相同，幂等返回 `changed=false`。
5. 固定 `origin/main` 与 `origin/develop`，用 Git 自动计算合并 tree；存在内容冲突时停止，不执行交互式冲突处理、rebase、cherry-pick 或强推。
6. 以 `origin/develop` 的 tree、`origin/main` 第一父提交和 `origin/develop` 第二父提交构造 Merge commit；标题与正文均写入 Jira key。
7. 使用单次 atomic push 把该提交同时更新到远端 `main` 和 `develop`，不允许部分更新。
8. 快进本地 `develop` 并刷新远端引用，回读确认三者指向同一 Merge commit。

该流程不创建分支、PR、Tag 或本地发布审计，不调用 `gh` 或 Jira，不运行完整发布验证，也不等待额外人工确认。显式执行命令本身就是本次快速修复授权。

## 7. 研发流程配置门禁

### 7.1 硬门禁

发布脚本在任何测试、推送或拉取请求操作前检查：

- GitHub CLI 认证可用：先执行 `gh auth status -h github.com`；状态检查失败时回退执行 `gh api user`，只有两项都失败才阻断，且不输出令牌或认证响应正文。
- `core.hooksPath` 指向 Git common directory 中带 `AGENTIC_OPS_TRUSTED_HOOK_LAUNCHER_V1` 标记的 trusted launcher 目录。
- 远端 `develop` 存在。
- GitHub 默认分支是 `main`。
- `main` 的仓库规则无 bypass，禁止直接推送、强推和删除，并要求通过拉取请求合入。
- `main` 至少需要 1 个独立人工批准，最后推送者不能自批，新的提交撤销旧批准，且必须解决全部 review threads。这样 candidate 即使删除仓库内 release/story gate 调用也不能自动合并。
- 当前不要求必需 GitHub CI；服务器信任根是上述独立人工审批 Ruleset，`origin/main` Runtime 是自动发布的确定性复检层。

发现缺失或漂移时：

1. 展示当前值、期望值和准备执行的动作。
2. 逐项请求用户确认。
3. 确认后调用幂等配置逻辑修复。
4. 用户拒绝、权限不足或修复后复检失败时立即停止。

非交互环境默认只检查并失败；只有显式传入 `--configure-workflow` 时才允许执行配置。

### 7.2 GitHub Free 软门禁

GitHub Free 私有仓库无法配置本设计要求的 `main` Ruleset 与 Auto-merge。正常发布允许在 `prepare` 和 `publish` 显式传入 `--allow-soft-gate`；脚本不得自动探测并静默降级，也不得把软门禁保存为仓库默认值。Hotfix 不使用硬/软门禁模式。

软门禁仍强制检查：

- GitHub CLI 认证可用。
- `core.hooksPath` 指向 Git common directory trusted launcher 目录，且 launcher 能从已接受 `HEAD` 加载版本化 `pre-commit`、`pre-push`。
- 远端 `develop` 存在。
- GitHub 默认分支是 `main`。
- 仓库允许 Merge commit。

软门禁只放宽 Ruleset 和 Auto-merge，不允许直接推送 `main`。由于服务器端无法阻止其他账号直推，命令输出、PR 描述、等待记录和完成审计都必须标记 `protection_mode=soft` 并显示风险说明。

软门禁默认在首次 `publish` 创建 PR 后每 5 秒查询一次状态，最多等待 30 分钟；人工完成 Merge commit 后，当前进程自动再次验证固定发布 HEAD 并继续发布。`--no-wait-for-merge` 保留非阻塞人工续跑能力：它以专用状态码 `2` 表示 `waiting_for_manual_merge`，人工合并后重新执行相同的 `publish` 命令。自动或人工续跑都不得复用首次测试结论。

## 8. 完整验证

正常发布的 `prepare` 与 `publish` 都在临时 Git worktree 中固定执行：

```bash
bash maintainer/scripts/test-python-runtime.sh
bash maintainer/scripts/test-resources.sh
bash developer/tests/bootstrap/test_install_boundary.sh
bash maintainer/scripts/test-release-workflow.sh
```

其中 `test-python-runtime.sh` 统一运行 maintainer/developer Runtime 回归，`test-resources.sh` 验证工作面、Skill、Rule、标准资产和旧分发残留，`test_install_boundary.sh` 验证 developer-only sparse 安装、更新与回滚，`test-release-workflow.sh` 验证正常发布门禁及 Hotfix 直合、原子性和幂等行为。正常发布验证命令不可由普通参数替换或跳过。Hotfix 执行期不运行这组完整验证。

`publish` 在完整验证前刷新官方 `origin/main`，分别创建 baseline 和固定 candidate worktree，只执行 baseline 的锁文件、launcher 和 Runtime 来检查从二者 merge-base 开始的 candidate 范围。`origin/main` 缺少新门禁时返回 `release_story_gate_baseline_upgrade_required`；Hook、故事门禁、注册表、锁文件或发布脚本等信任根发生净变更时返回 `release_story_gate_trust_root_changed`。两种情况都不能自动创建或合并 PR，必须先通过受保护 `main` 的独立人工审查 PR 安装或升级信任根。

本地 trusted launcher 用于隔离 candidate Hook 和防止误操作，但拥有本机 Git 控制权的人仍能修改 Git 配置或使用 `--no-verify`。因此它不单独构成硬安全边界；无 bypass、强制独立人工批准且最后推送者不能自批的 `main` Ruleset 是服务器信任根，`origin/main` 基线负责确定性复检。

## 9. 幂等、失败与恢复

- 本地分支落后或与远端分叉时停止，不自动 pull、merge 或 rebase。
- 已存在源分支和目标分支匹配的开放拉取请求时复用。
- 匹配的拉取请求已合并时直接进入远端包含关系验证。
- 软门禁根据版本和固定 HEAD 恢复发布，不使用执行时可能已前进的 `develop` HEAD 替换 `release/vX.Y`。
- 软门禁 PR 仍开放时返回状态码 `2`，不轮询、不自动合并，也不推送 tag。
- 软门禁 PR 已关闭但未合并、HEAD 漂移或合并结果未保留固定 HEAD 历史时停止并返回稳定错误码。
- 推送后失败不回滚远端分支，不自动关闭拉取请求。
- 不删除分支，不移动或强制更新远端 tag。
- 重复执行从当前可验证阶段继续，不重复创建拉取请求或重复推送相同 tag。
- 每个失败返回稳定错误码、失败阶段和可执行人工恢复动作。

## 10. 发布证据

拉取请求描述记录：

- 版本基线。
- 源分支和目标分支。
- 待合并 HEAD。
- 固定验证命令及结论。
- 本地验证完成时间。
- 保护模式；软门禁必须显示 GitHub Free 私有仓库缺少服务器端 PR-only 保护的风险。

脚本在 `.local/release-runs/` 写入结构化 JSON，至少包含：

- 操作模式和阶段。
- Jira ID（仅旧发布审计兼容；现役 Hotfix 不写本地发布审计）。
- 版本基线。
- PR 编号和地址。
- Merge commit。
- Tag 及其目标（正常发布）。
- 验证时间和最终状态。
- `protection_mode`；等待人工合并时写入 `waiting_for_manual_merge` 状态。

GitHub PR 和 Merge commit 是发布事实源，本地 JSON 是执行审计记录。输出不得包含 token、完整环境变量或原始敏感日志。

审计写入只允许在仓库物理根内逐级创建普通目录 `.local/release-runs`；任一祖先或叶子是符号链接、特殊文件或物理路径逃逸时，以 `release_audit_path_unsafe` 失败。JSON 使用同目录私有临时文件并以 rename 原子落盘，写后仍须是普通文件，不能跟随链接覆盖仓库外内容。

## 11. 正式规则状态

- 原临时开发限制已移除，不再作为当前执行规则。
- 分支职责、正常发布门禁、版本、Hotfix 直合和审计要求已迁入永久项目规则。
- 真实 Git、GitHub 和 Jira 操作继续受永久策略和明确人工确认约束，不再一律禁止。
- 发布检查清单、当前架构、版本设计、项目维护者故事、维护者上手和 README 索引必须与本流程保持一致。
- `main` 普通直推由 `develop` 日常开发和 PR-only 正常发布流程替代；Hotfix 是唯一脚本化例外。

## 12. 测试要求

新增：

```text
maintainer/scripts/test-release-workflow.sh
```

测试使用临时 Git 仓库和 fake `gh`，不得真实修改 GitHub 仓库设置、推送分支、创建拉取请求或推送 tag。至少覆盖：

- `main` 提交和推送被 Hooks 阻止，`develop` 正常工作。
- 缺失研发流程配置时的确认、拒绝、非交互失败和幂等修复。
- 正常发布 `prepare` 和 `publish`。
- Hotfix 单一 `publish --jira-id` 入口、Jira key 格式和 Merge commit 信息。
- Hotfix 不创建分支、PR 或 Tag，不调用 Jira/`gh`，并原子同步 `main` 与 `develop`。
- 拉取请求创建、复用、已合并恢复和等待超时。
- `--allow-soft-gate` 显式启用、默认不降级和软门禁保留的基础检查。
- 普通发布固定 `release/vX.Y` 分支、首次返回状态码 `2`、人工 Merge commit 后同命令恢复和二次完整验证。
- 软门禁拒绝 PR HEAD 漂移、关闭未合并、Squash 和 Rebase 合并。
- 任一验证失败时没有远端写入。
- Merge commit 后 `origin/main` 包含关系验证。
- 审计 JSON 不包含敏感信息。

完整回归继续执行第 8 节列出的全部命令。
