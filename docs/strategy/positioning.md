# AgenticOps 目标定位

## 1. 一句话定位

AgenticOps 是把公司事务处理方式沉淀成 AI 可执行标准流程的 AI 执行控制体系。

第一阶段先落地研发 Jira 任务：帮助研发操作 AIAgent 从 Jira 接管任务到完成任务。它让不同任务按各自需要的流程推进，让执行过程留下可恢复、可复盘的记录，并把关键状态、关键信息和证据回写到 Jira、PR 或项目 AI 工作空间，用于后续分析和优化。

## 2. AgenticOps 是什么

AgenticOps 是：

- AI 员工在研发流程中工作的执行体系。
- 面向研发 owner 的 AI 员工操作入口。
- 面向 AIAgent 的工作手册、操作契约和工具边界。
- 面向团队流程的 workflow profile。
- 面向不同任务类型的受控流程入口。
- 面向标准异常的自助修复、阻断和转人工机制。
- 面向任务执行的 evidence 和 feedback 闭环。

## 3. AgenticOps 不是什么

AgenticOps 不是：

- 新 Jira。
- 新 DevOps 平台。
- 全自动研发机器人。
- 单纯 prompt 集合。
- 脱离研发 owner 的自动任务分派系统。
- 脱离 Jira / Git / GitHub 的新事实源。
- 只靠培训员工记住流程的知识库。

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

- 把公司事务处理方式沉淀为 AI 可执行、可审计、可回滚的标准。
- 让 AI 员工从临时聊天助手变成流程内可管理执行状态。
- 保留研发 owner 和 reviewer 的责任边界。
- 让 AI 执行过程有可追踪证据链。
- 让不同任务按匹配的流程推进，而不是把所有任务压成同一条固定流程。
- 让关键状态和信息及时回写，避免任务进展只停留在聊天上下文里。
- 让失败、阻塞和人工确认点可被反馈分析。
- 让标准流程出问题时，AIAgent 能优先自助处理；不能安全处理时，明确阻断、记录原因并转人工。
- 让研发员工经过简单培训即可使用标准流程，而不需要记住所有细节。
- 用项目规则、AI 工作规则和 Operation Contract 减少幻觉。
- 用 Go CLI Runtime 让关键动作可检查、可拒绝、可审计，并便于向全公司研发稳定分发。

AgenticOps 中的关键标准资产包括：

- AI 员工手册：定义 AIAgent 如何接任务、何时停止、如何回写证据。
- Operation Contract：定义每类操作的输入、输出、失败码和副作用。
- Workflow Profile：定义不同项目和系统流程如何映射到标准操作。
- Policy / Gate：定义哪些动作必须人工确认，哪些动作禁止自动执行。
- Runbook：定义标准流程出问题时如何排查、修复、阻断或转人工。
- Templates：定义 Jira comment、PR evidence、阻塞说明、补卡说明等标准输出。

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

当前仓库已进入第一阶段本地实现。`agent-task-ops` Go CLI 已支持 fake Jira、本地工作空间、运行资产安装、evidence 写入、feedback report 和本地 release 打包的最小闭环；真实 Jira / GitHub 写操作、push、PR、merge 和发布仍未接入。
