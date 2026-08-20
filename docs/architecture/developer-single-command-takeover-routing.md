# developer 单命令接管路由设计

> **AO-48 实施设计。** 本文第 2.2 节记录的隐藏兼容入口只保留了一个发布窗口，已由 AO-51 删除。现役公开及可解析入口均只有 `ao-work takeover [<KEY>]`；兼容段落只用于解释迁移历史，不是当前操作说明。

## 1. 目标与边界

本设计对应 AO-48。研发工程师和 AIAgent 的正式接管入口统一为：

```text
ao-work takeover [<KEY>]
```

顶层入口只负责参数收敛和调用既有接管服务；候选查询、三种模式判定、Jira Comment、Status transition、恢复与本地最终收口继续由 AO-49/AO-50 已落地的 `execute_task_takeover(...)` 和 `TaskStore` 完成，不复制第二套业务逻辑。

本工作项不改变 maintainer 工作面。AO 项目继续只使用 `ao-maint takeover <AO-KEY>`；`ao-work` 只在 developer 工作空间处理该工作空间绑定的业务 Jira 项目。

## 2. 公开命令与兼容入口

### 2.1 正式入口

- `ao-work takeover <KEY>`：执行或恢复指定任务接管。
- `ao-work takeover`：只读返回排序后的候选，`selection_required=true`；不得自动选择、创建本地任务状态、写 Comment 或流转 Status。
- 顶层帮助只展示可选的 `issue_key`。`agent_id`、授权引用和附加 Comment 不再作为研发工程师需要理解的公开参数。

### 2.2 隐藏兼容别名

`ao-work task takeover` 保留一个发布窗口，满足已有自动化迁移：

- 继续进入同一个 `execute_task_takeover(...)`，不得复制分类、Saga 或恢复代码。
- 从 `task --help` 和 capability catalog 的公开命令中隐藏。
- 成功输出增加独立的 `deprecated_alias=true`、`deprecation_notice` 和替代命令；不得覆盖接管服务的 `human_notice`。
- 兼容别名维持原有显式 `--authorization-reference` 行为，避免旧调用的授权语义静默变化；新入口不要求该参数。
- AO-51 负责在后续发布窗口完成更广泛的资产回归，并按版本计划删除兼容入口；AO-48 不提前删除底层兼容能力。

## 3. 内部授权绑定

研发工程师明确说“接管 <KEY>”并触发 `ao-work takeover <KEY>`，就是事实明确的常规接管授权。Runtime 不再要求用户查看、复制或确认授权引用。

顶层入口调用接管服务时使用 `authorization_mode=takeover_instruction`。服务完成 Jira Issue、工作空间身份与本地任务身份读取并确定 `agentic_run_id` 后，生成只在 Runtime 内使用的稳定引用：

```text
takeover-instruction:<issue_key>:<agentic_run_id>
```

随后沿用现有 SHA-256 摘要进入稳定 Comment 标记和 `takeover_operation.authorization_digest`。因此：

- 同一 Issue 和同一 run 重试得到同一授权摘要，可恢复既有意图。
- 新 run 得到不同摘要，不会错误复用旧运行的接管证据。
- 原始内部引用不写入人可见输出、Jira Comment 或要求用户确认的字段。
- 无编号候选查询不创建 run，也不生成写操作授权。
- 已持久化意图仍以原授权摘要为准；任何调用都不得用新引用覆盖。

兼容别名继续校验调用方提供的显式引用。顶层路由和兼容别名最终都把一个稳定引用交给同一服务，后续 Saga 行为完全一致。

## 4. 模式、提示与风险路由

模式仍由现有唯一判定表产生：

- `new_takeover`
- `accept_existing_task`
- `resume_takeover`

CLI 不根据 Jira Status 或本地文件自行判断。成功结果完整透传服务的结构化字段；`human_notice` 必须始终存在，后两种模式必须包含“不是新接管”。Jira 审计 Comment 继续由同一 Saga 使用相同模式词汇写入并回读。

风险处理遵循失败关闭：

- 无编号调用只返回候选选择，不视为风险确认。
- 所有权冲突、映射缺失、写前事实漂移、Comment/Status 结果不确定或本地证据冲突继续返回现有稳定失败码、当前事实、已执行动作、风险和唯一恢复动作。
- 顶层 CLI 不提供 `new`、`resume`、`adopt`、`force` 或 `ignore` 子命令，也不把用户“确认”解释成覆盖 Jira/本地冲突。
- 风险事实经人工处理或确认后，AIAgent 仍重新调用 `ao-work takeover <KEY>`；Runtime 必须重新读取事实并仅按原 Saga 允许的恢复动作推进。需要改变稳定意图或放宽失败关闭规则时属于范围变化，必须另行设计。

