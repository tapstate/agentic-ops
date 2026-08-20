# Jira 任务状态流转设计（AIAgent 处理任务时变更任务卡片状态）

## 1. 目标与范围

本文定义 AgenticOps 两个工作面的「Jira 任务状态流转」能力：让 AIAgent / 项目维护者在处理任务的过程中，通过受控、可审计的方式把任务卡片流转到项目工作流中合适的既有状态（例如接管后进入执行状态、PR 提交后标记 `Pull Request Submitted`、阻塞时标记 `On Hold`、维护者批量推进子任务状态），并把状态变更作为真实 Jira 写操作纳入授权门禁与证据回写。

实施顺序（已确认）：**先完成 maintainer 面（`ao-maint jira transition`），再完成 developer 面（`ao-work jira transition`）**。两个面各自独立实现（`ao_maint` / `ao_work` 包，不跨面导入），但协议形态、D-037 匹配规则、快速适配路径保持一致，避免两套语义漂移。

设计准绳（已确认）：**Jira 状态流程和表单属性是易变的，设计必须提供快速适配路径**——状态名、transition 名/ID、自定义字段都可能随 Jira 站点工作流调整而变化，任何映射都不能硬编码进 Runtime，必须配置化、可渐进补充、失配时给出可直接照抄的诊断材料（详见 3.4）。

本文不改变：

- Jira 是任务事实源、状态由项目工作流决定的既有边界；
- 接管（`ao-work takeover`）的 `Assignee` 校验、受管接管 Comment 留痕与「待办 → 执行状态」流转行为；
- 完成态语义：AIAgent 不拥有合入权，默认不得把卡片置为 `完成 / Done`（详见决策点 D5）；
- 不新增任何 Jira 状态（decision-log「当前无需决策事项」已确认不新增 Jira 状态），只流转既有状态与既有 transition。

实现范围为「设计 + 方案评审」，本期不实现；实现阶段排期由评审确认后另行安排（按 D-048 惯例，排期由实现方安排）。

## 2. 现状与根因

### 2.1 developer 面

- `JiraClient.available_transitions()` 拉取可用 transition 列表、`execute_transition(issue_key, transition_id, comment)` 执行流转（可附带评论）、`update_issue_fields` 更新字段（`developer/runtime/src/ao_work/jira/client.py`）已存在。
- `ao-work takeover` 复用 `developer/runtime/src/ao_work/task_takeover.py` 服务校验经办人和状态映射，先写并回读受管中文接管 Comment，再用共享严格匹配器把卡片从等待状态流转到执行状态，回读确认后写本地接管记录。
- 处理过程中没有任何可用的状态变更能力：AIAgent 不能标记阻塞、不能标记 `Pull Request Submitted`、不能标记完成。
- jira 写协议已成熟且同构可复用：`jira comment / worklog / description` 全部走 `plan → apply → readback`（`service.py` 的 `WritePlan` / `WriteAttempt` 框架，含 idempotency key、plan 文件、授权引用、`jira_write_plan_tampered` 完整性校验），`jira/cli.py` 的 `_configure_write_actions` 统一注册。
- Project Profile（`developer/standards/projects/tapdata/profile.yaml`）已有 `statuses`（状态 → stage：`待办→waiting_takeover`、`正在进行/In Progress/Pull Request Submitted/Tests Passed→implementation`、`完成/Done→completed`）和 `transitions`（`start_progress.name=Start Progress`、`complete.name=Tests Pass`）；config model 加载为 `status_mapping` / `transition_mapping`。但 `transitions` 条目目前只有 `name`，缺少 D-037 要求的稳定 `id` / 来源 / 目标状态字段。
- `release_agent` 仍是 capability gap；其 developer 目标语义是写入终态 Comment 并关闭本地 run，不依赖 Jira Agentic 字段，也不作为本期 transition 能力依赖。

### 2.2 maintainer 面

- `ao-maint jira` 只有 `auth / inspect / comment / worklog`，没有 transition 子命令；现状状态推进是维护者人工调 Jira REST（`maintainer/.local/.env` 凭证 + `POST /rest/api/3/issue/<KEY>/transitions`，tapdata 站点实测 待办→已完成 id=41、待办→正在进行 id=31）。
- `maintainer/runtime/src/ao_maint/jira/client.py` 没有 `available_transitions` / `execute_transition` / `update_issue_fields` 方法，需要新增。
- `maintainer/runtime/src/ao_maint/jira/service.py` 已有与 developer 同构的 `WritePlan`（字段为 `maintainer_run_id`）/ plan / apply / readback 协议与 `decisions.ndjson` 审计，可直接复用框架。
- 连接配置 `maintainer/standards/connections/tapdata-cloud.yaml` 只有站点 + 认证环境变量引用，**没有任何状态 / transition 映射**——maintainer 面需要引入工作流映射配置（位置见决策点 D8）。

