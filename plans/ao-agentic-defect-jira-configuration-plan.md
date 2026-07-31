# AO Agentic 缺陷 Jira 配置实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 Jira AO 团队管理业务空间中，把现有 `Agentic 缺陷` 表单切换到专用工作类型，并配置隔离的 Agentic 任务接管工作流及所需字段。

**Architecture:** Jira 项目状态是接管授权和任务阶段的事实源，专用工作类型隔离普通 `Task`、`故障` 和 `Sub-task`。表单只收集用户输入，运行字段由 AgenticOps 写入；工作流通过 `待接管`、`执行中`、`等待决策`、`待重新分配` 和 `已完成` 控制一次授权、决策等待、完成审计和重新分配。

**Tech Stack:** Jira Cloud 团队管理业务空间、Jira Forms、项目内工作类型和工作流、已登录 Chrome 会话、Jira Cloud REST 只读元数据接口。

## Global Constraints

- 设计源头是 `docs/architecture/ao-agentic-defect-jira-workflow-design.md`。
- 所有 Jira 人可见名称、说明和字段内容使用中文。
- 不创建无法绑定 AO 的全局 `Agentic 工作流`。
- 不修改 AO 现有 `Task`、`故障`、`Sub-task` 和 `Workstream` 的工作流。
- 不迁移、删除或批量更新现有 Jira 工作项。
- 每次 Jira 保存后立即重新读取配置；验证失败时停止，不继续叠加修改。
- 未获得单独确认前，不创建配置验证工作项。
- 不把 Jira 显示名当作运行时唯一标识；能读取的 field id、work type id、status id 和 transition name 必须记录。
- 六个运行字段沿用 AgenticOps 的 snake_case 协议名并配置简体中文翻译；自动化和 AgenticOps profile 只使用 field id。
- 不改动当前仓库中已有的 TapData 运行手册和校验和未提交变更。
- 本计划不实现 AgenticCLI、飞书通知、调度器或后台服务。
- 未收到“提交变更”指令时，不执行 Git commit 或 push。

---

## 文件与外部配置边界

**创建：**

- Jira AO 工作类型：`Agentic 缺陷`。
- Jira AO 项目内工作流：逻辑名称 `Agentic 工作流`，只绑定 `Agentic 缺陷`。
- Jira AO 项目字段：执行模式、Agentic ID、Agentic Run ID、Agentic Takeover Time、Agentic Next Action、Agentic Completion Evidence、Agentic Heartbeat Time、任务分支、待决策事项、决策截止时间、决策结果。
- `docs/configuration/ao-agentic-defect-jira-configuration.md`：配置完成后的实际 ID、名称、绑定关系和验证记录。

**修改：**

- Jira 表单 `Agentic 缺陷`（form id `68`）。
- `plans/ao-agentic-defect-jira-configuration-plan.md`：执行时逐项勾选。

**不修改：**

- 现有 AO 工作项。
- 现有 AO 普通工作类型的工作流。
- AgenticCLI 源码、operation contracts、project profiles 和安装资源。

---

### Task 1: 重新核对 AO 配置基线

**Interfaces:**

- Consumes: AO 空间管理权限、form id `68`、已确认设计文档。
- Produces: 本次修改前的工作类型、表单字段、工作流绑定、状态和转换基线。

- [x] **Step 1: 读取 AO 工作类型列表**

打开：

```text
https://tapdata.atlassian.net/jira/core/projects/AO/settings/issuetypes
```

确认存在 `Workstream`、`Task`、`故障`、`Sub-task`，且不存在 `Agentic 缺陷` 工作类型。

Expected: 与设计基线一致；如果 `Agentic 缺陷` 已存在，先检查字段和工作流，不重复创建。

- [x] **Step 2: 读取表单配置**

打开：

```text
https://tapdata.atlassian.net/jira/core/projects/AO/form/68/builder
```

确认表单名称为 `Agentic 缺陷`，当前创建 `Task`，字段为摘要、描述、附件。

Expected: 与设计基线一致；若已有人修改表单目标工作类型或字段，停止并比较差异。

- [x] **Step 3: 读取共享工作流**

从 `Task` 工作类型进入 `编辑工作流`，记录关联工作类型、初始状态和现有转换。

Expected: `Task`、`故障`、`Sub-task` 共享 `To Do / In Progress / Done` 工作流；只读检查后关闭编辑器，不保存。

- [x] **Step 4: 记录基线结论**

