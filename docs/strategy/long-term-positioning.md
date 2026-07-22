# AgenticOps 长期定位

## 1. 长期目标

AgenticOps 首先落地在研发 Jira 任务处理场景。长期目标是把公司事务处理标准化、流程化，让 AI 在遇到问题时能按公司当前规范处理，并通过执行记录持续沉淀和优化规范。

AgenticOps 不要求人记住每一种事务应该怎么处理，而是把处理方式沉淀成可执行、可审计、可回滚的规范资产。

更具体地说，AgenticOps 需要回答四个问题：

- 怎么形成标准：从真实任务、失败日志、人工介入点和重复问题中沉淀。
- 形成什么样的标准：形成 AI 员工手册、operation contracts、workflow profiles、policies、runbooks 和 templates。
- 标准流程出问题怎么办：AIAgent 优先按 runbook 自助处理；不能安全处理时阻断、记录原因并转人工。
- 员工如何低培训上岗：研发 owner 使用安装后的 `agent-task-ops` 和标准操作入口，不需要理解源码、编译环境或每个流程细节。

## 2. 公司事务处理模型

无论事务来自 Jira、PR、CI、客户反馈、线上问题还是内部流程异常，AIAgent 都应先进入统一判断模型：

```text
识别事务类型
-> 判断当前阶段
-> 选择 operation / runbook
-> 执行 gate
-> 处理事务
-> 记录过程
-> 回写关键状态和证据
-> 生成反馈
-> 提出规范改进建议
```

该模型继续使用第一阶段已经确认的字段：

- `task_type`
- `current_stage`
- `next_action`

AIAgent 不按固定角色工作，也不靠临场记忆猜流程。

## 3. 可执行规范资产

公司规范不应只存在于口头经验、聊天上下文或散落文档中。AgenticOps 应逐步把规范拆成可版本化资产：

```text
company-handbook/
  公司级原则、通用边界、敏感信息规则

operation-contracts/
  每类事务能做什么、输入输出是什么、失败码是什么

workflow-profiles/
  不同团队、不同项目、不同系统流程如何映射

policies/
  哪些动作必须人工确认，哪些动作禁止自动执行

templates/
  Jira comment、PR evidence、日报、复盘、升级说明

runbooks/
  问题发生后按什么步骤排查、处理、升级、回写
```

第一阶段不需要一次性实现全部目录。它们是长期信息架构方向，当前实现仍以研发 Jira 任务闭环为边界。

其中 `AgenticOps` 是项目和体系，`agent-task-ops` 是给研发 owner 和 AIAgent 使用的 CLI 二进制。除非问题来自 `agent-task-ops` 二进制逻辑错误，否则应优先通过标准资产自助修复、阻断或转人工。

## 4. 问题处理闭环

问题出现后，AIAgent 应按规范处理，而不是直接猜测或绕过流程。

标准流程出问题时，处理优先级是：

```text
检查当前 operation / runbook
-> 能按标准资产自助处理则处理
-> 需要补充 Jira 信息则阻断并输出补全模板
-> 需要调整 profile / policy / template 则生成改进建议
-> 存在风险、权限或标准冲突则转人工
-> 如果确认是 agent-task-ops 二进制逻辑错误，再进入 CLI 修复发布路径
```

示例：Jira 卡片缺少目标仓库。

```text
AIAgent 发现缺少 target_repo
-> 查询 operation contract
-> 停止接管
-> 输出 required_human_action
-> 生成 Jira 补充模板
-> 记录 missing_field 事件
-> feedback report 汇总同类问题
-> 提出改进 Jira 创建模板或字段校验的建议
```

这条链路的目标是让问题变成可分析、可治理的规范改进输入，而不是依赖某个人下次记得提醒。

## 5. 规范演进门禁

AgenticOps 可以帮助生成规范改进建议，但不能未经人工确认自动修改公司规范。

规范演进必须经过：

```text
Observation 观察
-> Proposal 改进建议
-> Review 人工确认
-> Accepted Change 合入规范
-> Release 同步给使用者
-> Effectiveness Check 检查问题是否减少
```

所有规范变更必须可追溯、可回滚，并能说明影响范围。

## 6. 第一阶段边界

当前阶段仍聚焦研发 Jira 任务：

- 安装 AgenticOps。
- 初始化项目 AI 工作空间。
- 初始化 AIAgent 能力。
- 从 Jira 接管任务。
- 记录执行过程。
- 回写 evidence。
- 生成 feedback report。

真实 Jira / GitHub 写操作、push、PR、merge、发布、公司级事务平台化和自动规范变更都不属于第一阶段当前实现范围。

## 7. 长期形态

AgenticOps 长期形态是公司事务的 AI 操作控制层。

它不替代 Jira、Confluence、GitHub、Slack、飞书等事实源，而是在事实源之上提供：

- 标准流程。
- AI 操作入口。
- 门禁控制。
- 执行记录。
- 证据回写。
- 反馈分析。
- 规范演进。
