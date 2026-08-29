# AgenticOps

AgenticOps 是公司级 Agentic 研发基础设施，为 Codex、Claude 及后续 Agent 提供统一的
研发规则、操作门禁、任务恢复和证据边界。Agent 仍是执行主体；AgenticOps 负责把平台
事件转换为标准操作，并在副作用发生前给出允许、人工确认或拒绝的判定。

它不替代 Jira、Git、GitHub、CI 或人工审查：Jira 是任务事实源，Git 是代码事实源，
GitHub PR/CI 是审查与检查事实源。项目工作空间只保存初始化信息，以及按任务隔离的
本地运行、授权、恢复和门禁事件。

## 解决的问题

- 让不同 Agent 在同一套标准操作、授权和策略下执行研发任务，而不是各自维护流程规则。
- 在 Jira、Git、GitHub 等外部副作用发生前统一核验任务、仓库、分支、范围和授权。
- 将项目差异与平台差异隔离，避免把某个项目或某种 Agent 的特例写入公共内核。
- 保留可恢复的任务状态与验证证据；遇到事实不可信、权限不足或外部写入结果不明时失败关闭。

## 产品架构

```text
Agent 原生事件
    │
    ▼
Agent / Tool Adapter → 标准请求 → Gate Core + Policy → 标准判定
                                      │
                              Workflow / Project
```

| 层 | 责任 |
|---|---|
| `contracts/` | 版本化标准请求、判定、操作词表和 Manifest |
| `gate/` 与 `policies/` | 平台无关的上下文、策略和授权判定 |
| `workflow/` | 阶段、授权、CI、证据和恢复等确定性状态逻辑 |
| `projects/<project>/` | Jira、仓库分支、准入、验证和 Runbook 等项目差异 |
| `adapters/` | Agent 与工具协议的无状态转换 |
| `bootstrap/` | 产品根目录、安装、更新和项目工作空间接线 |

完整的层级边界、产品根目录与工作空间模型见 [v1 工程架构](docs/architecture/agenticops-v1-architecture.md)。

## 快速导航

### 维护者

维护 AgenticOps 的产品、规则、适配和发布链路；在源码仓库的 `develop` 分支工作。

- [维护指引](docs/maintenance-guide.md)
- [项目目标](docs/strategy/project-goals.md)

### 使用者

在业务项目工作空间使用已发布的产品，初始化 Agent 并处理 Jira 任务。

- [使用指引](docs/usage-guide.md)

## 文档与合同

[文档总纲](docs/README.md) 是现役人读文档的结构入口，按主题导航产品定位、架构、
使用维护、安全验证和产品合同。v1 的稳定能力、保护行为与验收证据集中在
[v1 用户故事总纲](docs/user-stories/v1/README.md)；具体工作项、进度、阻塞和验收
始终在 Jira 管理，不在仓库维护平行执行计划。

旧版 AgenticOps 的设计、合同和操作说明固定在 Git Tag `v0.7`；现役 v1.x 架构在
`develop` 分支演进。