### 2.3 根因

状态变更是真实 Jira 写操作，必须像评论、工作日志一样纳入「计划 → 授权 → 执行 → 回读」的受控协议，不能裸暴露 `execute_transition`；D-037 的匹配规则（稳定 ID 优先、名称兜底需唯一且来源/目标状态匹配、禁止模糊匹配）尚无落地载体；Jira 状态流程与表单属性的易变性没有被设计成「配置层快速适配」，任何一次 Jira 工作流调整都会直接变成 Runtime 改动或人工操作，适配成本高。

## 3. 方案

### 方案 A（推荐）：通用 `jira transition` 能力（plan / apply / readback 同构），两个工作面按序落地

两个工作面都提供 `jira transition` 命令组，协议与各自既有的 `comment / worklog` 完全同构：

- `plan`：实时读取 issue 当前状态与 Jira 可用 transition 列表，结合工作流映射配置推导候选目标，产出计划文件（目标 transition 的 id/名称/来源/目标状态、可选说明评论内容、idempotency key、plan_id）。**plan 永不使用缓存，Jira 可用列表实时拉取**——状态流程漂移在 plan 时即暴露。
- `apply`：校验授权引用（`user-confirmation:<KEY>:<plan_id>`，maintainer 面沿用 `ao-maint` 既有授权引用格式）→ 按 D-037 规则匹配（见 3.3）→ `execute_transition`（可附带中文说明评论）→ 回读当前状态确认。
- `readback`：幂等回读当前状态，输出最终状态名 + changelog 时间作为锚点。

配套：

- 契约：developer 面 `developer/standards/contracts/operations/jira-transition.yaml`；maintainer 面独立契约（maintainer 契约目录）或复用同一份共享契约（按两面的契约存放约定确认）。
- D-037 匹配器：`ao_work` 与 `ao_maint` 各自独立实现同一规则（不跨面导入），developer 接管服务的匹配逻辑调用同工作面共享匹配器（行为兼容，见决策点 D6）。
- developer profile `transitions` 条目扩展 `id` / `from` / `to` 字段；maintainer 面新增工作流映射配置（见 3.4 与决策点 D8）。

### 方案 B（备选，不单独做）：主链路自动推进状态

在 `task-run` 关键节点自动调用底层 transition：`execute-github-pr-create` 成功后自动置 `Pull Request Submitted`、阻塞失败码出现时自动置 `On Hold`、任务完成审计后自动置完成态。

利弊：自动化程度高、少一次人工动作，但「何时变状态」被固化为规则，且自动推进天然缺少授权点——与「状态变更是受控写操作」的原则冲突；完成态语义（AIAgent 不 merge）又要求人在场。单独做方案 B 会造成授权空洞。

### 方案 C（推荐组合）：A 先行，B 作为后续增强

本期实现只交付方案 A（通用受控能力，maintainer → developer 顺序）；方案 B 不写入代码，作为后续增强由版本化 Skill 在流程中编排 `jira transition` 命令实现（Skill 层调用受控命令 = 有人工授权点，不绕过门禁）。设计文档为 B 预留 profile 配置形态（见 3.5），但不实现。

### 3.1 实施阶段（maintainer 先行）

**阶段一：maintainer 面（先）**

- `ao_maint/jira/client.py` 新增 `available_transitions` / `execute_transition`（可带评论）。
- `ao_maint/jira/service.py` 新增 transition 的 plan / apply / readback（复用 `WritePlan` 框架，操作名 `jira_transition`）。
- `ao_maint/jira/cli.py` 注册 `jira transition` 子命令。
- maintainer 面工作流映射配置（见 3.4 与决策点 D8），tapdata-cloud 连接下维护 AO / TAP 项目常用 transition 映射。
- 测试 `maintainer/tests/runtime/`（协议 + D-037 匹配 + 幂等 + 授权）。
- 真实冒烟（授权后）：AO 项目受控卡片验证一次流转 + 回读。

