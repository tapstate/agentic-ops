# AgenticOps 文档总纲

本文是现役人读文档的结构入口。新建或调整文档时，先在本页或对应主题的子级总纲明确目标、范围、层级、职责和导航关系；再细化正文。仅当文档过长，或稳定内容被多个页面复用时，才拆分子文档。

| 主题 | 总纲与权威文档 | 适用内容 |
|---|---|---|
| 产品定位与架构 | [项目目标](strategy/project-goals.md)、[v1 工程架构](architecture/agenticops-v1-architecture.md)、[术语表](glossary.md) | 产品边界、分层、稳定术语、仅拦截明确协作/控制面操作的 Hook 与 Agent 原生权限责任及迁移准绳 |
| 使用与维护 | [首次使用指引](usage-guide.md)、[必需 MCP 配置](usage/mcp-setup.md)、[Agent引导安装指引](usage/agent-guided-install.md)、[任务授权指引](usage/task-authorization.md)、[扩展使用索引](usage/README.md)、[维护指引](maintenance-guide.md)、[Skill 维护规范](skill-maintenance.md) | 首次安装到接管任务、Claude Code/Codex 的必需 Jira MCP 接线、GitHub 工具的自主选择边界、由 AI Agent 在空工作空间完成安装与初始化、脚本加载任务、准入、受控基线与实施授权、可选安装与 Source Pool 预下载、更新和回退、项目工作空间根 `./agenticops` 薄入口、受控仓库准备、当前工作空间会话中的任务执行上下文、任务恢复与精确清理、Skill 分类与发现接线、证据标签，以及日常运行和维护 |
| 安全与验证 | [权限与安全边界](security/permissions.md)、[Git SSH 授权指引](security/git-ssh-access.md)、[Claude 端到端验证](testing/e2e-claude.md)、[Codex 端到端验证](testing/e2e-codex.md) | 凭证、以工作空间为单位的 Agent 文件系统授权、宽门禁下任务级 Git/Gate 与分支相关 GitHub 写操作边界、访问诊断和端到端验收 |
| 产品合同 | [v1 用户故事总纲](user-stories/v1/README.md) | 稳定的产品能力、保护行为和验收证据 |

文档链接权威来源而不重复维护相同规则。具体工作项、进度、阻塞和验收由 Jira 管理，不在本树新增平行执行计划。

缺陷质量协作属于“使用与维护”主题：[质量检查与证据](usage/quality-checkpoints.md)说明检查点、单用例检查项、用户处置、非阻断 Jira 状态同步、PR Ready 核对和回写恢复；项目验证方式以 `projects/<project>/quality.json` 为准，数据结构以 `contracts/quality-*.schema.json` 为准，不在使用文档另设任务阶段。

旧版 AgenticOps 的设计、合同和操作说明以 Git Tag `v0.7` 为准，不在 v1 现役文档树保留重复版本。
