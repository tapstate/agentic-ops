# v1 用户故事总纲

本页是 v1 产品故事的结构入口。用户故事记录稳定的产品能力、保护行为和验收证据，
不记录 Jira 工作项的进度、阻塞或执行计划。

产品定位、架构边界和术语分别以[项目目标](../../strategy/project-goals.md)、
[v1 工程架构](../../architecture/agenticops-v1-architecture.md)和
[术语表](../../glossary.md)为准；本目录不重复定义这些规则。

| 故事 | 职责 |
|---|---|
| [PROD-001 安装并接入多种 Agent](prod-001-install-and-connect-agent.md) | 产品根目录、工作空间与多 Agent 接线 |
| [PROD-002 在副作用前执行统一门禁](prod-002-policy-gate.md) | 标准操作、授权与三态门禁 |
| [PROD-003 推进并恢复多仓库研发任务](prod-003-multi-repository-task.md) | 多任务、多仓库隔离、恢复与任务证据 |
| [PROD-004 低成本适配产品项目](prod-004-adapt-project.md) | 项目 Profile、准入规则与 Runbook 适配 |
| [INT-001 审查并发布 AgenticOps](int-001-release-governance.md) | 仓库内部审查、发布与版本治理 |

新增或调整故事前，先更新本总纲中的职责和导航。只有故事正文过长，或有稳定内容需要
被多个故事复用时，才拆分子文档；拆分后仍由本页说明边界和入口。