将基线写入执行记录，至少包含：

```text
project=AO
form_id=68
form_work_type=Task
shared_work_types=Task,故障,Sub-task
shared_statuses=To Do,In Progress,Done
shared_transitions=any-status-to-To Do,any-status-to-In Progress,any-status-to-Done
```

Expected: 后续每个隔离性验证都能与该基线比较。

---

### Task 2: 创建专用工作类型

**Interfaces:**

- Consumes: Task 1 基线。
- Produces: AO 项目内 `Agentic 缺陷` 工作类型和 work type id。

- [x] **Step 1: 打开添加工作类型入口**

在 AO 空间设置的“工作类型”区域选择“添加工作类型”。

Expected: 出现添加现有工作类型或创建工作类型的界面。

- [x] **Step 2: 填写工作类型**

使用以下内容：

```text
名称：Agentic 缺陷
描述：由 AgenticOps 接管、执行、等待决策并回写完成审计的研发缺陷任务。
层级：标准工作项
```

Expected: 名称不与现有 AO 工作类型冲突。

- [x] **Step 3: 保存工作类型**

执行 Jira 保存动作。

Expected: `Agentic 缺陷` 出现在 AO 工作类型列表中。

- [x] **Step 4: 回读并验证隔离**

重新打开工作类型列表，确认：

```text
Agentic 缺陷=存在
Workstream=存在
Task=存在
故障=存在
Sub-task=存在
```

Expected: 未删除、重命名或替换原工作类型。

- [x] **Step 5: 读取 work type id**

从工作类型设置 URL 或 Jira 项目工作类型只读接口读取 `Agentic 缺陷` 的 id。

Expected: 获得一个稳定数字 id，并写入执行记录；无法读取 id 时保留设置 URL 作为证据并在 Task 7 再读取。

执行结果：`Agentic 缺陷` work type id 为 `10103`；创建后默认加入原共享工作流，待 Task 6 拆分。

---

### Task 3: 创建执行与所有权字段

**Interfaces:**

- Consumes: `Agentic 缺陷` 工作类型。
- Produces: 表单执行模式字段和任务所有权、运行关联字段。

- [x] **Step 1: 创建执行模式字段**

在 `Agentic 缺陷` 工作类型中创建单选字段：

```text
显示名：执行模式
类型：单选
选项 1：研发模式
选项 2：无人值守模式
```

Expected: 字段只加入 `Agentic 缺陷` 工作类型。

- [x] **Step 2: 确认产品协议命名基线**

```text
agentic_id：当前锁持有者
agentic_run_id：本次运行标识
agentic_takeover_at：接管成功时间
agentic_next_action：下一步动作
agentic_completion_evidence：完成证据
agentic_heartbeat_at：锁持有者最近心跳时间
```

Expected: AgenticOps 手册、操作契约、事件和 profile 已统一迁移到 `agentic_` 前缀的 snake_case；不保留旧别名，不引入 camelCase 第二套术语，不使用 Jira 系统 `updated` 代替心跳。

执行结果：CLI JSON、事件模型、运行上下文、Jira 映射、操作契约、标准流程、profile、测试和人读文档已统一迁移；`takeover-task` 输出并记录 `agentic_heartbeat_at`，真实 Jira 字段模式会在同一次更新请求中写入五个接管事实并清空上一轮 `agentic_completion_evidence`。已通过 `go test ./...`、`bash scripts/test-build.sh`、`bash scripts/test-resources.sh` 和 `bash tests/e2e/local-fake-flow.sh`。

- [x] **Step 3: 创建管理员级运行字段**

在 Jira 全局字段管理中创建字段。管理员字段保持全局上下文以支持团队管理空间复用；实际使用范围通过 AO 项目字段关联和工作类型布局控制：

```text
Agentic ID：短文本，映射 agentic_id
Agentic Run ID：短文本，映射 agentic_run_id
Agentic Takeover Time：时间戳，映射 agentic_takeover_at
Agentic Next Action：短文本，映射 agentic_next_action
Agentic Completion Evidence：段落，映射 agentic_completion_evidence
Agentic Heartbeat Time：时间戳，映射 agentic_heartbeat_at
```

Expected: 六个字段可配置翻译并可由 AO 团队管理空间复用；默认值为空。

执行结果：

```text
Agentic ID：customfield_10364
Agentic Run ID：customfield_10365
Agentic Takeover Time：customfield_10366
Agentic Next Action：customfield_10367
Agentic Completion Evidence：customfield_10368
Agentic Heartbeat Time：customfield_10369
```

