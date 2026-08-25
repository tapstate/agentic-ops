# developer 任务仓库范围与工作树生命周期设计

## 1. 目标与范围

本设计对应 AO-92，修复多仓库项目接管时把默认仓库过早固化为任务仓库、确认正确仓库后无法继续，以及任务工作树缺少完成清理闭环的问题。

仓库处理固定分为三个层次：

```text
源码池固定基线只读分析
-> 人工确认任务变更仓库范围
-> 仅为确认的变更仓库创建任务子工作树并编码
-> 汇总实际变更仓库并回写 Jira
-> 任务完成审计后安全清理任务子工作树
```

源码池成员是普通 clone，共享对象库并保留一个 Git 主工作树（primary worktree）。这里的“主工作树”是相对子工作树的 Git 概念，不表示该 checkout 一定处于 `main` 分支。AIAgent 不在池成员主工作树修改代码、创建任务分支或提交；池成员也不随任务完成删除。任务代码变更只允许发生在 `<source_pool_root>/.worktree/<JIRA-KEY>/<repo-short-name>/<from-branch>` 下的受管子工作树。

本设计不修改工作空间 `repositories.default`，不通过 `workspace init` 重写 Project Profile，不自动把源码搜索结果当作仓库确认，不删除源码池成员，也不放宽 Jira、Git、PR 或人工审查门禁。

## 2. 现状与缺口

现有 `record_current_task_source_context(...)` 在接管 Saga 本地收口阶段调用 `resolve_target_repository(...)`。Jira 描述缺少“目标仓库”时，Runtime 直接回退 `profile.default_repository`，随即按 `worktree_domain` 创建工作树并持久化来源上下文。

这造成四个缺口：

- `repositories.default` 同时承担“分析候选”和“最终变更仓库”两种语义；仓库事实未确认就创建任务工作树。
- 现役 Runtime 按目标领域预建全部工作树，不是先分析再只为实际变更仓库建树。
- 接管后虽能检测仓库不一致，却没有受支持的任务仓库范围重新确认、来源快照重建和旧 gate 失效入口。
- 任务工作树仅有创建失败回滚，没有任务完成后的公开安全清理操作。

AO-92 的复现中，研发工程师已确认实际代码位于 `tapdata/tapdata-connectors`，任务仍绑定 `tapdata/tapdata` 并停在 L4，证明上述边界必须形成一个完整生命周期，而不是只增加 Profile 更新能力。

## 3. 源码池分析规则

`workspace init` 继续按 Profile `repositories.list` 准备全部池成员。池成员主工作树仅供用户手动操作和 AIAgent 只读分析；AIAgent 任务操作不得在主工作树产生文件修改、branch、commit 或 PR。池成员当前 checkout 分支可能由用户改变，不参与任务分支推导。

逐仓分析基线按以下顺序确定：

1. `branches.baseline_branches[repository]`；
2. 仅当 Profile 没有声明 `baseline_branches` 映射时，回退 `branches.default_branch`；
3. 不读取池成员当前 checkout 分支作为隐式基线。

分析前在池成员锁内执行受控 `fetch origin`，然后把 `origin/<分析基线>` 解析为固定 commit SHA。仓库范围分析只读取该固定 Git 对象或只读快照；输出每个候选仓库的 slug、分析 ref、SHA、命中路径/符号和不确定性。无法刷新或解析时标记证据不足，不能把缓存 HEAD 描述为当前事实。

Jira 明确声明“目标仓库”时，它是优先候选，但仍需验证在 `repositories.list` 且属于唯一领域；未声明时 `repositories.default` 只作为候选建议。两种情况在仓库范围确认前均不创建任务工作树、不写来源快照，也不进入实现。

## 4. 仓库范围确认与重新绑定

公开操作使用可重复的 `--repository` 表达一个或多个变更仓库：

```sh
ao-work task repositories assess --issue-key TAP-12620

ao-work task repositories confirm \
  --issue-key TAP-12620 \
  --mapping-file TAP-12620-repository-branches.json \
  --confirm
```

