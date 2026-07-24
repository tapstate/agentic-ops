# 研发负责人上手

本文面向使用 AgenticOps 指挥 AIAgent 处理日常 Jira 任务的研发负责人。研发负责人不需要理解 AgenticOps 源码结构，重点是完成安装、项目 AI 工作空间初始化，并让 AIAgent 按标准资产执行。

## 第一次使用

推荐路径：

1. 阅读 [AI 员工手册](../../handbooks/ai-employee-handbook.md)，理解研发负责人和 AIAgent 的协作方式。
2. 阅读 [端到端演示](../examples/end-to-end-demo.md)，理解从安装到任务审计的完整流程。
3. 在项目 AI 工作空间目录内执行 `workspace init`，不要在 AgenticOps 源头仓库或 `~/.agentic-ops` 里初始化业务项目。
4. 指示 AIAgent 初始化 AgenticOps 能力，并要求它读取 AI 资产入口。

## 工作空间初始化

`~/.agentic-ops` 是全局安装目录；项目 AI 工作空间是具体业务项目的运行目录，例如 `tapstate/` 或 `tapdata/`。

初始化时需要明确：

- Jira 用户。
- Jira 项目。
- 项目 AI 工作空间目录。
- Jira 空间到代码仓库的映射。
- 本地源码根目录。
- 工作流配置。

示例命令以当前安装版本输出为准：

```sh
agentic-cli workspace init --workspace tapdata --jira-user <jira-user> --jira-project TAP
```

## 指挥 AIAgent

研发负责人可以用自然语言给 AIAgent 下达任务：

```text
初始化 AgenticOps 能力，工作空间是 tapdata。
列出我名下可以接管的 Jira 任务。
接管 TAP-123，并先说明计划、验证方式和风险点。
回写本次执行证据。
提交 TAP-123 本次执行的任务审计记录。
```

AIAgent 应读取 [AI 资产入口](../../ai-assets/README.md)，再按 AI 员工手册、操作契约、工作流配置、策略和模板推进。研发负责人不应要求 AIAgent 依赖临场聊天上下文猜流程。

## 人工确认点

以下动作必须由研发负责人或对应专业角色确认后才能继续：

- 真实 Jira 写操作。
- Git 推送。
- 创建或更新拉取请求。
- 合并。
- 发布。
- 需求范围、验收标准、目标仓库或风险边界发生变化。

任务完成、阻塞或交接时，AIAgent 必须提交任务级审计记录。本地反馈报告只能用于按需分析和改进建议，不能替代 Jira、审计服务或目标仓库中的任务事实记录。
