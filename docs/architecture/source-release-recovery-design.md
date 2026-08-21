# 源码发布状态诊断与受控恢复设计

## 1. 目标

正常发布必须由 `maintainer/scripts/release.sh` 完成。脚本应先分析真实 Git/GitHub 状态，再给出唯一可执行的下一步；不得要求公司员工指导员手工删除分支、移动 Tag、推测 PR 状态或把测试夹具提示误解为发布故障。

本设计处理一种受控例外：故事门禁基线升级 PR 已把待发布候选提前合入 `main`，使 `release/vX.Y -> main` 没有可创建的 PR。它不回退或改写 `main` / `develop`，也不把该例外扩展为绕过正常 PR 发布流程。

## 2. 命令与状态

`release.sh` 增加两个显式子命令：

```sh
maintainer/scripts/release.sh inspect --version vX.Y --allow-soft-gate
maintainer/scripts/release.sh recover --version vX.Y --merged-pr <number> --allow-soft-gate
```

`inspect` 只读：刷新可信远端引用，读取本地/远端 Tag、固定发布分支、PR 及 Merge commit，自动解析与当前发布状态绑定的已合并 PR，并输出稳定状态、事实引用和无占位符的唯一下一命令。

`prepare` 与 `publish` 在发现候选已位于 `origin/main` 时复用检查结果，返回 `release_candidate_already_in_main`；不得将 GitHub 的 `No commits between ...` 误报为权限或通用 PR 创建失败。

至少区分以下状态：

- `release_candidate_ready`：按正常 `prepare` / `publish` 流程继续；
- `release_waiting_manual_merge`：给出 PR URL 和同一 `publish` 继续命令；
- `release_candidate_already_in_main`：候选已提前合入，给出 `recover` 命令；
- `release_local_tag_repair_required`：本地同名 Tag 不指向已核验候选，必须在恢复最终确认中重建；
- `release_remote_tag_conflict`、`release_reference_drift`、`release_merged_pr_invalid`：停止，不自动删除或覆盖远端引用。

## 3. 受控恢复

`recover` 只接受显式的 `--merged-pr`，并在任何引用变更前验证：

1. PR 属于 `tapstate/agentic-ops`，目标分支为 `main`，状态为 `MERGED`，且存在 Merge commit；
2. PR head 是 Merge commit 的祖先，Merge commit 仍位于刷新后的 `origin/main`；
3. `develop`、本地/远端 `release/vX.Y` 与本地/远端 `vX.Y` 的状态和检查结果一致；
4. 远端同名 Tag 不存在，或已精确指向同一候选；不删除、不覆盖远端 Tag；
5. 在固定候选 worktree 执行完整发布验证。

本例的原始基线缺少故事门禁，不能把事后 `origin/main` 检查伪装为当时的可信基线检查。因此 `recover` 必须把“候选已被基线升级 PR 提前合入”的风险和指定 PR 作为独立人工确认事实写入审计；它不能成为未来普通发布的快捷路径。

验证通过后，首次 `recover` 只展示完整确认包和绑定当前 PR、head、Merge commit、main 与 Tag 状态的精确继续命令，不产生引用副作用。第二次执行必须同时提供该命令中的 `--confirm-release` 与 `--confirm-recovery <binding>`；事实变化或绑定不一致时重新检查。确认有效后，脚本通过 Git 原子引用更新把错误的**本地**同名 Tag 重建为已核验 PR head 的 annotated Tag，推送不可变 Tag 并写入恢复审计。远端 Tag 已正确存在时允许幂等补写审计；错误或轻量远端 Tag 一律阻断。无效 `release/vX.Y` 分支不自动删除，保留为可检查事实。

## 4. 确认包契约

所有需要人工确认的发布路径共用确认包，且必须在取得确认前完整输出：

- 动作：即将执行的分支、PR、Tag、审计写入；
- 目标：仓库、源/目标分支、固定 head、版本；
- 影响与风险：包括软门禁、提前合入和本地 Tag 重建；
- 明确不执行项：不改写 `main` / `develop`，不删除或覆盖远端 Tag，不自动合并；
- 事实引用：当前 Git ref/Tag、PR URL/编号/head/Merge commit、验证时间与审计文件；
- 后续人工门禁与唯一下一命令；
- 精确确认引用或 `--confirm-release` 入口。

确认 ID 只绑定已展示的确认包，不能替代确认内容或事实引用。命令输出与测试必须能证明“只给 ID”“缺事实引用”均不会进入有副作用阶段。

## 5. 验收

- `inspect` 对正常、等待合并、候选已在 main、Tag 冲突和引用漂移输出稳定状态及唯一下一命令；
- `publish` 在无差异 PR 前返回 `release_candidate_already_in_main`，不调用 PR 创建；
- `recover` 拒绝缺失/错误或未绑定当前发布状态的 PR、非 Merge commit、远端 Tag 冲突、未确认状态及过期/伪造确认绑定；
- 恢复成功只重建本地错误 Tag 并推送正确的不可变远端 Tag，不修改 `main`、`develop` 或远端 Tag；
- 每个最终确认输出完整确认包；缺少任一确认项或事实引用时，测试证明没有分支、PR、Tag 或审计副作用；
- 保留既有四项固定完整验证与软门禁人工 Merge commit 约束。
