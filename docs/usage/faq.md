# 常见问题

## `gh auth status` 或安装命令失败

确认 Git、Python 3.9+ 和 GitHub CLI 已安装；再检查当前 GitHub 账号是否能读取 `tapstate/agentic-ops`。需要重新登录时按[gh 一键安装](gh-one-click-install.md)操作。没有 `gh` 时使用[Git SSH 安装](git-ssh-install.md)。

## 安装目录已存在

安装程序不会覆盖 `~/.agentic-ops`。这是已有安装时使用[更新与回退](update-and-rollback.md)的 `update`，不是重复运行安装。

## `doctor` 报接线漂移或 Agent 无法启动

先运行 `~/.agentic-ops/agenticops doctor --workspace <项目工作空间>`；确认后使用同一路径的 `repair --workspace <项目工作空间>`。不要手改 `.agenticops/workspace.json`、Hook 或生成的 Skill 链接。

## Codex 首次启动提示 Hook

在 Codex 中按 `/hooks` 审核并信任本项目 Hook。Hook 会在副作用前请求确认或停止不可信操作；它不是可跳过的提示。

## 再次接管已接管任务

不要重新初始化同一任务。Agent 会展示已有 `run_id`，由你选择继续现有现场，或先清理洁净 worktree 后按精确 `run_id` 重做。任务级清理只影响本地状态，不修改 Jira；不要用工作空间级 `purge` 代替它。

## 任务准备被 Source Pool 阻断

默认 `auto-clone` 会按项目目录下载缺失仓库。若仍被阻断，确认 Git SSH 权限、项目仓库映射和 Pool 目录可读写；已有主工作树时还需满足 origin、基线分支和洁净度要求。需要修改 Pool 或改为手动供给时参阅[自定义 Source Pool](custom-source-pool.md)。
