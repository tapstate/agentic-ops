# developer Jira 接管 Saga 设计

## 1. 目标与边界

本设计对应 AO-49，落实统一接管操作的 Jira 副作用编排。Runtime 对用户呈现一次逻辑接管，但内部明确采用可恢复的 Saga：

```text
读取并验证事实
-> 持久化稳定意图
-> 确保受管 Comment 并回读
-> 必要时执行 Status transition 并回读
-> 本地最终收口
```

这里的“原子”只表示同一 `agentic_run_id`、同一稳定意图和最终一致的可恢复操作，不表示 Jira 单请求事务，也不表示 Jira 与本地文件之间存在分布式事务。

本工作项不写 Agentic Jira Custom Field，不探测业务 Jira 字段目录，不引入跨工作空间并发锁，也不增加直接 REST、Connector 或临场脚本入口。所有 Jira 副作用仍由 developer Python Runtime 发起，AO-50 的 `TaskStore` 接管状态服务继续作为唯一恢复状态。

## 2. 当前缺口

现有正式接管虽然先检查 transition、再写 Comment、最后执行 transition，但只有全部 Jira 操作成功后才调用 `migrate_legacy_takeover(...)` 合成本地 v2 状态。这会产生四个不可接受的恢复空窗：

- Comment 已写但进程中断时，本地没有稳定标记、正文摘要或接管类型，重试可能重新分类并生成新 Comment。
- transition 响应丢失时，Runtime 没有阶段检查点，无法区分“未执行”“已执行但响应丢失”和“第三方改到了其它状态”。
- Jira 已到目标状态但来源快照或本地最终落盘失败时，下一次调用仍可能重新走 Comment/transition 路径。
- Comment 复用只检查作者和正文包含标记，没有同时验证 Comment ID、独立标记行和完整内容摘要；相同文本被复制时证据不足。

AO-49 必须把正式接管改为从稳定意图驱动的状态恢复，不再以本次 Jira Status 临时重算接管类型。

## 3. 权威状态与兼容扩展

继续使用 AO-50 定义的四阶段状态机：

```text
intent_persisted
-> comment_verified
-> status_verified
-> local_finalized
```

`sync.json.takeover_operation` 仍是唯一权威快照。新建意图时，在现有 schema v2 上增加可选的不可变字段 `comment_markdown`，保存 Runtime 实际准备写入 Jira 的完整受管 Comment。这样恢复不依赖调用者再次提供 `--transition-comment`、工作空间名称或操作时间，也不会根据当前 Jira Status 重建正文。

兼容规则如下：

- 新建的 `intent_persisted` 操作必须包含 `comment_markdown`，其规范化纯文本摘要必须等于 `comment_content_sha256`。
- 已存在且包含 `comment_markdown` 的操作将该字段纳入不可变意图比较，任何覆盖尝试都以 `takeover_intent_conflict` 失败关闭。
- AO-50 已生成但没有该字段的 `local_finalized` v2 状态仍可读取；Runtime 通过已记录 Comment ID 回读作者、稳定标记和现有摘要，不回退或重写已完成操作。
- 没有 `comment_markdown` 的未完成 v2 意图不能安全重发 Comment，返回 `takeover_comment_material_missing`，不猜测原正文。
- legacy v1 成功状态只允许从原 `takeover_task` 事件提取运行编号、Comment ID、标记、接管类型和授权摘要，并在 Jira 作者、负责人和目标 Status 全部回读一致后调用 `migrate_legacy_takeover(...)`；证据不足保持原状态。

Comment 摘要统一按 `plain_text(markdown_to_adf(comment_markdown))` 的 UTF-8 内容计算，确保写前正文与 Jira 回读正文使用同一规范化口径，而不是比较原始 Markdown 与 Jira ADF 的不同表示。

## 4. 写前事实与稳定意图

首次 Jira 写入前完成两次同口径校验：第一次用于形成意图，第二次紧邻 Comment POST，用于发现意图落盘后的事实漂移。`preflight_facts_sha256` 至少绑定：

