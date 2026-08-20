# developer 接管能力端到端验收设计

## 1. 目标与事实边界

本设计对应 AO-51，验收 AO-47 至 AO-50 形成的唯一现役接管能力：

```text
ao-work takeover [<KEY>]
```

验收对象只包含 developer 工作面的任务接管 Saga：Jira `Assignee`、Jira `Status`、当前运行的受管 Comment，以及本地 task state。developer 不创建、映射、探测或读写 Agentic Jira Custom Field。

AO-51 不把真实接管验收扩张为业务代码修改、Git 提交、任务分支推送或 PR 创建。task-to-PR 只验证其正式接管证据来源已经收敛为当前运行的受管 Comment、Comment 作者和不可变标记；完整业务任务到 PR 的真实测试仍由独立的 `test-task-to-pr-e2e` 流程负责。

工作面继续硬隔离：AO 项目只能由 `ao-maint` 处理，TAP 等业务项目只能由相应 developer 工作空间的 `ao-work` 处理。maintainer 当前会话不得直接调用业务 Jira 写接口，也不得读取 developer 凭证或本地 task state。

## 2. 现状与需要收口的漂移

AO-47 至 AO-50 已实现三种模式、可恢复本地状态机、Comment/transition 写后回读、顶层命令和稳定内部授权。AO-51 不重写 Saga，而是完成总验收并修复以下现役资产漂移：

- maintainer 的真实全链路 Skill 仍描述 `task start -> 准入确认 -> 方案确认 -> 正式接管` 的旧顺序，必须改为先执行 `ao-work takeover <KEY>`，普通信息分析不新增确认门禁。
- TAP Project Profile 的项目规则和缺陷准入模板仍把 `takeover_task` 写成 `capability_gap`，与能力目录的 `implemented` 事实冲突。
- AI 员工手册仍把已实现的 `list_tasks` 写成能力缺口。
- AO-48 保留的 `ao-work task takeover` 隐藏兼容入口到期；AO-51 删除该 parser、弃用输出和兼容测试，公开资产与安装产物只允许顶层入口。
- 新工作空间继承测试需要从“包含 developer 规则”加强为“只包含 developer 规则、顶层接管入口和当前能力事实”。

冻结历史、明确标注为迁移基线的文档不改写历史命令；资源测试只把现役资产、安装资产和面向用户的示例作为禁止旧入口的扫描范围。

## 3. 实施范围

### 3.1 标准资产与命令收口

- 更新 developer `AGENTS.md`、日常任务 Skill、task-to-PR Skill、AI 员工手册、TAP 项目规则、准入模板、操作合同、能力目录、示例和架构说明。
- 更新 maintainer `test-task-to-pr-e2e` Skill，使它以顶层接管为唯一入口，并删除准入摘要确认、通用方案确认和接管前 `task start`。
- 删除隐藏兼容入口 `ao-work task takeover`；`ao-work task --help` 与直接解析都不再接受该子命令。
- 保留内部 `task start` 只服务旧运行恢复，不向用户或能力选择公开。

### 3.2 Fake Jira 与本地状态矩阵

按单一 Saga 补齐或强化以下场景：

| 场景 | 必须证明的结果 |
| --- | --- |
| 新接管 | Comment 先确认、必要 transition 后确认、本地 `local_finalized` |
| 接纳存量 | 明文“不是新接管”、不重复 transition |
| 恢复 | 复用同一 run、按既有 phase 恢复、不重复已确认副作用 |
| 无编号 | 只读候选，不创建 run、Comment 或 transition |
| 相同指令重试 | 内部授权摘要稳定，Comment 标记不重复 |
| 外来 Comment 作者 | 不复用证据，失败关闭 |
| 外来 Agent / run | 输出冲突事实并进入风险决策，不覆盖本地意图 |
| 写前 Jira 事实漂移 | 在任何写入前阻断 |
| Comment 响应不明 | 仅回读；确认存在则恢复，确认不存在才可按原意图重试，不可判定则 uncertain |
| Comment 已确认、transition 失败 | 保存 `comment_verified`，后续只恢复 transition，不新增 Comment |
| transition 响应不明 | 由 Status 回读判定完成、可恢复或冲突；不盲目重试 |
| Jira 成功、本地收口失败 | 重建本地最终状态，不重写 Jira |
| legacy 迁移 | 事实完全一致才迁移；失败时原文件不变并关闭流程 |

测试不仅断言成功码，还要断言 Comment ID/作者/标记、Status 前后值、phase、run 复用、恢复动作和副作用调用次数。

### 3.3 task-to-PR 证据与安装继承

- task-to-PR 的 Jira probe 只接受当前 `agentic_run_id` 的受管接管 Comment；作者、标记或运行编号任一不匹配均不得声明正式接管。
- 新工作空间初始化测试回读生成的 `AGENTS.md`、Skill、能力目录与用户示例，断言只出现 `ao-work takeover [<KEY>]`，不出现 maintainer 规则、`ao-maint` 任务入口、旧多级接管入口或用户需确认的内部标识。
- developer-only sparse managed clone 与安装边界测试继续证明正常文件树不含 maintainer Runtime、Skill、Rule、授权或配置。

### 3.4 工作面轨迹

自动化测试记录并断言入口归属：