Jira 经典字段上下文不列出 AO 项目本地工作类型，因此不使用经典上下文伪造 AO 范围；六个字段只从 AO 项目字段页复用。

- [x] **Step 4: 将管理员字段加入专用工作类型**

从 AO 字段页复用六个管理员字段，并只加入 `Agentic 缺陷` 工作类型布局。

Expected: 普通 `Task`、`故障`、`Sub-task` 布局不增加这些字段。

执行结果：六个字段已加入 `Agentic 缺陷` 布局；首次保存遇到项目字段关联的最终一致性延迟，等待传播并刷新后保存成功。已逐一回读 `Task`、`故障`、`Sub-task`，其配置区均未出现这些字段。

- [x] **Step 5: 清理临时 camelCase 字段**

在执行 Jira 删除动作前单独确认，然后删除本次配置过程中创建且尚无数据的以下 AO 本地字段：

```text
agenticId
agenticRunId
agenticStarted
agenticAction
agenticResult
agenticUpdated
```

Expected: 只有确认六个新字段已加入 `Agentic 缺陷` 且字段 ID 已记录后才删除；不影响已有 Jira 工作项。

执行结果：在用户确认后删除六个无数据临时字段，并逐一回读确认字段列表中不再出现；Jira 提供 60 天恢复期，正式映射只保留 `customfield_10364` 至 `customfield_10369`。

- [x] **Step 6: 创建任务分支字段**

```text
显示名：任务分支
类型：短文本
用途：保存 Git 分支引用，不替代 Git 事实源
```

Expected: 字段默认值为空。

- [x] **Step 7: 回读字段布局**

重新打开 `Agentic 缺陷` 工作类型，确认至少出现：

```text
执行模式
Agentic ID
Agentic Run ID
Agentic Takeover Time
Agentic Next Action
Agentic Completion Evidence
Agentic Heartbeat Time
任务分支
```

Expected: 字段未被加入公开表单，且未修改其它工作类型布局。

执行结果：上述八个字段均存在于 `Agentic 缺陷`；公开表单尚未改动，普通工作类型布局保持不变。

---

### Task 4: 创建决策与审计字段

**Interfaces:**

- Consumes: `Agentic 缺陷` 工作类型。
- Produces: 无人值守决策等待和完成审计所需字段。

- [x] **Step 1: 创建决策请求字段**

```text
显示名：待决策事项
类型：段落
```

Expected: 可保存完整中文决策问题。

- [x] **Step 2: 创建决策截止时间字段**

```text
显示名：决策截止时间
类型：时间戳
```

Expected: 可保存精确到时间的十分钟截止点。

- [x] **Step 3: 创建决策结果字段**

```text
显示名：决策结果
类型：段落
```

Expected: 可保存用户回复或超时结论。

- [x] **Step 4: 回读字段布局**

确认 Task 3 和 Task 4 的十一个自定义字段全部存在，名称和类型正确。

Expected: 不存在重名字段；管理员字段必须通过 AO 项目字段页显式复用，并只加入 `Agentic 缺陷` 布局。

执行结果：执行模式、六个 Agentic 管理员字段、任务分支和三个决策字段共十一个字段均已在 `Agentic 缺陷` 布局回读成功。

- [x] **Step 5: 配置 Agentic 字段中文翻译**

在 Jira 全局字段管理中为六个英文默认名配置简体中文翻译：

```text
Agentic ID -> 当前 AIAgent
Agentic Run ID -> 运行 ID
Agentic Takeover Time -> 接管时间
Agentic Next Action -> 下一步动作
Agentic Completion Evidence -> 完成证据
Agentic Heartbeat Time -> 最近心跳时间
```

Expected: 用户语言为简体中文时显示中文；未配置语言回退到英文默认名。

- [x] **Step 6: 回读字段翻译**

从字段管理页重新打开六个字段的翻译配置，逐一确认英文默认名和简体中文翻译。

Expected: 翻译完整且 field id 未变化；如果当前账号没有 Jira 全局管理权限，停止并把此项记录为明确阻塞，不以重命名字段代替翻译。

执行结果：六个字段的 `中文 (中国)` 名称和中文描述均已保存并逐一回读；字段 ID 保持 `customfield_10364` 至 `customfield_10369` 不变。

---

### Task 5: 配置 `Agentic 缺陷` 表单

