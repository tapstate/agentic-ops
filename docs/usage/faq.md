# 常见问题

## `gh auth status` 或安装命令失败

确认 Git、Python 3.9+ 和 GitHub CLI 已安装；再检查当前 GitHub 账号是否能读取 `tapstate/agentic-ops`。需要重新登录时按[gh 一键安装](gh-one-click-install.md)操作。没有 `gh` 时使用[Git SSH 安装](git-ssh-install.md)。

## 安装目录已存在

安装程序不会覆盖 `~/.agentic-ops`。这是已有安装时使用[更新与回退](update-and-rollback.md)的 `update`，不是重复运行安装。

## `doctor` 报接线漂移或 Agent 无法启动

先运行 `~/.agentic-ops/agenticops station doctor --station <项目工位>`；确认后使用同一路径的 `repair --station <项目工位>`。不要手改 `.agenticops/station.json`、Hook 或生成的 Skill 链接。

## 提示旧 Hook 或工位代际不兼容

新工位不生成通用 Agent Hook。先区分工位代际：跨 epoch 必须按[更新与回退](update-and-rollback.md)由原版本结束任务、归档释放或清理，再显式 purge 后切换重建；不能使用下面的命令在线采用旧状态。

仅在同 epoch 的工位中，检测到托管旧 Hook 时，start、普通 repair 和 doctor 会报告迁移范围并保留文件。核对归属及调用不再逐次受检的边界后，从该工位执行：

```sh
./agenticops station repair --accept-checkpoint-migration
```

此操作仅移除归属哈希匹配的旧托管 Hook，并刷新指引和初始化清单；同 epoch 的任务、run、历史确认和证据保留。任一旧 Hook 已被修改时停止，不覆盖用户内容。退役文件可从 Git 历史查阅，但当前安装已无其执行依赖，不能只复制旧模板来恢复拦截；需要回退时仍遵守产品版本与工位 epoch 的完整检查。自定义或全局 Hook 不受本次迁移管理。

迁移后 Git/Jira/PR 使用平台原生权限；方案确认在 Workflow 检查点重新校验，Jira 同步不再保证强制单次调用。旧 Agent 会话应结束，再从工位启动，使接线与当前版本一致。

## 状态命令提示缺少 run 或阶段

从 `task.py status --issue-key <key>` 或 `next` 读取当前 run 与阶段。状态写命令须带 `--expected-run-id <run>`，advance 另带 `--expected-stage <当前阶段>`。固定读取到的值再提交；旧请求被拒绝后先核对状态，不自动替换参数重复推进。

## 完成时写入失败或会话中断

恢复原 issue/run/op 与原请求，先查看 operation 的已完成步骤和回执。release/clean 中断不解除占用；completed 不回退，正式档案不重写。退出成功后才允许下一任务，不能手改 current 或换操作编号绕过恢复。

## 再次接管已接管任务

不要覆盖当前任务。先回读 current/op，恢复同一 run 和未完成操作；不继续时明确归档清理，再接新任务。已完成任务由研发释放；任务 clean 不修改 Jira，station purge 不能替代任务退出。

## 完整工程准备失败

takeover 按 Profile 准备全部独立 source；核对 Git 权限、catalog origin、版本和既有目录洁净度。恢复同一操作，不删未知残留或重新创建 run。目录复用见[工位源码与材料](station-materials.md)。