- Connection ID、Jira Issue ID、Issue Key 和 Project Key。
- 当前工作空间 Jira accountId 与 Issue Assignee。
- Jira 原 Status、目标 Status、目标 transition ID。
- 当时可用 transition 的规范化集合；无需 transition 时明确记录空值。
- 当前本地 `agentic_run_id`、接管类型和授权摘要。

写前必须一次性验证：

1. Issue 仍属于工作空间绑定项目，Issue ID 未变化，Assignee 仍是当前工作空间账户。
2. 原 Status 与 Project Profile 映射仍有效，目标执行状态唯一。
3. 需要 transition 时，配置映射和 Jira 可用 transition 都能精确匹配目标；缺口在任何 Comment 写入前阻断。
4. 本地任务身份与当前 Connection、Issue、Project、run 一致。
5. 当前 run 没有冲突的受管接管 Comment；相同稳定标记只能存在一条。

意图持久化后，`agentic_run_id`、`takeover_kind`、授权摘要、原/目标 Status、transition ID、Comment 标记、正文和内容摘要全部不可变。恢复调用必须复用这些事实：即使 Jira 已经进入目标状态，也不能把原来的 `new_takeover` 改判为 `accept_existing_task` 或生成新标记。

由于 Jira 与本地不存在跨系统锁，最后一次写前回读之后仍存在极小的并发时间窗。本工作项通过严格回读和冲突停止避免错误宣称成功，但不宣称解决两个独立工作空间同时接管的互斥；真实并发需求出现后另行设计锁或服务端仲裁。

## 5. 受管 Comment 幂等与恢复

Comment 稳定标记继续绑定：

```text
issue_key + agentic_run_id + takeover_kind + authorization_digest
```

查找和复用规则固定为：

- 优先按本地已记录 `comment_id` 读取单条 Comment；没有 ID 时，分页读取当前 Issue Comment，并按独立完整行中的稳定标记查找。
- 同一标记必须唯一；重复标记返回 `takeover_comment_duplicate`。
- 候选 Comment 必须同时满足：ID 非空、作者等于当前工作空间 Jira accountId、稳定标记是独立完整行、规范化正文摘要等于稳定意图摘要。
- 同一 Issue/run 出现不同接管标记，或相同标记由其他作者写入、正文摘要不同，返回 `takeover_comment_evidence_conflict`，不得创建第二条 Comment 掩盖冲突。
- 只有全部证据一致时调用 `verify_takeover_comment(...)` 进入 `comment_verified`。

Comment POST 的不确定结果按以下顺序处理：

| POST 后事实 | Runtime 动作 | 结果 |
| --- | --- | --- |
| 正常响应，按 ID 和列表回读一致 | 保存 Comment 证据 | 进入 `comment_verified` |
| 连接中断或 5xx，但回读发现唯一一致 Comment | 视为写入已成功 | 进入 `comment_verified` |
| 响应不明，可靠回读确认标记不存在 | 保持 `intent_persisted` | 返回 `takeover_comment_retryable_absent`、`retry_safe=true`，允许同一意图重试 |
| 响应不明且回读失败或结果不可判定 | 调用 `mark_takeover_uncertain(...)` | 返回 `takeover_comment_result_uncertain`、`retry_safe=false`，停止自动副作用 |
| 回读发现作者、标记、正文或数量冲突 | 调用 `block_takeover(...)` | 返回稳定冲突码并进入风险决策 |

Comment 已验证之前绝不执行 Status transition。Comment 已成功但后续失败时保留该可见审计记录，不删除、不修改，也不宣称接管完成。

## 6. Status transition 幂等与恢复

进入 `comment_verified` 后先回读 Issue：

- 当前 Status 已等于稳定目标值：不执行 transition，直接调用 `verify_takeover_status(...)`。
- 当前 Status 仍等于稳定原值：重新验证原 transition ID 仍可用，然后最多发起一次必要 transition。
- 当前 Status 是原值和目标值之外的第三种状态：调用 `block_takeover(...)`，返回 `takeover_status_external_conflict`，进入逐项风险决策。
- Issue、负责人或项目身份无法可靠回读：返回不确定结果，不执行或重试 transition。

