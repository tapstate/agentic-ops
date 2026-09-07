# AgenticOps 标准契约

`contracts/` 是 Agent、Tool Adapter 与 Gate Core 之间的唯一协议事实源。

质量 receipt 增加 deferred 与可选 reason：表示已知未写入，必须说明原因；unknown 表示结果不明，不得盲目重发。同步状态不决定本地阶段通过。issue-versions 输入的 effective 保存 versions、execution_branch 和 proof，observed 保留初始 Jira 快照；版本无需 Jira ID，也不映射分支。旧事件按记录时规则重放，人读评论格式由当前 Project 配置决定。

- `gate-request.schema.json`：Adapter 交给 Gate 的标准操作请求。
- `gate-decision.schema.json`：Gate 返回给 Adapter 的三态标准判定。
- `adapter-manifest.schema.json`：Agent 能力和生成产物声明。
- `operation-catalog.json`：标准操作名称、类别、语义和是否可作为请求输入。
- `product-state.schema.json`：产品根目录（Product Root）的本地模式、跟踪分支和版本状态。
- `workspace.schema.json`：产品根目录、项目和 Agent 集合的工作空间配置。
- `workspace-init.schema.json`：生成接线的产品版本、普通文件内容哈希，以及中央 Project Skill 的受控符号链接清单。
- `task-registry.schema.json`：项目工作空间内多个任务的统一注册与激活状态。
- `task-state.schema.json`：每个 Jira 任务统一的阶段、事实、仓库和恢复状态。
- `jira-field-readback.schema.json`：字段同步回读输入，绑定当前本地事实摘要及 Jira 来源，不修改本地有效事实。
- `quality-action.schema.json`、`quality-state.schema.json`：任务 run 内质量检查、用户处置及外部证据回写的输入和恢复记录；不扩展任务状态机，也不代表外部系统事实已被认证。

Manifest v2 新增可选 `retired_artifacts`，声明需要显式迁移的已托管产物；不生成平台特例或可写门禁档位。当前内置 Agent 的 artifacts 不包含通用 Hook。Gate v1 协议资产仍可独立测试和显式调用，但不再接入使用者原生工具；以下旧 Gate 目标字段不代表当前 Jira 同步有强制单次调用保证。

Workflow CLI 的状态写命令现要求 `--expected-run-id`，advance 另要求 `--expected-stage`；缺参数明确失败，不替调用者读取并填充。任务状态文件结构不变，旧 run/历史证据保留。方案确认新增 `enforcement=workflow_checkpoints` 标记，授权绑定在检查点重查；新增 approved_plan_digest 绑定 fix_plan，旧记录不回填。init.json 的 checkpoint_migration 保存迁移前产品引用、接受时间与退役路径，仅为操作记录，不是身份认证。

## 兼容规则

`authorization.py renew` 使用 expected-run-id 与 expected-authorization-digest 双重绑定，只延长未撤销、方案摘要及仓库绑定不变的授权有效期；renewals 保存决定者、确认来源、前后有效期和原授权摘要。`show --digest` 只输出当前授权的规范化 SHA256。任务的 `completed` 阶段是验收提交点；同 run 的 `advance --expected-stage ci_validation` 重试可以收敛授权撤销及注册状态，成功收敛后重复调用不再写入。除该终态恢复外，过期阶段请求仍拒绝。

- 协议使用整数 `protocol_version`，Manifest 和操作词表使用 `schema_version`。
- 新增可选字段或标准操作可以保持当前版本，但必须补充一致性测试。
- `gate-request-v1.target.jira_transition_id` 是 Jira 状态转换的可选精确目标；只有 Workflow 为当前 task/run/node 准备且仍待执行的同一转换，Gate 才可识别为项目限定的自动同步意图，不能泛化为 Jira 写入授权。
- `gate-request-v1.target.jira_watermark_field` 与 `jira_watermark_digest` 共同标识一个接管版本水印字段载荷；仅当前 task/run 已准备、未消费的完全相同载荷可放行一次，不能泛化为 Jira 字段编辑授权。
- `task-state-v1` 的仓库 `authorized_endpoint` 是向后兼容的可选字段；新登记仓库必须从 Project catalog 固化该字段，旧任务可在受控 `repository prepare` 时迁移。旧授权不会被静默补写：缺少该字段时非 push 操作继续按 v1 通用绑定校验，push 必须失败关闭并重新签发授权。
- 删除字段、增加必填字段或改变既有字段和操作语义必须升级主版本。
- 未知版本、缺失字段和契约与 Policy 漂移必须拒绝；未知操作必须转人工。
- Adapter、Gate、Policy 不得私自定义未登记的标准操作或字段语义。