**Interfaces:**

- Consumes: form id `68`、`Agentic 缺陷` 工作类型、`执行模式` 字段。
- Produces: 创建专用工作类型的表单和必填输入约束。

- [x] **Step 1: 切换表单目标工作类型**

打开表单构建器，将顶部工作类型从 `Task` 改为 `Agentic 缺陷`。

Expected: 表单仍保留摘要、描述和附件；目标工作类型显示为 `Agentic 缺陷`。

- [x] **Step 2: 配置表单说明**

设置中文说明：

```text
请说明问题、预期结果、影响范围和已知限制。提交后，任务将进入 AgenticOps 待接管流程。
```

Expected: 说明在表单预览中可见。

- [x] **Step 3: 设置描述必填**

将“描述”字段设为必填。

Expected: 摘要和描述均显示必填标记，附件保持选填。

- [x] **Step 4: 添加执行模式**

把“执行模式”加入表单，并设置为必填。

Expected: 表单提供且只提供 `研发模式`、`无人值守模式` 两个选项。

- [x] **Step 5: 保存并回读表单**

等待 Jira 显示“已保存所有更改”，重新打开构建器并核对：

```text
目标工作类型=Agentic 缺陷
摘要=必填
描述=必填
附件=选填
执行模式=必填
```

Expected: 运行字段未出现在公开表单主要输入区。

- [x] **Step 6: 预览表单**

打开预览，只检查布局和必填标记，不提交。

Expected: 中文说明完整，字段无重复或截断，未创建 Jira 工作项。

执行结果：表单目标已切换为 `Agentic 缺陷`；摘要和执行模式由工作类型强制必填，描述已设为必填，附件保持选填。预览回读到完整中文说明及 `研发模式`、`无人值守模式` 两个选项，未提交表单。

---

### Task 6: 拆分并配置项目内工作流

**Interfaces:**

- Consumes: `Agentic 缺陷` 工作类型、Task 1 共享工作流基线。
- Produces: 只绑定 `Agentic 缺陷` 的项目内工作流。

- [x] **Step 1: 从专用工作类型进入工作流编辑器**

在 `Agentic 缺陷` 工作类型中选择“编辑工作流”。

Expected: 编辑器显示当前共享工作流和关联工作类型；此时不保存。

- [x] **Step 2: 添加五个专用状态**

按以下状态和类别创建：

```text
待接管：待办
执行中：进行中
等待决策：进行中
待重新分配：待办
已完成：完成
```

Expected: 每个状态只创建一次，名称完全一致。

- [x] **Step 3: 设置初始创建转换**

把“创建”转换的目标改为 `待接管`。

Expected: 新建 `Agentic 缺陷` 不会进入旧 `To Do`。

- [x] **Step 4: 创建主流程转换**

依次创建：

```text
接管任务：待接管 -> 执行中
请求决策：执行中 -> 等待决策
继续执行：等待决策 -> 执行中
完成任务：执行中 -> 已完成
```

Expected: 名称、来源和目标完全匹配设计。

- [x] **Step 5: 创建退出与重新授权转换**

依次创建：

```text
决策超时：等待决策 -> 待重新分配
结束接管：执行中 -> 待重新分配
重新分配：待重新分配 -> 待接管
重新打开：已完成 -> 待重新分配
```

Expected: `待重新分配` 不存在自动返回 `待接管` 的其它路径。

- [x] **Step 6: 删除专用工作流中的旧状态和任意状态转换**

从本次专用工作流中移除旧 `To Do`、`In Progress`、`Done` 及通往专用状态的任意状态转换。

Expected: 最终只保留五个专用状态、一个创建转换和八个命名转换；不得删除共享工作流本身的旧状态。

- [x] **Step 7: 更新工作流并隔离绑定**

选择“更新工作流”，在关联工作类型选择中只保留 `Agentic 缺陷`，取消 `Task`、`故障` 和 `Sub-task`。

Expected: Jira 创建或保存一个只作用于 `Agentic 缺陷` 的项目内工作流。如果保存界面支持名称，设置为 `Agentic 工作流`；否则保留 Jira 项目内名称并记录实际名称。

- [x] **Step 8: 回读专用工作流**

重新从 `Agentic 缺陷` 打开工作流编辑器，核对状态、转换和关联工作类型。

Expected:

```text
关联工作类型=Agentic 缺陷
状态数=5
创建转换目标=待接管
命名业务转换数=8
任意状态转换=0
```

