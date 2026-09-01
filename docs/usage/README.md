# AgenticOps 扩展使用

本目录保存首次使用之外的稳定操作说明。[首次使用指引](../usage-guide.md)只覆盖默认路径；遇到不同环境或需要调整默认值时，再按下面的场景进入对应文档。

| 场景 | 文档 | 何时使用 |
|---|---|---|
| 默认安装 | [Git SSH 安装](git-ssh-install.md) | 已配置 SSH，按受信 `main` 安装使用工作面 |
| 无法使用 Git SSH | [gh 一键安装](gh-one-click-install.md) | 通过 GitHub CLI 登录并安装 |
| 改变业务仓库来源 | [自定义 Source Pool](custom-source-pool.md) | 要复用已有仓库、隔离缓存或改为手动供给 |
| 日常维护安装 | [更新与回退](update-and-rollback.md) | 安装已存在、更新失败、接线漂移或需要回退 |
| 排障与恢复 | [常见问题](faq.md) | 安装、启动、Hook 或已接管任务出现问题 |

这些文档只说明使用工作面。维护 AgenticOps 源码、测试或发布请使用[维护指引](../maintenance-guide.md)。