transition 请求后必须回读 Issue Status，不以 HTTP 成功响应替代事实证据：

| transition 后事实 | Runtime 动作 | 结果 |
| --- | --- | --- |
| 回读为目标 Status | 保存 Status 证据 | 进入 `status_verified` |
| 回读仍为稳定原 Status | 保持 `comment_verified` | 返回 `takeover_transition_retryable_original`、`retry_safe=true`，后续同一意图可恢复 |
| 回读为第三种 Status | 保存冲突 | `takeover_status_external_conflict`、`retry_safe=false` |
| 无法可靠回读 | 保存不确定结果 | `takeover_transition_result_uncertain`、`retry_safe=false` |

恢复时目标 Status 已达成视为成功证据，不重复 transition；只有可靠确认仍为原 Status 才允许后续重试。每次成功最多包含一次必要 transition，重复调用已经完成的同一意图不再产生 Jira 副作用。

## 7. 本地最终收口

`status_verified` 后按固定顺序执行：

1. 再次回读并验证 Comment ID、作者、标记、正文摘要、Assignee 和目标 Status。
2. 调用 `record_current_task_source_context(...)` 生成或覆盖同一 run 的来源快照。
3. 调用 `finalize_takeover(...)`，由 AO-50 状态服务在任务锁内收口 `sync.json`、`progress.json` 和 journal。
4. 通过 `read_takeover_recovery(...)` 交叉回读，只有 `phase=local_finalized`、`result=completed`、`state_consistent=true` 才返回成功。

来源快照或本地写入失败时返回 `takeover_local_finalize_failed`，保留 `status_verified` 或 AO-50 可识别的部分收口状态，`retry_safe=true` 仅允许重做本地收口。下一次调用先从原意图回读 Jira 事实，不创建 run、Comment 或 transition。若本地身份、快照和事件彼此冲突，则沿用 AO-50 的失败关闭语义，要求人工核对，不能用新事件覆盖冲突。

原有在 Saga 完成后额外写入 `takeover_task` 通用门禁事件的路径移除，避免与 AO-50 的 `takeover_completed` / `takeover_recovered` 事件形成两个成功事实源。

## 8. 统一输出和错误合同

成功输出必须包含：

- `issue_key`、`agentic_run_id`、`takeover_kind` 和非新接管明文提示。
- `takeover_comment_id`、`takeover_comment_author`、`takeover_comment_author_verified=true`。
- `jira_status_before`、`jira_status_target`、`jira_status_after`、`transition_applied`。
- `takeover_phase=local_finalized`、`takeover_result=completed`、`external_result_certainty=verified`。
- `state_consistent=true`、来源快照路径和下一动作。

任一证据缺失时不得返回 `takeover_status=completed`。错误输出使用稳定码并明确 `retry_safe`、当前 phase、operation ID、已确认外部事实和唯一恢复动作；不要求用户复制或确认内部 digest、Comment 标记或 operation ID。

失败码至少包括：

| 类别 | 稳定失败码 | `retry_safe` |
| --- | --- | --- |
| 写前事实漂移 | `takeover_preflight_facts_changed` | `false` |
| 意图或本地身份冲突 | `takeover_intent_conflict` / `takeover_state_identity_mismatch` | `false` |
| Comment 已确认不存在、可重试 | `takeover_comment_retryable_absent` | `true` |
| Comment 结果不确定 | `takeover_comment_result_uncertain` | `false` |
| Comment 证据冲突或重复 | `takeover_comment_evidence_conflict` / `takeover_comment_duplicate` | `false` |
| transition 仍在原状态、可恢复 | `takeover_transition_retryable_original` | `true` |
| transition 结果不确定 | `takeover_transition_result_uncertain` | `false` |
| 外部第三方状态冲突 | `takeover_status_external_conflict` | `false` |
| 本地收口失败 | `takeover_local_finalize_failed` | `true`，仅限本地恢复 |
| legacy 或恢复证据不一致 | `takeover_legacy_state_unverified` / `takeover_recovery_evidence_mismatch` | `false` |

