# 真实全链路测试环境与本地样例配置设计

## 1. 目的

本文区分两类测试输入：

- 正式全链路使用用户逐项确认的真实 Jira、真实业务工作空间、真实仓库和真实 GitHub 权限，交付到 PR 审查。
- `offline_fake` 使用隔离本地 fixture 回归 Bootstrap、Runtime 和数据合同，不证明真实任务完成。

仓库样例值只能帮助识别配置结构，不能成为真实执行默认值，也不能由 AI 从本机其它工作空间自动补全。

## 2. 工作面与交接

maintainer 通过 `$run-local-integration` 准备 manifest、运行离线回归并只读验收；developer 通过 `$run-task-to-pr-test` 执行真实业务任务并生成结果包。两面只通过这两个显式工件交接：

```text
maintainer 确认 manifest
-> developer 执行真实任务并产出原始审计/结果包
-> maintainer 只读验收
```

maintainer 不读取业务凭据或执行业务开发；developer 不加载源头维护规则、不修改 AgenticOps 源码。结果包不得反向携带业务 token、私钥、完整 Jira 描述或原始敏感日志。

## 3. 正式测试输入规则

用户必须在执行前明确确认：

- Jira key、站点、Project、Project Profile 与当前工作空间唯一账户；
- AgenticOps ref、业务工作空间、业务仓库与基线/任务/目标分支；
- 任务范围、禁止范围、验证方式与 PR 审查终点；
- Jira、Git、GitHub 的允许操作、授权来源、清理和证据边界；
- manifest 的规范化内容摘要。

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
