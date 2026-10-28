# 常见问题

## 工位接管或执行任务遇到问题，需要反馈

所有项目共用的 `ao-feedback` 上报入口、问题总结和必要复现资料见[问题反馈流程](problem-feedback.md)，维护者通过 AO 项目问题列表统一处理。该页区分目标技能与当前能力；仅请求排障不自动上传现场，明确要求反馈后才按授权提交。工位不能启动、接管未成功或归因未明时，也可以先整理已有现象并反馈。

## `gh auth status` 或安装命令失败

确认 Git、Python 3.12.x 和 GitHub CLI 已安装；再检查当前 GitHub 账号是否能读取 `tapstate/agentic-ops`。需要重新登录时按[gh 一键安装](gh-one-click-install.md)操作。没有 `gh` 时使用[Git SSH 安装](git-ssh-install.md)。

## 安装目录已存在

安装程序不会覆盖 `~/.agentic-ops`。这是已有安装时使用[更新与回退](update-and-rollback.md)的 `update`，不是重复运行安装。

## `doctor` 报接线漂移或 Agent 无法启动

先运行 `~/.agentic-ops/agenticops station doctor --station <项目工位>`；确认后使用同一路径的 `repair --station <项目工位>`。不要手改 `.agenticops/station.json`、Hook 或生成的 Skill 链接。

## 提示旧 Hook 或工位代际不兼容

新工位不生成通用 Agent Hook。先按[更新与回退](update-and-rollback.md)核实实际兼容模式：旧 epoch 模式跨代际仍须退出重建，新版功能校验模式按所属入口核验数据；不能仅凭 epoch 不同或相同推定任务可执行。

在受支持的工位中，检测到托管旧 Hook 时，start、普通 repair 和 doctor 会报告迁移范围并保留文件。核对归属及调用不再逐次受检的边界后，从该工位执行：

```sh
./agenticops station repair --accept-checkpoint-migration
```

此操作仅移除归属哈希匹配的旧托管 Hook，并刷新指引和初始化清单；任务、run、历史确认和证据保留，是否仍有效由当前检查点判断。任一旧 Hook 已被修改时停止，不覆盖用户内容。退役文件可从 Git 历史查阅，但当前安装已无其执行依赖，不能只复制旧模板来恢复拦截；需要回退时仍遵守产品版本与工位 epoch 的完整检查。自定义或全局 Hook 不受本次迁移管理。

迁移后 Git/Jira/PR 使用平台原生权限；方案确认在 Workflow 检查点重新校验，Jira 同步不再保证强制单次调用。旧 Agent 会话应结束，再从工位启动，使接线与当前版本一致。

## 状态命令提示缺少 run 或阶段

从 `task.py status --issue-key <key>` 或 `next` 读取当前 run 与阶段。状态写命令须带 `--expected-run-id <run>`，advance 另带 `--expected-stage <当前阶段>`。固定读取到的值再提交；旧请求被拒绝后先核对状态，不自动替换参数重复推进。

## 完成时写入失败或会话中断

恢复原 issue/run/op 与原请求，按[原清理操作的恢复](task-authorization.md#原清理操作的恢复)回读已完成步骤和残留。任务可能已解绑但仍在 finalizing 收尾，须待清理完成并释放执行占用后再接下一任务；不能仅凭 current 为空推定成功。completed 不回退，不手改状态或换操作编号绕过恢复。

## 再次接管已接管任务

不要覆盖当前任务。先回读 current/op，恢复同一 run 和未完成操作；不继续时先核实材料保全或明确放弃，再按已确认范围清理。需要持久保存进度时独立[归档](task-archive-and-resume.md#持久接续点操作)，归档不是清理的强制前置。任务清理不修改 Jira，station purge 不能替代任务退出。

## 完整工程准备失败

takeover 按 Profile 准备全部独立 source；核对 Git 权限、catalog origin、版本和既有目录洁净度。恢复同一操作，不删未知残留或重新创建 run。目录复用见[工位源码与材料](station-materials.md)。