## 9. 实现落点

- `developer/runtime/src/ao_work/task_takeover.py`
  - 把现有线性写入重构为由持久化 phase 驱动的 Saga。
  - 增加写前事实摘要、受管 Comment 唯一查找和规范化正文校验。
  - 所有恢复分支只消费原 `takeover_operation`，不重新分类。
- `developer/runtime/src/ao_work/task_state/takeover.py`
  - 兼容增加可选不可变 `comment_markdown`，补充其格式和摘要联合校验。
- `developer/runtime/src/ao_work/task_state/store.py`
  - `persist_takeover_intent(...)` 保存 Comment 原文；必要时补充只读 legacy 迁移证据。
  - 继续由现有阶段方法写快照和事件，不新增第二套 Saga 状态。
- `developer/tests/runtime/test_task_takeover.py`
  - 扩展故障注入 Transport，验证请求丢失、可靠回读、第三方漂移、重复调用和本地失败恢复。
- `developer/tests/runtime/test_takeover_state_machine.py` 与 inspect/resume 测试
  - 验证 schema 兼容、不可变 Comment 内容和统一恢复读取。

若实现中发现 Jira 客户端无法区分只读失败与副作用响应不明，只允许在 `ao_work.jira.client` 增加结构化异常事实；不得复制 HTTP 实现或绕过现有客户端。

## 10. 验证矩阵

专项测试至少覆盖：

- transition 映射缺失在本地意图和 Jira Comment 写入前阻断。
- 相同授权重复执行：同一 run、一条 Comment、最多一次必要 transition。
- Comment POST 实际成功但响应丢失，回读后继续完成。
- Comment POST 结果不明且可靠确认不存在，返回可重试且不执行 transition。
- Comment 回读失败、外部作者、复制标记、正文摘要冲突、重复标记全部失败关闭。
- Comment 成功而 transition 失败时保留 Comment，输出不得宣称完成。
- transition 实际成功但响应丢失，目标 Status 回读后继续完成。
- transition 后仍为原 Status 可恢复；第三种 Status 进入风险决策；回读失败进入不确定状态。
- Jira 已完成但来源快照或本地最终落盘失败，下一次调用不新增 Jira 副作用并完成本地恢复。
- legacy v1 与 AO-50 无 `comment_markdown` 的已完成 v2 状态兼容读取；未完成且缺少重发材料时失败关闭。
- 成功输出缺少 Comment 作者、Status 或本地一致性任一证据时测试失败。

完成专项测试后执行现役四项完整验证：

```sh
bash maintainer/scripts/test-python-runtime.sh
bash maintainer/scripts/test-resources.sh
bash developer/tests/bootstrap/test_install_boundary.sh
bash maintainer/scripts/test-release-workflow.sh
```

## 11. 风险与验收决策

- **状态兼容风险**：采用 schema v2 可选字段扩展，不修改既有必填字段或阶段；旧完成状态可读，旧未完成状态在缺少原文时失败关闭。
- **Jira 最终一致性风险**：任何副作用响应都以回读事实为准；无法可靠回读时停止，不用自动循环掩盖不确定性。
- **本地多文件中断风险**：复用 AO-50 的交叉校验和恢复事件；本地恢复不得触发 Jira 重写。
- **并发剩余风险**：本工作项只检测已可见的冲突，不承诺跨工作空间互斥，保持 AO-49 明确的不在范围边界。
- **公开入口边界**：AO-49 只完成底层唯一 Saga；命令简化和顶层 `ao-work takeover <KEY>` 由 AO-48 接入同一实现，不在这里并行维护另一个入口。

设计审查通过后，工作项级连续执行授权覆盖以上范围内的实现、测试和必要 Jira 进度回写；若需要改变 schema 版本、引入并发锁、扩大 Jira 字段写入、改变公开命令或放宽不确定结果的失败关闭策略，视为范围或风险变化，必须重新进入设计审查或风险决策。