- AO issue 的写操作测试只通过 `./maintainer/bin/ao-maint`。
- TAP 等业务 issue 的写操作测试只通过业务工作空间中的 `ao-work`。
- 两个 Runtime 不互相 import，不共享凭证路径、task state、授权或 Project Profile。
- 项目 Key 与当前工作面不一致时在外部访问前失败关闭，不能由 AI 改用另一工作面的命令重试。

## 4. 真实 TAP Jira 验收

真实验收是独立的风险门禁，只在本设计确认、自动化回归通过且用户指定受控 TAP 卡片后执行。执行前只展示并确认可查阅资源与副作用，不要求用户复制 plan ID、digest 或内部授权摘要。

### 4.1 必需资源

- 一个明确的 TAP 测试任务 Key；任务属于 TAP 项目并分配给测试 developer 工作空间绑定的 Jira 用户。
- 该任务初始 Status 可由 TAP Project Profile 唯一映射到 `waiting_takeover`，并存在到 `implementation` 的严格 transition；如果只能验证存量接纳，则必须明确记录未覆盖“新接管 transition”。
- 一个与 AgenticOps 源仓库、`~/.agentic-ops` 和其它业务工作空间分离的 developer 工作空间。
- 用户对该卡片的 Jira 读取、追加一条受管中文 Comment、必要 Status transition 和本地 task state 写入的明确授权。

浏览器或 Jira 页面只可用于人工选择和审阅卡片；实际 Jira 写入必须由该隔离工作空间的 `ao-work takeover <KEY>` 完成。

### 4.2 执行顺序

1. 在不读取凭证、不访问 Jira 的前提下完成能力目录、安装版本、Project Profile 和工作面边界预检。
2. 用户确认具体 TAP Key 与副作用清单后，在隔离 developer 工作空间完成隐藏凭证授权和 `workspace preflight`。
3. 首次运行 `ao-work takeover <KEY>`，保存脱敏输出与本地状态摘要。
4. 立即以同一指令再次运行，验证恢复复用同一 run，且 Comment 与已完成 transition 不重复。
5. 使用现役只读恢复入口回读 task state，并通过 Jira 回读核对 Assignee、Status、Comment ID、Comment 作者和不可变标记。
6. 形成 AO-51 中文验收记录；真实证据只保存稳定 ID、摘要和脱敏事实，不保存 token、原始凭证或完整敏感日志。

### 4.3 通过条件

- 首次结果的模式、run、Comment 和 Status 与 Jira 回读一致，本地 phase 为 `local_finalized`。
- 第二次执行复用同一 run、Comment 和已完成 transition，没有重复副作用。
- 请求轨迹中不存在 Agentic Jira Custom Field 的读取或写入。
- 所有业务 Jira 写入均可归因到 developer `ao-work`，AO-51 的维护 Jira 回写均可归因到 `ao-maint`。

若没有满足条件的受控 TAP 卡片、身份/映射不一致、写入结果不确定或无法证明工作面隔离，则真实 E2E 结论为 `blocked`，不得用 Fake Jira 或浏览器手工写入替代。

## 5. 验证计划

先运行目标测试：

- 接管 CLI、Fake Jira Saga 和本地状态机单测。
- 能力目录、标准资产、task-to-PR 接管证据与工作面边界测试。
- 新工作空间初始化、developer-only 安装继承和旧入口拒绝测试。
- 受控 TAP 真实接管 E2E（仅在独立风险授权后）。

最后执行固定完整验证：

```sh
bash maintainer/scripts/test-python-runtime.sh
bash maintainer/scripts/test-resources.sh
bash developer/tests/bootstrap/test_install_boundary.sh
bash maintainer/scripts/test-release-workflow.sh
```

## 6. 审查决策与残留风险

本次设计审查需要确认：

1. AO-51 删除到期的隐藏 `ao-work task takeover` 兼容入口，不再保留第二个发布窗口。
2. 真实 TAP E2E 只验收接管 Saga，不扩张为业务代码到 PR；task-to-PR 接管证据用自动化测试验收。
3. 真实 E2E 必须另行给出具体 TAP Key，并在执行前审查 Comment、Status 和本地状态副作用。
4. 跨工作空间并发锁继续延期；本次只证明单工作空间重复与恢复幂等，不声称并发互斥。

主要风险：

- 删除隐藏别名可能使尚未迁移的私有调用失败；仓库公开资产与安装产物已在 AO-48 切到顶层入口，本次以扫描和解析拒绝测试确认迁移完成。
- Jira Comment 是可见且不可删除的审计副作用，Status transition 可能影响团队看板；因此必须使用明确受控卡片。
- 若受控卡片初始已在执行状态，只能证明接纳存量与恢复，不能宣称新接管 transition 已经真实验证。
- 当前仍没有跨工作空间并发锁；真实并发需要出现需求后专题设计，AO-51 不通过测试技巧掩盖该风险。

设计确认后的工作项级连续执行授权覆盖上述资产修复、Runtime/测试调整、目标测试、四项固定验证、形成未推送本地 commit，以及必要的 AO-51 中文进度回写。真实 TAP E2E 的具体卡片和外部副作用、范围扩大、兼容入口继续保留、Saga 判定变化或并发锁设计仍需独立风险决策。
