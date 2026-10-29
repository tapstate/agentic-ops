# AgenticOps 标准契约

工位兼容清单的 `validation: feature` 表示由各功能核验所需状态，epoch 作为来源信息保留；未声明时仍为旧 epoch 模式。支持范围及旧操作限制以[工位合同](../docs/architecture/single-task-station.md#10-升级与恢复)为准。下述跨 epoch 强制退出描述仅适用于旧模式。
`contracts/` 维护显式 Gate API、声明式 Agent 接线与工位状态的机器合同。本文导航各合同职责与兼容边界；分层与执行保证以[工程架构](../docs/architecture/agenticops-v1-architecture.md)为准。

Agent 启动的默认参数归 `adapter-manifest.schema.json` 的可选 `launch.arguments` 声明，Bootstrap 按参数数组传递，再追加使用者参数，不执行 shell 二次解析。旧 Manifest 不声明时仍使用空参数数组；这不改变工位状态格式或 epoch。Codex 的当前默认值与原生审批含义见[Codex Adapter](../adapters/agents/codex/README.md)。

质量 receipt 增加 deferred 与可选 reason：表示已知未写入，必须说明原因；unknown 表示结果不明，不得盲目重发。同步状态不决定本地阶段通过。issue-versions 输入的 effective 保存 versions、execution_branch 和 proof，observed 保留初始 Jira 快照；版本无需 Jira ID，也不映射分支。旧事件按记录时规则重放，人读评论格式由当前 Project 配置决定。

- `gate-request.schema.json`：Adapter 交给 Gate 的标准操作请求。
- `gate-decision.schema.json`：Gate 返回给 Adapter 的三态标准判定。
- `adapter-manifest.schema.json`：Manifest v3 的 Agent 接线修订、生成与退役产物、启动方式和 Skill 目标声明，不包含工具 Hook 协议。
- `operation-catalog.json`：标准操作名称、类别、语义和是否可作为请求输入。
- `product-state.schema.json`：产品根目录（Product Root）的本地模式、跟踪分支和版本状态。
- `station.schema.json`：产品根目录、项目和 Agent 集合的工位配置。
- `station-layout.json`：工位固定路径、结构角色和合法动作能力，由 Bootstrap 与 Workflow 共同读取，不提供默认清理动作。
- `station-reset.schema.json`：生命周期清理计划；v9 增加逐仓归位与明确删除兜底，v8 在途计划按原整区删除语义恢复，不静默转换。归位依赖及私有恢复区职责见[任务数据描述](../workflow/task-data.md#源码归位依赖)。其它旧计划须由原版本核实收尾。
- `station-init.schema.json`：生成接线的产品版本、普通文件内容哈希，以及中央 Project Skill 的受控符号链接清单。
- `task-registry.schema.json`：项目工位内多个任务的统一注册与激活状态。
- `task-state.schema.json`：任务模块拥有的 current 信封、阶段、事实、仓库和恢复状态；依赖含义、来源、修补入口及备份清理见 [任务状态依赖描述](../workflow/task-data.md)，其它功能使用 task_store 接口。
- `jira-field-readback.schema.json`：字段同步回读输入，绑定当前本地事实摘要及 Jira 来源，不修改本地有效事实。
- `quality-action.schema.json`、`quality-state.schema.json`：任务 run 内质量检查、用户处置及外部证据回写的输入和恢复记录；不扩展任务状态机，也不代表外部系统事实已被认证。

Manifest v3 删除 `entrypoint`、`hook` 和 `capabilities`，不再要求 Agent 提供 Python Hook；`adapter_version` 只标识平台接线修订。可选 `retired_artifacts` 仍用于显式移除已核验归属的托管产物，不能删除用户修改文件。注册器不解析旧版 Manifest，也不提供工具拦截模式开关。Gate v1 继续支持独立测试和显式调用，但不接入使用者原生工具；以下 Gate 目标字段不代表当前 Jira 同步有强制单次调用保证。旧 Hook 退役仍须沿受支持入口显式确认归属，不通过删除任务记录代替接线迁移。

Workflow CLI 的状态写命令现要求 `--expected-run-id`，advance 另要求 `--expected-stage`；缺参数明确失败，不替调用者读取并填充。任务状态文件结构不变，旧 run/历史证据保留。方案确认新增 `enforcement=workflow_checkpoints` 标记，授权绑定在检查点重查；新增 approved_plan_digest 绑定 fix_plan，旧记录不回填。init.json 的 checkpoint_migration 保存迁移前产品引用、接受时间与退役路径，仅为操作记录，不是身份认证。

工位兼容清单由同名 Schema 校验；`station_state_epoch` 保留单调递增的来源标识，`validation: feature` 将执行条件交给所属功能检查。旧客户端没有该声明时仍按其原 epoch 合同运行。`station-init.schema.json` 记录生成来源和 cleanup 清单；刷新旧工位保留来源 epoch，不用改标记代替校验。升级与回退的唯一规则见[工位合同](../docs/architecture/single-task-station.md#10-升级与恢复)，这里不另维护支持列表或逐版本转换规则。

归档恢复的外部材料合同由 [task_handoff 依赖描述](../workflow/task-handoff.md) 维护；其版本独立于任务状态 schema。

## 兼容规则

工位 epoch 36 在 current-task 信封增加可选 `execution_control`，由受管写入入口核对会话；旧客户端会忽略或丢失占用，不能用旧版执行新版状态。结构与交接行为见[执行接管](../docs/architecture/single-task-station.md#执行接管当前源码)。

工位 epoch 35 增加持久归档的待 Jira 回读操作与明确未发送取消语义，复用原 operation 记录。旧客户端不能把这一状态当成本地快照已完成，切换遵循工位合同，不能丢弃在途回读；详情见[持久接续点操作](../docs/usage/task-archive-and-resume.md#持久接续点操作)。

工位 epoch 34 增加项目声明的单级独立验收、逐用例计划与实绩、测试编写情况及 PR 总结/补测 Issue 关联检查，沿用质量事件和 `pr-ready-input.json`，不新增状态账本。原始执行结果与编写缺口决定分别保存，历史事件按当时规则重放；旧客户端不能解释新的提审要求；新版按当前要求复查，不沿用失效批准。字段与操作以[共同验证材料](../docs/usage/quality-checkpoints.md#共同验证材料)为准。

epoch 27 将 PR Ready 的完成语义收敛为 GitHub 已退出 Draft 且回读确认，预检结果使用 `preflight_ready`。PR 提审证据复用当前 run 的 `interactions/pr-ready-input.json`，不新增状态账本；旧客户端不能解释具体 Checks 例外及新评论表示，不能据旧版提审结果推定当前条件已满足。旧质量事件保留原评论生成与严格回读，新增 `human-text-v2` 只生成阶段总结。质量 readback 的可选 `body_representation=literal-markdown-v1` 仅用于已核验为字面文本的 Markdown 转换，不解码原文已有反斜杠或其它转义；缺省保持原严格比较。

项目 `plan_contract.review.environment_version=2` 显式启用环境阶段依赖，缺省或版本1保持全部 missing 阻塞 Q2，未知版本拒绝。质量事件已有 rules 快照承载该声明，历史回放使用当时规则，不按当前配置重解释。新增阶段/检查项声明改变旧客户端的解释能力，独立交付时再次提升 epoch；升级与回退均原版退出并 purge，不在线迁移或补造确认。

来源同步验证的新事件由写入口生成 `binding_version=2`，仅绑定所属仓的源码版本与七字段仓库登记摘要；无该字段的历史事件保持全仓语义。未知版本及其它验证种类使用该字段必须拒绝，调用者不能指定该内部版本。`local/ci/review` 不改变失效范围。旧客户端不能理解新证据语义；历史事件按自身版本解释，不转换或伪造批准。

`authorization.py renew` 使用 expected-run-id 与 expected-authorization-digest 双重绑定，只延长未撤销、方案摘要及仓库绑定不变的授权有效期；renewals 保存决定者、确认来源、前后有效期和原授权摘要。`show --digest` 只输出当前授权的规范化 SHA256。任务的 `completed` 阶段是验收提交点；同 run 的 `advance --expected-stage ci_validation` 重试可以收敛授权撤销及注册状态，成功收敛后重复调用不再写入。除该终态恢复外，过期阶段请求仍拒绝。

- 协议使用整数 `protocol_version`，Manifest 和操作词表使用 `schema_version`。
- 新增可选字段或标准操作可以保持当前版本，但必须补充一致性测试。
- `gate-request-v1.target.jira_transition_id` 是 Jira 状态转换的可选精确目标；只有 Workflow 为当前 task/run/node 准备且仍待执行的同一转换，Gate 才可识别为项目限定的自动同步意图，不能泛化为 Jira 写入授权。
- `gate-request-v1.target.jira_watermark_field` 与 `jira_watermark_digest` 共同标识一个接管版本水印字段载荷；仅当前 task/run 已准备、未消费的完全相同载荷可放行一次，不能泛化为 Jira 字段编辑授权。
- `task-state-v1` 的仓库 `authorized_endpoint` 是向后兼容的可选字段；新登记仓库必须从 Project catalog 固化该字段，旧任务可在受控 `repository prepare` 时迁移。旧授权不会被静默补写：缺少该字段时非 push 操作继续按 v1 通用绑定校验，push 必须失败关闭并重新签发授权。
- 删除字段、增加必填字段或改变既有字段和操作语义必须升级主版本。
- 未知版本、缺失字段和契约与 Policy 漂移必须拒绝；未知操作必须转人工。
- Adapter、Gate、Policy 不得私自定义未登记的标准操作或字段语义。

本版清单与非阻塞退出使用 epoch 30。取消归档冻结、源码重置和清理计划修订；独立归档只追加已有事实。工作清理、任务清理及注销边界以[单任务工位合同](../docs/architecture/single-task-station.md)为准。