`assess` 只读输出候选、固定分析基线及源码证据，产物名为 `proposed_repository_branch_map`。它只是分析建议，不是后续工作的最终依据。AIAgent 必须把完整建议表展示给用户；用户可以增加/移除仓库或修改任一仓库的 `from_branch`。

`assess` 同时输出可保存到工作空间普通 JSON 文件的 `confirmation_template`，其中完整绑定 `issue_key`、`agentic_run_id`、问题版本、问题版本来源仓库、固定 SHA 和 `repository_branch_map`。研发工程师在该模板上修正；不得把 `.agentic-ops/` 受管状态文件直接作为 `--mapping-file` 输入。

`confirm` 读取工作空间受管输入中的完整关系表。没有 `--confirm` 时只展示“分析建议 → 用户修正结果”的逐项差异、将失效的来源/gate 和计划工作树；带 `--confirm` 才把用户确认结果保存为 `confirmed_repository_branch_map`。确认动作不预建任务工作树；具体仓库真正开始工作时，再从确认表按需创建对应子工作树。

每个 repository 必须：

- 使用唯一 `owner/repository` 格式；
- 位于当前 Project Profile `repositories.list`；
- 属于唯一 `worktree_domain`；
- 用户确认的 `from_branch` 是合法 Git 分支名，并能在刷新后的对应 repository `origin` 中唯一解析；它可以不同于分析建议，但必须记录修正前值、修正后值和人工确认事实；
- 与当前 Jira、Assignee、安装身份和任务运行事实一致。

初次确认、替换错误仓库和增加跨仓库变更范围使用同一操作。形成任何代码事实前允许用新确认替换范围；已有修改、commit、push 或 PR 后，不得移除已有仓库，也不得静默改绑。新增仓库必须单独展示增量范围与风险并重新确认；仓库删除或替换进入风险决策。

仓库范围确认复用同一 `agentic_run_id`，不重写接管 Comment/Status，也不新建第二个任务运行。

仓库分析后、创建任何任务子工作树前，Runtime 必须形成可供设计审查的建议表和用户确认表。设计确认对象至少包含：

- 问题版本原始规格、问题版本来源仓库、解析后的问题版本分支及其固定 SHA；
- 确认的变更仓库有序列表；
- 每个仓库的分析建议分支、用户确认的 `from_branch`、两者差异、固定 baseline SHA、计划任务分支和计划工作树路径；
- 无法解析、需要显式分支或存在多候选的仓库及人工动作。

问题版本或任一用户确认的逐仓分支关系变化都会改变设计事实并使旧设计确认失效；必须重新展示完整确认表并进入设计审查，不能把分析建议、仓库名或内部摘要当成人工确认。设计同时展示分析时的 remote SHA 作为代码证据，但人工确认主体是问题版本和 `confirmed_repository_branch_map`；开始工作前仅 remote Head 前移且关系未变时，Runtime 更新工作树基线 SHA、重建来源上下文并重跑 intake/solution。若新代码事实改变设计、范围或风险，再重新进入设计审查。

## 5. 任务子工作树与编码规则

分析阶段可以按 Profile 和项目对齐工具生成分支关系建议，但 Runtime 不得直接按建议创建工作树。用户确认或修正完整关系表后，实际开始某个或某组仓库工作时，只选择 `confirmed_repository_branch_map` 中的对应条目执行：

1. 解析任务“问题版本”。Profile 为工作树领域显式声明 `problem_version_repository`；缺失时兼容使用该领域 `baseline_repository`。问题版本未在 Jira 声明时读取问题版本来源仓库的 `baseline_branches`，只有 Profile 完全未声明映射时才回退 `default_branch`。
2. 使用 TapData 产品仓库的任务（包括仅切换 Jira 项目的 TapState）将 TM、FE、connector 仓库统一归入 `product` 领域并声明 `problem_version_repository: tapdata/tapdata`，以 `tapdata/tapdata` 的问题版本作为对齐输入，使用版本化 `tap_align_branches.py plan --no-fetch --remote-only --repositories <candidate repos> --json` 生成逐仓建议分支。两个 connector 独立按同一问题版本推导各自远端分支；PluginKit 或分支无法解析时阻断，不以 common-lib 或 `main` 静默替代。领域仍用于候选范围和归属校验，不再兼任问题版本来源。其它项目或非 TapData 产品领域按各自 Profile 的 problem version repository、overrides、dev_branches、same_name 顺序生成建议。
3. 用户可以修正建议分支；Runtime 只校验确认分支的格式、仓库范围和远端存在性，不得以“与建议不一致”为由覆盖用户结果。开始工作时刷新本次所需仓库的 origin，并在创建任何工作树前解析、记录这些仓库确认分支的远端基线 SHA；任一失败则不创建。
4. 使用 `git worktree add --detach <path> <fixed-sha>` 仅创建或精确复用本次所需仓库的子工作树，写入 per-worktree Git identity。
5. 在子工作树内按项目分支规则创建任务分支；完成分支与身份回读后才允许代码修改。