**阶段二：developer 面（后）**

- `ao_work/jira/transition.py` 新增（plan / apply / readback 编排，复用 `WritePlan` / `WriteAttempt`）。
- `ao_work` D-037 匹配器收敛（共享实现，developer 接管服务改用，行为兼容）。
- `ao_work/jira/cli.py` 注册 `jira transition` 子命令。
- profile `transitions` 扩展 `id` / `from` / `to`；能力目录登记 `jira_transition`（implemented）；契约新增。
- 测试 `developer/tests/runtime/test_jira_transition.py` + `test_takeover` 回归。
- 真实冒烟（授权后）：TAP 项目受控卡片验证（或与阶段一合并冒烟，按评审结论）。

### 3.2 协议与命令形态

复用各面 `jira service` 的 `WritePlan` 框架与 cli 注册方式，transition 特有参数：

- plan：`--issue-key`、`--target-status`（或 `--target-transition <key>`，二选一）、`--idempotency-key`、`--plan-file`、可选 `--comment-content-file`（中文说明评论）。
- apply：`--plan-file`、`--confirm-plan-id`、`--authorization-reference`、`--decision-summary`。
- readback：`--issue-key`、`--idempotency-key`、`--plan-file`、`--confirm-plan-id`。

maintainer 面额外支持 `--transition-id`（无映射配置时的安全精确路径，见 3.4）；developer 面不提供 `--transition-id`（AIAgent 语义层只用状态名 / transition key，见决策点 D4）。

### 3.3 D-037 匹配规则（两面对齐的共享语义，各自独立实现）

输入：issue 当前状态、Jira `available_transitions` 列表、工作流映射条目（key、name、id、from、to）、请求的目标状态 / transition key /（maintainer 面可选）transition id。

规则（严格顺序，任一不满足即阻断，禁止模糊匹配）：

1. 解析候选条目：请求 `--target-transition <key>` → 精确取映射 `[key]`；请求 `--target-status <状态名>` → 取 `to` 等于该状态的全部条目；maintainer 面 `--transition-id` → 直接校验该 id 存在于 Jira 可用列表（不经过映射）。
2. 候选条目声明稳定 `id`：校验该 `id` 存在于 Jira 可用 transition 列表，且条目 `from`（若声明）包含 issue 当前状态、`to`（若声明）等于目标状态；满足才采用。
3. 候选条目无 `id`：名称兜底要求 Jira 可用列表中同名 transition 恰好一个、条目 `from` 包含当前状态、`to` 等于目标状态；任一不满足阻断。
4. 候选重复、目标不符、当前不可用 → `jira_transition_mapping_gap` 阻断；apply 前重新拉取可用列表（计划不跨期生效）。
5. 执行后回读当前状态，与目标状态不一致 → `jira_transition_readback_mismatch` 阻断。

### 3.4 快速适配路径（易变适配，设计准绳）

Jira 状态流程与表单属性易变，适配必须发生在配置层，Runtime 不硬编码任何状态名、transition 名 / ID 或字段 ID。快速适配路径由四条机制组成：

1. **配置化**：状态 → stage 映射、transition key → {name, id, from, to}、字段 ID 映射全部放在版本化标准资产（developer 面 `profile.yaml`；maintainer 面工作流映射配置，见决策点 D8）。Jira 工作流调整后只改配置、走正常 story gate，不改 Runtime 代码。
2. **缺省可用、渐进补充**：映射条目未配 `id` 时按 D-037 名称兜底可运行（要求名称唯一 + 来源/目标匹配），配置补齐 `id` 后更稳；新状态未映射时 plan 提示「未映射状态」而非崩溃。maintainer 面在完全没有映射配置时可用 `--transition-id` 显式精确流转（安全退化路径），不阻塞维护者操作。
3. **诊断输出即适配材料**：映射失配 / 状态未知时，plan 输出与失败 details 携带对照材料——当前状态、Jira 可用 transitions 完整列表（id + 名称 + to + 是否可用）、工作流映射已配置条目、未映射的状态名清单；维护者可直接照抄补配置。适配修改因此是「抄材料 → 补配置 → 重跑」，不依赖重新描述问题。
4. **表单属性显式配置 + 降级记录**：只有项目流程确实要求的 transition 表单属性才进入 profile 映射；developer 接管不探测、不映射、不读写 Agentic Jira Custom Field。未配置的非必填属性跳过并记录，不阻断主流转。

