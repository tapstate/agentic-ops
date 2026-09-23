# AgenticOps 标准契约

`contracts/` 维护显式 Gate API、声明式 Agent 接线与工位状态的机器合同。本文导航各合同职责与兼容边界；分层与执行保证以[工程架构](../docs/architecture/agenticops-v1-architecture.md)为准。

质量 receipt 增加 deferred 与可选 reason：表示已知未写入，必须说明原因；unknown 表示结果不明，不得盲目重发。同步状态不决定本地阶段通过。issue-versions 输入的 effective 保存 versions、execution_branch 和 proof，observed 保留初始 Jira 快照；版本无需 Jira ID，也不映射分支。旧事件按记录时规则重放，人读评论格式由当前 Project 配置决定。

- `gate-request.schema.json`：Adapter 交给 Gate 的标准操作请求。
- `gate-decision.schema.json`：Gate 返回给 Adapter 的三态标准判定。
- `adapter-manifest.schema.json`：Manifest v3 的 Agent 接线修订、生成与退役产物、启动方式和 Skill 目标声明，不包含工具 Hook 协议。
- `operation-catalog.json`：标准操作名称、类别、语义和是否可作为请求输入。
- `product-state.schema.json`：产品根目录（Product Root）的本地模式、跟踪分支和版本状态。
- `station.schema.json`：产品根目录、项目和 Agent 集合的工位配置。
- `station-reset.schema.json`：唯一现役版本 6 的清理范围、保全与规则快照；旧计划必须由原版本退出，不在线转换。历史档案保留，不作为可恢复执行合同。
- `station-init.schema.json`：生成接线的产品版本、普通文件内容哈希，以及中央 Project Skill 的受控符号链接清单。
- `task-registry.schema.json`：项目工位内多个任务的统一注册与激活状态。
- `task-state.schema.json`：每个 Jira 任务统一的阶段、事实、仓库和恢复状态。
- `jira-field-readback.schema.json`：字段同步回读输入，绑定当前本地事实摘要及 Jira 来源，不修改本地有效事实。
- `quality-action.schema.json`、`quality-state.schema.json`：任务 run 内质量检查、用户处置及外部证据回写的输入和恢复记录；不扩展任务状态机，也不代表外部系统事实已被认证。

Manifest v3 删除 `entrypoint`、`hook` 和 `capabilities`，不再要求 Agent 提供 Python Hook；`adapter_version` 只标识平台接线修订。可选 `retired_artifacts` 仍用于显式移除已核验归属的托管产物，不能删除用户修改文件。注册器不解析旧版 Manifest，也不提供工具拦截模式开关。Gate v1 继续支持独立测试和显式调用，但不接入使用者原生工具；以下 Gate 目标字段不代表当前 Jira 同步有强制单次调用保证。删除旧 Hook 执行依赖属于工位接线读用语义不兼容，必须通过 epoch 边界由原版本退出并 purge 后重建，不能靠新版扫描或在线迁移旧任务。

Workflow CLI 的状态写命令现要求 `--expected-run-id`，advance 另要求 `--expected-stage`；缺参数明确失败，不替调用者读取并填充。任务状态文件结构不变，旧 run/历史证据保留。方案确认新增 `enforcement=workflow_checkpoints` 标记，授权绑定在检查点重查；新增 approved_plan_digest 绑定 fix_plan，旧记录不回填。init.json 的 checkpoint_migration 保存迁移前产品引用、接受时间与退役路径，仅为操作记录，不是身份认证。

工位持久化不兼容边界由 `station-state-compatibility.json` 声明，结构由同名 Schema 校验。该清单只维护单调递增的 `station_state_epoch`：兼容修改保持不变，不兼容修改必须提升。现役产品只接受与自身相同的 epoch；升级与回退也只比较这个值，不另设旧 epoch 映射、支持列表或最低 updater protocol。`station-init.schema.json` 要求新工位记录当前 epoch；缺少该字段的工位不受支持，必须由原版本完成任务归档、释放并 purge。当前不提供跨 epoch 在线数据迁移、旧状态读取或 repair 采用。

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
