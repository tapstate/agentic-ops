# AgenticOps 文档总纲

本文是现役人读文档的结构入口。新建或调整文档时，先在本页或对应主题的子级总纲
明确目标、范围、层级、职责和导航关系；再细化正文。仅当文档过长，或稳定内容被多个
页面复用时，才拆分子文档。

| 主题 | 总纲与权威文档 | 适用内容 |
|---|---|---|
| 产品定位与架构 | [项目目标](strategy/project-goals.md)、[v1 工程架构](architecture/agenticops-v1-architecture.md)、[术语表](glossary.md) | 产品边界、分层、稳定术语和迁移准绳 |
| 使用与维护 | [使用指引](usage-guide.md)、[维护指引](maintenance-guide.md) | 产品安装、Source Pool、项目工作空间初始化、任务 worktree、由工作空间薄入口启动 Agent，以及日常运行和维护 |
| 安全与验证 | [权限与安全边界](security/permissions.md)、[Git SSH 授权指引](security/git-ssh-access.md)、[Claude 端到端验证](testing/e2e-claude.md)、[Codex 端到端验证](testing/e2e-codex.md) | 凭证、Agent 文件系统授权、外部系统边界、访问诊断和端到端验收 |
| 产品合同 | [v1 用户故事总纲](user-stories/v1/README.md) | 稳定的产品能力、保护行为和验收证据 |

文档链接权威来源而不重复维护相同规则。具体工作项、进度、阻塞和验收由 Jira 管理，
不在本树新增平行执行计划。

旧版 AgenticOps 的设计、合同和操作说明以 Git Tag `v0.7` 为准，不在 v1 现役
文档树保留重复版本。