### 3.5 主链路自动推进预留（不实现）

profile 预留 `agent_transition_limits` 配置形态（如允许进入 `completed` 的显式例外、允许自动推进的节点清单），供后续 Skill 编排 `jira transition` 时读取；本期不实现该配置的 Runtime 语义。

## 4. 安全边界（不弱化项）

- 真实 Jira 写必须有明确授权引用与审计：apply 必须 `user-confirmation:<KEY>:<plan_id>` 授权引用，决策记录写审计（maintainer 面 `decisions.ndjson`、developer 面本地任务事件），计划文件留档。
- 状态不得临场猜测：developer 面目标 transition 必须来自 profile 映射 ∩ Jira 可用列表；maintainer 面无映射时仅允许 `--transition-id` 显式精确指定（Jira 事实，不是猜测），禁止发明状态名或 transition 名。
- D-037 完整落地：稳定 ID 优先、名称兜底需唯一且来源/目标状态匹配、候选重复/目标不符/不可用/回读不一致一律阻断、禁止模糊匹配。
- 不弱化 `ao-work takeover`：`Assignee` 校验、受管 Comment 回读、状态 transition 回读和本地运行记录保持强制；共享匹配器不得绕过这些门禁。
- 不新增 Jira 状态、不改变项目工作流：只流转既有状态与既有 transition；快速适配只改映射配置，不允许通过配置「发明」Jira 不存在的状态或 transition。
- 完成态默认禁止 AIAgent 推进（AIAgent 无合入权），例外必须由 profile 显式声明并经人工确认；maintainer 面由维护者操作，不受该限制。

## 5. 失败码

新增（两个面命名风格一致）：

- `jira_transition_plan_failed`：plan 阶段无法读取 issue 或可用 transition。
- `jira_transition_mapping_gap`：目标状态 / transition key 未映射，或 D-037 规则不满足（复用 takeover 既有失败码语义；details 带 3.4-3 的适配对照材料）。
- `jira_transition_failed`：执行 transition 失败（与 `release-agent.yaml` 契约中既有失败码同名保持一致）。
- `jira_transition_readback_mismatch`：apply 后回读状态与目标状态不一致。

复用既有失败码：`jira_issue_read_failed`、`missing_jira_write_mapping`、`real_jira_confirmation_required`、`workflow_transition_not_allowed`、`missing_permission`、`jira_write_plan_tampered`（协议完整性）、`jira_idempotency_conflict`（同 idempotency key 复用）。

## 6. 组件变更

### 6.1 阶段一（maintainer 面，先）

- `maintainer/runtime/src/ao_maint/jira/client.py`：新增 `available_transitions` / `execute_transition`。
- `maintainer/runtime/src/ao_maint/jira/service.py`：新增 transition plan / apply / readback（复用 `WritePlan`，操作名 `jira_transition`）。
- `maintainer/runtime/src/ao_maint/jira/cli.py`：注册 `jira transition` 子命令（复用既有 `_configure_write_actions` 模式）。
- maintainer 面工作流映射配置（决策点 D8 定位置；默认 `maintainer/standards/connections/tapdata-cloud-workflow.yaml`，含 `statuses` / `transitions`，与连接配置同目录分离职责）。
- 测试 `maintainer/tests/runtime/test_maintainer_jira_transition.py`。
- 能力/契约：maintainer 面无能力目录（维持现状），契约按 maintainer 面约定补充或复用共享契约（评审确认）。

### 6.2 阶段二（developer 面，后）

- `developer/runtime/src/ao_work/jira/transition.py`（新增）：plan / apply / readback 编排。
- `developer/runtime/src/ao_work/jira/match.py`（新增）：D-037 匹配器（或放入 service.py，视评审结论）。
- `developer/runtime/src/ao_work/task_takeover.py`：`_transition_name_for` 改为调用共享匹配器，行为兼容（回归测试守护）。
- `developer/runtime/src/ao_work/jira/cli.py`：注册 `jira transition` 子命令。
- `developer/runtime/src/ao_work/config/model.py` + `loader.py`：`transition_mapping` 条目支持 `id` / `from` / `to` 字段。
- `developer/standards/contracts/operations/jira-transition.yaml`（新增）；`developer/standards/capabilities/operations.yaml` 登记 `jira_transition`（implemented）。
- `developer/standards/projects/tapdata/profile.yaml`：`transitions` 条目补齐 `id` / `from` / `to`（按 tapdata 真实工作流实测值）。
- 测试：`developer/tests/runtime/test_jira_transition.py`（plan/apply/readback、D-037 全分支、幂等、授权引用、回读不一致）、`test_takeover` 回归适配。