源码分析阶段没有被确认进入变更范围的仓库不得创建任务工作树。分析后发现需要新增仓库时，返回仓库范围增量确认，不得直接在源码池或其它任务工作树修改。

任务子工作树路径固定为：

```text
<source-pool-root>/.worktree/<jira-key>/<repo-short-name>/<normalized-from-branch>
```

其中 `repo-short-name` 是 `owner/repository` 的仓库短名；用户输入中的 `sort-repo-name` 在本设计中按该含义规范为 `repo-short-name`。`from_branch` 保存原始值用于审计，路径段沿用安全规范化规则（例如 `feature/x` → `feature-x`），并执行分支名、路径穿越、短名冲突和总长度校验。目录顺序固定为 Jira → 仓库 → 来源分支，替换现役 Jira → 问题版本 → 仓库布局；发现旧布局时失败关闭，不自动迁移或删除。

## 6. 状态、恢复与仓库清单

TaskStore 在同一任务锁内保存 `repository_scope_operation`：

```text
analysis_recorded
-> proposal_recorded
-> mapping_confirmed
-> worktrees_active
-> completion_evidence_readback
-> worktrees_cleaned
```

稳定意图同时保存 `proposed_repository_branch_map`、`confirmed_repository_branch_map`、两者差异、问题版本、Profile 摘要和人工确认事实。重复调用只能从原阶段恢复；确认映射、输入集合或外部事实漂移时失败关闭。

本地任务状态必须分别持久化建议表和确认表，而不是只保存一个 `target_repo`。`confirmed_repository_branch_map` 每项至少包含：

- `repository`；
- `problem_version_repository`；
- `problem_version`；
- `derivation_rule`；
- `proposed_from_branch`；
- Profile 分析源的 `analysis_branch` / `analysis_baseline_sha`、建议分支及其 `proposed_branch_sha`；
- 用户确认的 `from_branch` / `confirmed_branch_sha` 与工作树创建时的 `worktree_baseline_sha`；
- `user_corrected`；
- `task_branch`；
- `worktree_path`；
- `worktree_status`（`not_created`、`prepared`、`cleaned` 或 `blocked`）。

完成设计确认时所有条目都应保持 `not_created`。实际需要在某仓库工作时，Runtime 只从 `confirmed_repository_branch_map` 读取该仓库和 `from_branch`，重新回读远端 SHA 后创建或复用对应子工作树，并把 `worktree_baseline_sha` 与来源上下文写回任务状态；不得回退分析建议、临场重新推导或在确认表外创建工作树。

任务状态分别记录：

- `confirmed_change_repositories`：人工确认后允许创建工作树和编码的仓库集合；
- `actual_change_repositories`：由 Git/PR 回读证明实际产生变更的最终仓库集合。

每个最终仓库证据至少包含：

- `repository`；
- `problem_branch` 与固定 baseline SHA；
- `task_branch`；
- 最终 Head SHA；
- 变更路径摘要；
- 验证结果；
- push 后远端 SHA；
- PR URL、PR Head 和目标分支（适用时）。

`actual_change_repositories` 不能由计划列表直接复制。Runtime 必须通过逐仓 Git diff、commit、远端分支和 PR 回读形成；确认范围中没有实际变更的仓库保留在审计中但不列入“实际变更仓库”。发现实际变更仓库不在确认集合时立即阻断提交、推送和完成总结。