- [x] **Step 9: 回读普通工作流**

分别从 `Task`、`故障` 和 `Sub-task` 读取工作流。

Expected: 三者仍使用 Task 1 记录的 `To Do / In Progress / Done` 共享工作流，没有 Agentic 专用状态。

执行结果：发布时只选择 `Agentic 缺陷`，Jira 项目内简化工作流编辑器不提供独立名称输入。专用工作流回读为五个状态、`Create -> 待接管` 和八个业务转换，无任意状态转换；状态 ID 为 `10177` 至 `10181`，转换 ID 为 `1` 至 `9`。普通 `Task` 回读仍为原三状态及三个任意状态转换，编辑器关联范围中已不包含 `Agentic 缺陷`。

---

### Task 7: 读取稳定 ID 并形成配置记录

**Files:**

- Create: `docs/configuration/ao-agentic-defect-jira-configuration.md`

**Interfaces:**

- Consumes: 已保存的工作类型、字段、表单和工作流。
- Produces: AgenticOps profile 后续可使用的实际 Jira 映射材料。

- [x] **Step 1: 读取 AO project id 和 work type id**

通过 Jira Cloud 只读项目元数据接口或设置 URL，记录固定值 `project_key=AO`、`work_type_name=Agentic 缺陷`、`form_id=68`，并把接口返回的项目数字 id 和工作类型数字 id 原样写入配置记录的 `project_id`、`work_type_id`。

Expected: 所有实际值来自 Jira 回读，不手工推测。

- [x] **Step 2: 读取自定义 field id**

从 Jira Cloud 字段只读接口按 AO scope 和字段名称解析十一个 field id。

Expected: 每个逻辑字段只有一个 AO 有效匹配；重名或 scope 不明确时停止并记录冲突。

- [x] **Step 3: 读取状态标识**

从 AO 工作类型或项目状态只读接口读取五个状态的 id 和名称。

Expected: 名称完全一致；无法读取 id 时记录 Jira 设置 URL 和名称，不伪造 id。

- [x] **Step 4: 记录转换名称**

记录八个业务转换名称。未创建验证工作项前不猜测 transition id。

Expected: profile 可以先按唯一 `name` 映射；transition id 留到 Task 8 验证工作项阶段读取。

- [x] **Step 5: 写入配置记录**

创建 `docs/configuration/ao-agentic-defect-jira-configuration.md`，包含：

```markdown
# AO Agentic 缺陷 Jira 配置记录

## 项目与表单
## 工作类型
## 字段映射
## 状态映射
## 转换映射
## 工作流绑定
## 验证结果
## 尚未执行的验证
```

Expected: 只写实际回读结果；没有值的 transition id 明确说明需通过验证工作项读取。

执行结果：Jira 项目元数据回读 `project_id=10248`、`work_type_id=10103`；创建元数据回读十一个字段 ID 和两个执行模式选项 ID；工作流编辑器回读五个状态 ID 和九个转换 ID。实际配置已写入 `docs/configuration/ao-agentic-defect-jira-configuration.md`，并区分配置级 ID 回读与尚未执行的实例级可用转换验证。

- [x] **Step 6: 验证文档**

Run:

```sh
rg -n "T[B]D|T[O]DO|待[定]|占[位]" docs/configuration/ao-agentic-defect-jira-configuration.md
git diff --check -- docs/configuration/ao-agentic-defect-jira-configuration.md
```

Expected: 第一条无输出，第二条退出码为 0。

执行结果：配置记录占位符扫描无输出，目标文档和全仓库 `git diff --check` 均通过。

---

### Task 8: 配置验证工作项人工检查点

**Interfaces:**

- Consumes: 已配置表单和工作流、Task 7 配置记录。
- Produces: 是否获准创建真实 Jira 验证工作项的明确决策。

- [x] **Step 1: 展示配置完成摘要**

向用户展示：工作类型、表单字段、工作流状态、转换、隔离验证和已读取 ID。

Expected: 用户能在创建任何测试数据前审查实际配置。

执行结果：配置摘要、字段 ID、状态 ID、转换 ID、工作流隔离证据和未执行的实例验证已写入配置记录并在本轮结果中展示。

- [x] **Step 2: 请求创建验证工作项的单独确认**

拟创建内容：

```text
摘要：[配置验证] Agentic 缺陷工作流
描述：验证 AO Agentic 缺陷表单、状态转换和重新分配门禁。该工作项不对应产品缺陷。
执行模式：研发模式
```

