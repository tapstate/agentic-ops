# 常见问题

## `gh auth status` 或安装命令失败

确认 Git、Python 3.9+ 和 GitHub CLI 已安装；再检查当前 GitHub 账号是否能读取 `tapstate/agentic-ops`。需要重新登录时按[gh 一键安装](gh-one-click-install.md)操作。没有 `gh` 时使用[Git SSH 安装](git-ssh-install.md)。

## 安装目录已存在

安装程序不会覆盖 `~/.agentic-ops`。这是已有安装时使用[更新与回退](update-and-rollback.md)的 `update`，不是重复运行安装。

## `doctor` 报接线漂移或 Agent 无法启动

先运行 `~/.agentic-ops/agenticops doctor --workspace <项目工作空间>`；确认后使用同一路径的 `repair --workspace <项目工作空间>`。不要手改 `.agenticops/workspace.json`、Hook 或生成的 Skill 链接。

## 升级后提示流程检查点迁移

新工作空间不生成通用 Agent Hook。已有工作空间检测到托管的旧 Hook 时，start、普通 repair 和 doctor 会报告迁移范围并保留文件。核对后执行：

```sh
./agenticops repair --accept-checkpoint-migration
```

此操作仅移除归属哈希匹配的旧托管 Hook，并刷新指引和初始化清单；任务、run、历史确认和证据保留。任一旧 Hook 已被修改时停止，不覆盖用户内容。移除的文件是可再生产物，可从迁移前产品版本的模板恢复；恢复旧控制前应明确决定回退并核对配置。自定义或全局 Hook 不受本次迁移管理。

迁移后 Git/Jira/PR 使用平台原生权限；方案确认在 Workflow 检查点重新校验，Jira 同步不再保证强制单次调用。旧 Agent 会话应结束，再从工作空间启动，使接线与当前版本一致。

## 状态命令提示缺少 run 或阶段

从 `task.py status --issue-key <key>` 或 `next` 读取当前 run 与阶段。状态写命令须带 `--expected-run-id <run>`，advance 另带 `--expected-stage <当前阶段>`。固定读取到的值再提交；旧请求被拒绝后先核对状态，不自动替换参数重复推进。

## 完成时写入失败或会话中断

使用原来的 issue、run 和 `--expected-stage ci_validation` 重试最后一次 `advance`。Workflow 先检查验收条件，再原子保存 `completed` 阶段，最后撤销授权并更新注册状态。保存阶段前失败时，授权保持原样，重试重新检查；保存阶段后失败时，重试只幂等收敛撤销和注册，不重做验收或追加完成事件。即使原授权随后到期，也不会阻断已提交终态的收敛。只有收敛完成命令才返回成功；不要手改状态或为此 reset。

## 再次接管已接管任务

不要重新初始化同一任务。Agent 会展示已有 `run_id`，由你选择继续现有现场，或先清理洁净 worktree 后按精确 `run_id` 重做。任务级清理只影响本地状态，不修改 Jira；不要用工作空间级 `purge` 代替它。

## 任务准备被 Source Pool 阻断

默认 `auto-clone` 会按项目目录下载缺失仓库。若仍被阻断，确认 Git SSH 权限、项目仓库映射和 Pool 目录可读写；已有主工作树时还需满足 origin、基线分支和洁净度要求。需要修改 Pool 或改为手动供给时参阅[自定义 Source Pool](custom-source-pool.md)。