仓库范围变化会使旧来源快照、intake、solution、gate 与相关授权摘要可审计失效：历史文件保留，journal 写入失效原因和新范围，不原地伪造旧摘要。收口后从新来源上下文重新执行 `task intake assess`。

## 7. Jira 完成总结

任务实现和验证完成后，在 Jira 结果反馈评论的“完成内容”中必须逐仓汇报 `actual_change_repositories`；“已输出表单字段”必须明确包含 `actual_change_repositories`。评论仍遵守公共 `evidence` 模板，不修改跨工作面的公共必填键。

示例：

```markdown
- 运行 ID: `run-TAP-12620-...`
- 完成内容:
  - 实际变更仓库:
    - `tapdata/tapdata-connectors`：问题分支 `develop@<sha>`，任务分支 `<branch>`，最终 Head `<sha>`，PR `<url>`
  - 未产生变更的已确认仓库: `无`
- 验证结果:
  - `tapdata/tapdata-connectors`：`<commands and result>`
- 残留风险: `<risk or none>`
- 已输出表单字段: `actual_change_repositories`、验证证据、PR 证据
```

生成评论计划前，Runtime 必须校验列表非空、每项有 Git 证据、集合不越过确认范围，并与当前任务最终 Head/PR 一致。评论继续使用 `plan -> apply -> readback`；回读不一致时不得流转 Jira 完成状态，也不得开始工作树清理。

## 8. 任务工作树清理

完成总结评论和 Jira 完成状态均已回读后，执行公开清理操作：

```sh
ao-work task worktrees cleanup --issue-key TAP-12620
```

清理只处理任务状态精确登记的路径，不接受任意目录、glob 或源码池根。逐仓清理前必须验证：

- Jira/task-run 已到允许清理的终态；
- 工作树 clean；
- 最终提交已被远端分支或 PR/合入事实承接；
- 当前 worktree、repository、问题版本、任务分支和 Head 与完成审计一致；
- 没有进行中的 repository scope 或外部写入不确定状态。

任一仓库不满足条件时整体阻断，不删除任何工作树。全部通过后获取池成员锁，逐仓执行非强制 `git worktree remove`，回读 `git worktree list --porcelain`，再删除空的任务父目录并记录 `worktrees_cleaned` 事件。不得使用 `--force` 丢弃 dirty 或未承接提交；失败时保留剩余工作树与恢复清单。源码池成员永不由本操作删除。

## 9. 实现落点

- `developer/runtime/src/ao_work/work_cli.py`：注册 repositories assess/confirm 与 worktrees cleanup。
- `developer/runtime/src/ao_work/task_repository_scope.py`：生成建议关系表、读取用户修正、输出逐项差异、确认完整关系表并维护可恢复状态机。
- `developer/runtime/src/ao_work/task_worktree.py`：只按 `confirmed_repository_branch_map` 准备工作树；将路径改为 Jira/仓库短名/from-branch，并新增严格、非强制、可回读的生命周期清理原子操作。
- `developer/runtime/src/ao_work/config/` 与 TapData Profile：为领域增加显式 `problem_version_repository`，将 TM、FE、connector 的问题版本来源统一绑定到 `tapdata/tapdata`，并将 connector 纳入产品领域。
- `developer/runtime/src/ao_work/task_start.py`：接管只记录任务/Jira 基础上下文；仓库范围确认后记录分析上下文，按需创建工作树时再记录精确逐仓 source context。
- `developer/runtime/src/ao_work/task_state/`：保存 confirmed/actual repository 集合、范围阶段、失效证据和清理状态。
- `developer/runtime/src/ao_work/task_gate.py`、`task_resume.py`：仓库未确认、范围变化或清理未收口时返回唯一合法下一动作。
- `developer/runtime/src/ao_work/task_run/`：逐仓记录 Git、验证、push 和 PR 证据，生成 `actual_change_repositories` 并阻断越界变更。
- `developer/runtime/src/ao_work/jira/`：结果评论计划校验实际变更仓库表单，并继续使用公共 evidence 模板和 plan/apply/readback。
- developer 操作契约、能力目录、DE-004/DE-005、AI 员工手册与相关架构文档：同步新能力、停止条件和恢复语义。

