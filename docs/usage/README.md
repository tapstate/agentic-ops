# AgenticOps 扩展使用

质量检查与证据文档统一说明本地有效事实、独立实施分支、简洁 Jira 评论和同步失败恢复；所有阶段的同步警告在 PR 后总结统一展示。

本目录保存首次使用之外的稳定操作说明。[首次使用指引](../usage-guide.md)只覆盖默认路径；遇到不同环境或需要调整默认值时，再按下面的场景进入对应文档。

[任务授权指引](task-authorization.md)同时负责已有分支/PR 续办、远端分支的精确 Head 获取、历史基线与版本规划的独立核验及新分支重做：说明何时恢复原 run、何时 cleanup/reset、如何更新未准备的仓库登记；Git 成果与旧验证证据的边界以架构和质量文档为准。

| 场景 | 文档 | 何时使用 |
|---|---|---|
| 默认安装 | [Git SSH 安装](git-ssh-install.md) | 已配置 SSH，按受信 `main` 安装使用工作面 |
| 配置必需 MCP | [必需 MCP 配置](mcp-setup.md) | 首次使用 Jira 事实时，连接 Jira/Atlassian |
| 让 AI Agent 安装 | [Agent引导安装指引](agent-guided-install.md) | 从空目录启动 Agent，由它依据现役安装文档安装并初始化项目工作空间 |
| 无法使用 Git SSH | [gh 一键安装](gh-one-click-install.md) | 通过 GitHub CLI 登录并安装 |
| 改变业务仓库来源或预热缓存 | [自定义 Source Pool](custom-source-pool.md) | 要复用已有仓库、隔离缓存、预下载项目目录中的仓库或改为手动供给 |
| 脚本接管与授权 | [任务授权指引](task-authorization.md) | 从空任务列表加载 Jira 任务，完成准入、受控基线、方案确认与实施授权 |
| 缺陷质量协作 | [质量检查与证据](quality-checkpoints.md) | 当前 run 的交互文件分配、影响版本与优先修复线、稳定方案确认、精确代码证据、从 Jira「已链接工作项」识别 Test 用例、接管/验收节点的非阻断 Jira 状态同步、PR Ready 三类核对和逐检查点 Jira 回读；复用任务授权及阶段，项目标准从 Project 配置读取 |
| 日常维护安装 | [更新与回退](update-and-rollback.md) | 安装已存在、更新失败、接线漂移或需要回退 |
| 排障与恢复 | [常见问题](faq.md) | 安装、启动、Hook 或已接管任务出现问题 |

[常见问题](faq.md)负责旧 Hook 显式迁移与失败恢复；[任务授权指引](task-authorization.md)负责当前 run/阶段绑定和检查点确认；[质量检查与证据](quality-checkpoints.md)负责当前 run 的交互文件和原生 Jira 调用后的证据回读；[更新与回退](update-and-rollback.md)负责跨工作空间状态代际的升级前检查与任务清理引导。外部操作不再由通用 Hook 拦截。

授权到期但方案未变时，按[任务授权指引](task-authorization.md)显式续签，保留当前 run 和证据；完成操作中断时，按[常见问题](faq.md)重试原请求，收敛已提交的完成状态，不重新验收或重置任务。

这些文档只说明使用工作面。维护 AgenticOps 源码、测试或发布请使用[维护指引](../maintenance-guide.md)。