### 6.3 公共

- 文档：`developer/standards/handbooks/ai-employee-handbook.md` 第 5 节命令示例（CLI 参数变更必须同步修正指引文档，2026-08-18 已确认要求）、`docs/profiles/workflow-profile.md` 的 `transitions` 配置规则、`docs/runtime/python-runtime.md` 能力说明、maintainer 面运行文档（`ao-maint jira` 使用说明）。

## 7. 测试与验证

- 阶段一：maintainer 单测全过（`test_maintainer_jira_transition.py` + 既有回归）；真实 Jira 冒烟（授权后）验证 AO 项目受控卡片一次流转 + 回读。
- 阶段二：developer 单测全过（`test_jira_transition.py` + `test_takeover` 回归）；能力目录断言（`test_capability_catalog.py`）同步。
- 固定完整验证：`test-python-runtime.sh`、`test-resources.sh`、`test_install_boundary.sh`、`test-release-workflow.sh` 全过（实现引入运行代码时必须同批补充可执行验证）。
- 故事质量门禁：各阶段按 AO-23 走 `story impact → approve → verify` 全流程，先确认影响的故事。

## 8. 文档与故事

- `docs/architecture/jira-status-transition-design.md`（本篇）。
- `docs/decision-log.md`：评审确认后登记 D-0xx（实施顺序 maintainer 先行、通用 transition 能力形态、D-037 落地载体、完成态默认禁止 AIAgent、快速适配路径、主链路自动推进后续化）。
- `docs/development-engineers/` 相关指引、`ai-employee-handbook.md` 第 5 节命令示例、maintainer 运行文档同步。
- 用户故事：实现阶段新增/更新 developer 与 maintainer 用户故事并注册，覆盖「受控流转任务状态、授权引用、回读确认、完成态默认禁止、配置快速适配」的固定验收。

## 9. 决策点结论（评审载体，待确认）

| 决策点 | 选项 | 推荐 | 结论 |
| --- | --- | --- | --- |
| D1 能力形态 | A 通用命令先行 / B 自动推进 / C A+B 一次做 | A 先行，B 由 Skill 编排后续化 | 待确认 |
| D2 实施顺序 | maintainer 面先、developer 面后 / 同时 / developer 先 | **maintainer 先、developer 后（用户已定）** | 已确认 |
| D3 协议形态 | plan/apply/readback 同构（复用 WritePlan）/ 单命令 + 授权引用 | 同构 | 待确认 |
| D4 目标状态来源（developer） | 仅 profile 映射 ∩ Jira 可用列表 / 允许任意 Jira 可用状态 | 仅 profile 映射 ∩ Jira 可用列表 | 待确认 |
| D5 完成态 | AIAgent 默认禁止置完成/Done（人处理）/ 允许（profile 显式例外） | 默认禁止，profile 可声明例外；maintainer 不受限 | 待确认 |
| D6 takeover 匹配 | developer 阶段重构为共享 D-037 匹配器（行为兼容）/ 保持现状不动 | 共享匹配器 | 待确认 |
| D7 profile transitions | 扩展 id/from/to 字段（D-037 前提）/ 本期只支持名称匹配 | 扩展字段 | 待确认 |
| D8 maintainer 映射配置位置 | 独立工作流映射文件（如 `connections/tapdata-cloud-workflow.yaml`）/ 扩展 `tapdata-cloud.yaml` | 独立文件，与连接配置同目录分离职责 | 待确认 |
| D9 无配置退化路径 | maintainer 面允许 `--transition-id` 显式精确流转 / 必须预配置映射 | 允许 `--transition-id`（安全退化，不阻塞维护） | 待确认 |
| D10 说明评论 | apply 可选带中文状态说明评论 / 不带评论 | 可选带评论 | 待确认 |
| D11 快速适配形态 | 诊断对照材料 + 配置渐进补充 + 字段按名探测（3.4 四条机制）/ 仅配置化 | 3.4 四条机制（用户已定准绳） | 待确认 |
| D12 决策登记 | 确认后登记 decision-log D-0xx（AO-23 tag 提交本设计）/ 仅文档不入 decision-log | 登记 | 待确认 |