这使用户只查看具体任务事实、拟执行动作、变更点和逐项风险，不接触 `impact_id`、plan ID、digest 或授权参数。

## 5. 实现落点

- `developer/runtime/src/ao_work/work_cli.py`
  - 注册顶层 `takeover` parser 和执行路由。
  - 把顶层调用标记为 `takeover_instruction` 授权模式。
  - 保留隐藏的 `task takeover` 兼容 parser，并只在结果层增加弃用信息。
- `developer/runtime/src/ao_work/task_takeover.py`
  - 在 run 已确定后生成稳定的内部授权引用。
  - 保持候选只读路径先于授权生成。
  - 保持显式引用兼容模式及既有 Saga/恢复逻辑不变。
- `developer/standards/contracts/operations/takeover-task.yaml` 与 `developer/standards/capabilities/operations.yaml`
  - 将 `[takeover]` 声明为唯一公开命令。
  - 把授权来源改为 Runtime 绑定的明确接管指令；显式引用仅为隐藏兼容输入。
- developer AI 入口、日常任务 Skill、AI 员工手册和面向研发工程师的入门/示例资产
  - 只展示 `ao-work takeover [<KEY>]`。
  - 删除让用户构造内部引用或理解多级接管命令的步骤。
- 测试
  - 在 CLI、接管 Runtime、能力目录、资源与初始化边界测试中覆盖顶层入口、隐藏别名、授权稳定性和公开资产约束。

## 6. 验证矩阵

专项验证至少覆盖：

- 顶层带 KEY 调用与兼容别名进入同一个服务，三种接管模式、Comment、Status 和本地 phase 结果一致。
- 顶层带 KEY 调用无需授权参数；同一 run 的失败恢复生成同一授权摘要，不新增 Comment 或 transition。
- 顶层无 KEY 调用全程只读并返回候选，不生成授权、run 或接管事件。
- `accept_existing_task` 与 `resume_takeover` 的结构化输出和 Jira Comment 都明文包含“不是新接管”。
- `ao-work --help` 展示 `takeover`，`ao-work task --help` 不展示兼容别名；直接调用兼容别名仍可执行并输出弃用提示。
- capability catalog、developer `AGENTS.md`、正式 Skill、手册和用户示例不再出现 `ao-work task takeover` 或要求用户传入授权引用。
- 顶层入口仍受 developer 工作空间、项目、Assignee、Status 映射和 Saga 失败关闭约束，不能处理 AO 项目或切换到 maintainer 工作面。

完成专项测试后执行四项固定完整验证：

```sh
bash maintainer/scripts/test-python-runtime.sh
bash maintainer/scripts/test-resources.sh
bash developer/tests/bootstrap/test_install_boundary.sh
bash maintainer/scripts/test-release-workflow.sh
```

## 7. 风险与审查决策

- **授权语义风险**：顶层命令把“明确接管指令”视为常规授权，只能在带 KEY 的写路径、run 已确定后生成稳定摘要；候选查询绝不能因此获得写权限。
- **兼容风险**：旧别名保留一个发布窗口并维持显式引用语义；新旧入口共享服务，但输出会增加弃用字段。
- **审计风险**：内部引用不对用户展示，因此审计必须依赖 Issue、run、Comment 标记、授权摘要和本地事件的联合事实，不能宣称聊天身份的独立远程证明。
- **失败恢复风险**：顶层路由不得把风险确认变成强制覆盖；AO-49 已定义的不确定结果、冲突和不可变意图继续失败关闭。
- **资产迁移风险**：AO-48 先保证新工作空间和主要公开资产只生成顶层入口；AO-51 再执行全量资产/E2E 验收和兼容入口后续清理，避免在本工作项扩大为真实 Jira E2E。

设计确认后的工作项级连续执行授权覆盖上述实现、专项测试、四项固定验证及必要的 AO-48 中文进度回写。若需要删除兼容别名、改变三种模式判定、允许强制覆盖风险、修改 Saga phase、引入跨工作空间并发锁或扩大工作面/Jira 项目范围，必须重新进入设计审查或风险决策。

## 8. AO-51 最终收口

AO-51 完成一个发布窗口后的兼容清理：删除 `ao-work task takeover` parser、显式授权引用模式、隐藏 `agent_id` / Comment 参数及弃用输出。Runtime 只从安装身份读取 `agent_id`，并只按 `issue_key + agentic_run_id` 生成内部授权引用。第 2.2、3、5 和 7 节中的兼容描述因此只代表 AO-48 当时的迁移安排，不再是现役行为。