## 10. 验收矩阵

1. 源码池分析只读取刷新后的固定 ref/SHA，不修改池成员主 checkout。
2. Jira 未声明仓库时默认仓库仅作为候选；仓库范围确认前不创建工作树、不写来源快照。
3. 分析只生成 `proposed_repository_branch_map`，任何路径都不得直接把它用于建树、编码或授权。
4. 用户可以增加/移除仓库或逐仓修改 `from_branch`；Runtime 展示完整差异并验证远端存在后，将明确确认的结果保存为 `confirmed_repository_branch_map`。
5. 单仓与多仓设计审查必须同时展示问题版本、建议表、用户修正差异和完整确认表；确认时不创建任何子工作树。
6. 实际开始某仓库工作时只从确认表创建对应工作树；目标领域其它仓库仅作源码池分析，不预建工作树。
7. 新工作树路径严格为 `<source-pool-root>/.worktree/<jira-key>/<repo-short-name>/<normalized-from-branch>`；斜杠分支规范化、短名冲突、路径穿越和旧布局均有失败关闭测试。
8. TapData TM、FE、connector 均以 `tapdata/tapdata` 问题版本为输入，由 remote-only 对齐计划生成建议分支；用户确认结果才是最终关系，当前 connector 独立基线语义不得继续生效。
9. 各仓库分支关系确认后才允许按需创建；本次所需仓库的 remote SHA 必须全部固定，任一失败不产生未确认或部分任务工作树。
10. 错误初始范围可在代码事实前替换；已有代码事实后移除/替换失败关闭，新增仓库重新进入增量确认。
11. 问题版本、仓库列表或确认分支关系变化后旧设计确认、source/intake/solution/gate 失效，同一 run 从新来源重新准入。
12. 实际变更列表由逐仓 Git/PR 回读生成；越过确认范围、缺 Head/验证/远端或 PR 证据时不得提交完成总结。
13. Jira evidence 评论逐仓报告实际变更仓库，plan/apply/readback 一致后才允许完成流转和清理。
14. 清理对 dirty、未推送、未被 PR 承接、路径/Head 漂移或外部结果不确定整体阻断；成功只移除精确任务工作树和空父目录，不删除池成员。
15. 任一范围准备、来源重建、Jira 回写或清理阶段中断后可从同一稳定意图恢复，不重复外部副作用。
16. 现有接管、恢复、任务工作树、准入门禁和 Jira 评论专项测试保持通过，再执行四项完整验证。

```sh
bash maintainer/scripts/test-python-runtime.sh
bash maintainer/scripts/test-resources.sh
bash developer/tests/bootstrap/test_install_boundary.sh
bash maintainer/scripts/test-release-workflow.sh
```

## 11. 风险与设计边界

- **仓库或分支误判**：分析建议没有执行权限；只有用户可修正并明确确认的完整关系表允许驱动建树和编码。
- **跨仓库审计不完整**：confirmed 与 actual 两个集合分离；最终列表只接受 Git/PR 回读证据。
- **分析源漂移**：所有分析和工作树基线绑定刷新后的 remote SHA，不依赖池成员当前 checkout。
- **领域与版本来源混淆**：Profile 分开保存候选领域与 `problem_version_repository`；TapData connector 归入产品候选领域，问题版本来源仍是 `tapdata/tapdata`。
- **清理丢失成果**：默认非强制删除，dirty、未承接提交或事实漂移整体阻断。
- **公共模板边界**：不修改跨工作面 Jira 评论公共必填键；任务专用仓库表单通过 `actual_change_repositories` 输出并由 developer Runtime 校验。

设计审查确认后，连续执行授权仅覆盖上述 developer Runtime、标准资产、文档与测试实现、验证和必要 AO-92 进度回写。业务 Jira 真实写入、业务仓库 commit/push/PR、Jira 完成流转和实际工作树清理仍由对应任务授权与门禁控制；自动仓库确认、源码池代码修改、强制工作树删除、schema 不兼容或保护分支写入必须重新进入风险决策。
