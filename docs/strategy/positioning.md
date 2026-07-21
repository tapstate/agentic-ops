# AgenticOps 目标定位

## 1. 一句话定位

AgenticOps 是面向研发流程的 AI 执行控制体系，用于让 AIAgent 在现有 Jira-centered 研发体系中可控地接管任务、完成开发、运行验证并回写证据。

## 2. AgenticOps 是什么

AgenticOps 是：

- AI 员工在研发流程中工作的执行体系。
- 面向研发 owner 的 AI 员工操作入口。
- 面向 AIAgent 的工作手册、操作契约和工具边界。
- 面向团队流程的 workflow profile。
- 面向任务执行的 evidence 和 feedback 闭环。

## 3. AgenticOps 不是什么

AgenticOps 不是：

- 新 Jira。
- 新 DevOps 平台。
- 全自动研发机器人。
- 单纯 prompt 集合。
- 脱离研发 owner 的自动任务分派系统。
- 脱离 Jira / Git / GitHub 的新事实源。

## 4. 主要用户

第一阶段主要用户是研发 owner。

研发 owner 使用 AgenticOps：

- 安装全局工具和手册。
- 初始化项目 AI 工作空间。
- 初始化 AIAgent 能力。
- 拉取自己名下 Jira 待办。
- 授权 AI 员工接管一个 issue。
- 确认 AI 员工完成的本地变更。
- 授权 push / PR。
- 查看每日反馈报告。

## 5. 项目价值

AgenticOps 的价值：

- 让 AI 员工从临时聊天助手变成流程内可管理执行状态。
- 保留研发 owner 和 reviewer 的责任边界。
- 让 AI 执行过程有可追踪证据链。
- 让失败、阻塞和人工确认点可被反馈分析。
- 用项目规则、AI 工作规则和 Operation Contract 减少幻觉。
- 用 Go CLI Runtime 让关键动作可检查、可拒绝、可审计，并便于向全公司研发稳定分发。

## 6. 第一阶段结果

第一阶段完成后，应能解释并演示这条链路：

```text
安装 AgenticOps
-> 初始化项目 AI 工作空间
-> 初始化 AIAgent 能力
-> 拉取 Jira 待办
-> 接管新任务
-> 本地开发和验证
-> 回写 evidence
-> 人工确认 push / PR
-> 上报每日工作日志
-> 生成改进建议
```

当前阶段只落文档和设计，不实现代码。
