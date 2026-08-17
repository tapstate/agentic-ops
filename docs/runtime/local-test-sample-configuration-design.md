# 真实全链路测试环境与本地样例配置设计

## 1. 目的

本文区分两类测试输入：

- 正式全链路从一次性业务工作空间配置、Project Profile、Jira 卡片、Runtime 探测和人工审查计划组合完整 manifest，交付到 PR 审查。
- `offline_fake` 使用隔离本地 fixture 回归 Bootstrap、Runtime 和数据合同，不证明真实任务完成。

仓库样例值只能帮助识别配置结构，不能成为真实执行默认值，也不能由 AI 从本机其它工作空间自动补全。

## 2. 工作面与交接

maintainer 通过 `$test-task-to-pr-e2e` 做无副作用能力预检、创建隔离 developer 工作空间并启动独立 developer/reviewer AI，通过 `$run-local-integration` 运行离线回归和只读验收；developer 通过 `$run-task-to-pr-test` 执行真实业务任务并生成结果包。跨面只通过显式 manifest、脱敏结果包和相互隔离的进程边界交接：

```text
maintainer 确认 manifest
-> maintainer 启动隔离 developer AI，但不在自身上下文执行业务动作
-> developer 执行真实任务并产出原始审计/结果包
-> maintainer 只读验收
```

maintainer 不读取业务凭据或执行业务开发；developer 不加载源头维护规则、不修改 AgenticOps 源码。结果包不得反向携带业务 token、私钥、完整 Jira 描述或原始敏感日志。

正式 Skill 必须先调用 `ao-maint integration preflight-task-to-pr-e2e <KEY>`。信息准入摘要与方案分级已经由 `ao-work task intake assess|confirm` 和 `ao-work task solution classify|confirm` 提供内容摘要、变更重算、固定分级和单次重试门禁。当前能力目录仍缺正式接管、受控提交、任务分支推送和 PR 创建四个 `ao-work` 原子操作，所以预检继续在任何外部访问前失败关闭；这不是配置缺失，也不能通过增加用户输入解决。只有这四项能力具有所有权、保护分支/非快进门禁和写后回读后，才允许进入后续真实执行。

## 3. 正式测试输入规则

正式 E2E 的测试身份由一次性全链路配置确定：`agent_id`、Project Profile 和预期确认人不从每任务参数或本机身份推断。业务工作空间初始化时另外确认唯一 Jira 账户、业务仓库和执行身份；以后不按任务重复配置。Project Profile 提供站点、Project、状态/字段映射、默认仓库和项目策略；Jira 卡片提供任务事实；Runtime 生成 run、时间和摘要。

每个任务先由 AI 分析缺项，再从 Jira、Project Profile、业务源码和 Runtime 回读中自动补全，并将“原始事实、补全值、来源、仍缺项、假设和影响”作为一份完整准入摘要交给用户确认，不能只展示一个摘要 ID。确认后再形成方案，并分为 L1 直接实施、L2 确认后实施、L3 先修改设计并重新分析、L4 停止升级。每个任务只要求人工确认：

- AI 汇总的计划、包含/排除范围、任务分支和验证方式；
- Jira、Git、GitHub 的本次允许操作与 PR 审查终点；
- 与批准计划摘要绑定的任务级授权；
- 范围变化或高风险动作的新决策。

每个 Runtime 环节必须基于实际返回状态给出唯一结构化下一动作。失败只在 `retry_gate.allowed=true` 时可以对同一 `retry_key` 再试一次，且必须先回读状态、改变输入并记录 retry 事件；相同输入循环、没有回读的重试或重试耗尽后继续均必须停止。

完整 manifest 继续保留所有机器校验字段，但不作为用户逐项填写的配置界面。

缺项或摘要变化时不得执行副作用。凭据只通过当前业务工作空间认可的安全入口配置，不放进 manifest 或结果包。

正式测试终点固定为真实 PR 已创建或更新并等待审查；明确排除 merge、Jira Done、release、tag、保护分支直推和历史改写。

## 4. FE/TM 本地样例

Tapdata FE 与 TM 样例保留以下内部一致的参考值：

- MongoDB 样例连接统一为 `mongodb://mongo/tapdata`；
- TM 样例不设置 `TAPDATA_TM_ID`；
- FE 与 TM 均使用 `app_type=DAAS`。

这些值不是正式测试默认配置。启动前必须展示并逐项确认：

- FE 与 TM 是否连接本次测试指定的同一 MongoDB；
- FE `backend_url` 是否指向本次启动的 TM API；
- TM HTTP/WebSocket 端口是否可用且隔离；
- 工作目录、数据清理和证据保留范围是否匹配 manifest。

未确认不得启动 FE 或 TM。确认只对当前 manifest 与任务有效，不能反向改写仓库样例、Project Profile 或共享策略。

## 5. 结果与复盘合同

developer 原始结果包必须同时包含交付事实和过程质量事实：

- Jira、业务 Git、GitHub PR/CI 的稳定引用与回读；
- 修改、验证、授权、残留风险和 PR 审查状态；
- 人工干预、自动化失败、重复动作、等待、输出质量问题和不合理点；
- Skill/Runtime/脚本、AI 判断、项目工具与人工动作的责任边界；
- 问题证据、影响、根因假设、复现条件、耗时影响；
- 按收益、风险、复现频率排序的优化候选及建议载体。

maintainer 只读核对完整性与边界，不重写原始审计。经人工确认的候选另行进入维护流程，不能在业务测试中直接修改 AgenticOps 标准。

## 6. 验证

- 检查正式路径由 developer 工作面执行并停在 PR 审查。
- 检查 maintainer 只准备协议、执行离线合同回归和只读验收。
- 检查 manifest/结果包是唯一跨面交接，且不包含敏感信息。
- 检查 merge、Jira Done、release、tag 和保护分支直推明确被禁止。
- 检查复盘覆盖全部摩擦、问题、不合理点和优化候选。
- 检查 FE/TM 样例满足 MongoDB、`TAPDATA_TM_ID`、`app_type` 约定，并声明运行前确认门禁。