Expected: 只有用户明确确认后进入 Task 9；未确认时配置阶段结束，不创建工作项。

执行结果：用户明确回复“确认”，本轮只创建一张固定内容的配置验证工作项。

---

### Task 9: 创建并验证工作流实例

**Interfaces:**

- Consumes: 用户对验证工作项的单独确认。
- Produces: 表单创建、状态转换、transition id/name 和重新接管门禁的实例证据。

- [x] **Step 1: 通过表单创建验证工作项**

使用 Task 8 的固定内容提交 `Agentic 缺陷` 表单。

Expected: 创建一个 `Agentic 缺陷` 工作类型，初始状态为 `待接管`，执行模式为 `研发模式`。

- [x] **Step 2: 读取可用转换**

调用 Jira 工作项可用转换只读接口，记录 `接管任务` 的 transition id。

Expected: `待接管` 不提供直接进入 `已完成` 的转换。

- [x] **Step 3: 验证主流程**

按顺序执行并在每步回读状态：

```text
接管任务 -> 执行中
请求决策 -> 等待决策
继续执行 -> 执行中
完成任务 -> 已完成
```

Expected: 每一步只出现设计允许的目标状态。

- [x] **Step 4: 验证重新打开与重新分配**

按顺序执行并回读：

```text
重新打开 -> 待重新分配
重新分配 -> 待接管
```

Expected: `重新打开` 不直接回到 `待接管`；只有明确执行 `重新分配` 后重新获得接管资格。

- [x] **Step 5: 完成验证工作项**

再次执行：

```text
接管任务 -> 执行中
完成任务 -> 已完成
```

在工作项中留下中文验证说明，标明这是配置验证，不是产品缺陷。

Expected: 验证工作项最终处于 `已完成`，不删除工作项，保留审计证据。

- [x] **Step 6: 更新转换 ID 和验证记录**

把所有已读取 transition id、验证工作项 key 和每个状态回读结果写入 `docs/configuration/ao-agentic-defect-jira-configuration.md`。

Expected: 配置记录能够证明表单、工作类型、状态、转换和一次授权规则全部实际生效。

执行结果：表单创建 `AO-1`，实际工作类型为 `Agentic 缺陷`，初始状态为 `待接管`，执行模式为 `研发模式`。已回读每个状态的可用转换，并实际执行八个业务转换，包括主动结束接管和决策超时路径；最终再次执行 `接管任务 -> 完成任务`，工作项处于 `已完成`。中文验证评论 ID 为 `46508`，完整实例证据已写入配置记录。

---

### Task 10: 完成审计

**Interfaces:**

- Consumes: Jira 当前配置、配置记录、计划勾选状态。
- Produces: 本轮 Jira 配置是否完成的要求级证据。

- [x] **Step 1: 逐项核对设计验收标准**

核对：

```text
表单创建专用工作类型
摘要和描述必填
附件选填
执行模式必填且只有两个选项
专用工作流只绑定 Agentic 缺陷
五个状态存在
八个业务转换存在
任意状态转换不存在
普通工作类型工作流未变化
重新打开不直接授权接管
```

Expected: 每一项都有 Jira 回读或验证工作项证据。

- [x] **Step 2: 检查仓库范围**

Run:

```sh
git status --short
git diff --check
```

Expected: 本轮只新增设计、计划、配置记录和计划勾选；现有 TapData 运行手册与校验和改动保持原样。

- [x] **Step 3: 更新计划状态**

将已执行步骤勾选，未获得验证工作项确认时保留 Task 9 未完成并在配置记录中说明。

Expected: 计划状态与 Jira 实际状态一致，不以计划勾选代替 Jira 证据。

- [x] **Step 4: 汇报结果**

汇报：

```text
已创建和修改的 Jira 配置
工作流隔离验证
字段、状态和转换映射
验证工作项及最终状态（如已获确认）
未执行事项和原因
本地文件变化
```

Expected: 不声称未验证事项已经完成，不自动提交或推送仓库变更。

执行结果：已完成 Jira 配置级和实例级审计；验证工作项为 `AO-1`，最终状态为 `已完成`，全部业务转换均有 Jira changelog 证据。仓库复验执行了 `git status --short`、`find . -maxdepth 3 -type f | sort`、常见占位词扫描、`git diff --check` 和 `go test ./...`；Go 测试在允许本地回环监听的环境中全部通过。
