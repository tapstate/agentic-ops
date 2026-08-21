# AgenticOps 完整设计能力边界

> **状态：现役能力边界。** 本文已于 2026-08 按 `Skill + Python Runtime + Shell Bootstrap + Rule`、`maintainer` / `developer` 双工作面和源码发布模型完成修订。旧 Go CLI、平台二进制、`install-resources/checksums.txt` 及其发布方式只存在于明确标注“冻结历史 / 迁移基线”的文档和 Git 历史中，不得用来解释现役实现。

## 1. 目的

本文定义 AgenticOps 完整设计的能力边界和设计决策，不展开落地推进记录。

## 2. 设计决策

2026-07-23 用户已确认：完整设计是 AgenticOps 当前必须遵守的能力边界，不再把真实 Jira 门禁、所有权绑定、工作流配置、策略、诊断和更新能力仅视为远期目标。

这条决策的含义是：

- 本地模拟流程只作为自动化回归验证入口，不定义 AgenticOps 的最终能力边界。
- 真实 Jira 写操作、Git 推送、创建拉取请求、合并和发布可以被 AgenticOps 支持，但必须默认受策略、门禁、人工确认和审计记录约束。
- AIAgent 不直接猜测 Jira 字段、Jira 状态、目标仓库、验证方式、任务分类或流程阶段；缺失映射时必须返回稳定缺口并引导人工补充。
- 标准资产必须能通过工作流配置、策略、运行手册、模板或发布资产更新持续演进；只有确认问题来自对应工作面的 Python Runtime 时，才进入 Runtime 修复发布路径。

## 3. 能力边界

### 操作契约

`developer/standards/contracts/operations/` 是业务机器可读操作契约的源头。操作契约必须能表达：

- 结构化输入和输出。
- 前置门禁。
- 稳定失败码。
- 人工动作。
- 副作用。
- 人工确认要求。
- 重试和重做规则。
- 允许执行的任务类型和阶段。

AIAgent 执行任务时面向操作契约工作，而不是直接面向 Jira / GitHub / Git 的底层事实和临场聊天上下文。

### 工作流配置与标准流程

工作流配置负责把 AgenticOps 标准字段、标准阶段、标准动作和具体项目流程连接起来。它必须覆盖：

- Jira 字段映射。
- Jira 状态和 `transition` 映射。
- 任务分类到标准流程的映射。
- Jira 空间到目标代码仓库的映射。
- 本地源码路径映射。
- 专业审查节点映射。
- 允许写操作和人工确认点。

Standard Process Registry 维护任务分类、标准流程、阶段标准、责任角色、所有权门禁、日志上报、重试重做和完成清理规则。

未知 Jira 状态、缺失字段映射、缺失任务分类或缺失标准流程时，AgenticOps 必须输出稳定缺口，不允许 AIAgent 猜测映射。

### Jira 适配器与所有权门禁

Jira 适配器必须隔离 Jira Cloud REST API、模拟 Jira 数据和具体团队 Jira 工作流差异。真实 Jira 适配器至少需要受控支持：

- 读取当前 Jira 用户。
- 搜索负责人名下任务。
- 读取 Jira 卡片。
- 写入受控 Jira 评论。
- 执行受控字段写入。
- 读取并执行受控 `transition`。

developer 任务接管必须检查 `Assignee`、任务分类、标准流程、入口状态、目标仓库、验收标准和验证方式。接管成功后，运行记录必须包含 `agentic_run_id`、`agent_id`、`takeover_kind`、已回读的 `takeover_comment_id`、`task_class`、`process_id`、`current_stage` 和 `agentic_next_action`。

执行过程中如 `Assignee` 变更、本地运行身份或授权绑定不一致，AIAgent 必须停止并记录原因。任务完成或明确交接后，必须通过受控操作写入中文终态 Comment 并关闭本地 run。AO maintainer 工作面的 Agentic 字段规则由其独立设计约束，不套用于 developer。

### 问题修复与同步

AgenticOps 正式给研发日常使用前，必须具备成熟问题修复路径。修复路径按问题类型选择载体：

- `ao-work` 逻辑错误：通过新的 latest developer 安装修复。
- Jira 流程状态不适配：通过工作流配置更新修复。
- Jira 卡片属性缺失：阻断接管并输出补全模板。
- 关键步骤门禁调整：通过策略更新修复，并保留人工确认和审计记录。

诊断与修复能力必须使用结构化输出，且诊断包不得包含 secrets、tokens、private keys、原始 Jira 描述、原始敏感日志或敏感代码片段。

### 更新、发布与版本治理

AgenticOps 采用 latest-only 支持策略：BUG 只在最新版本修复，不维护旧版本补丁线。现役交付是固定 Git ref 下的 Python 源码、锁文件、Skill、Rule 和标准资产，不构建项目自有平台二进制，也不生成旧 `install-resources/checksums.txt`。版本由 annotated `vX.Y` Tag、不可变 commit、锁文件和本地发布审计共同追溯；资源合同和固定完整验证负责确认交付集合，不得以历史二进制清单或 checksum 冒充现役发布证据。

更新机制必须确保：

- 必要更新只阻断受影响操作。
- managed clone 切换到固定 ref 前完成来源、目标提交、锁文件和交付边界校验。
- 安装失败或新版本不可用时允许本地恢复。
- 发布动作受权限、策略、人工确认和审计约束。

正常发布固定为 `develop -> main`。硬门禁模式在完整验证和最终人工确认后可以为同一固定 HEAD 创建或复用 PR，并启用 GitHub Auto-merge；真正合入仍由无 bypass 的 `main` Ruleset、独立人工批准、CI 和审查线程条件裁决，不是 AIAgent 自主合并。GitHub Free 私有仓库无法提供该硬门禁时，只能显式选择 soft gate：固定 `release/vX.Y -> main`，等待研发工程师人工 Merge commit，再校验合并事实并重新完成固定验证。不得把 soft gate、手工等待或历史“非 auto-merge”描述当成现役硬门禁默认流程。

跨版本兼容治理和发布权限治理属于正式化能力边界；具体落地不在本文展开。

## 4. 非目标

完整设计不默认引入：

- Web 控制台。
- 后台常驻进程。
- 自动分配任务。
- 脱离最终人工授权、Ruleset 和审查条件的自主合并。
- 自动发布。
- 自动修改公司规范。

在有效工作项级连续执行授权下，任务分支可以自动提交、推送并创建目标为 `develop` 的拉取请求；`master`、`main`、`develop`、`release/*` 及同类保护分支禁止自动推送。合并、发布、Git Tag、强推、历史改写和范围变化仍需独立人工确认。

如果需要引入这些能力，必须先形成独立用户决策，明确事实源、权限边界、审计要求、失败回滚路径和人工确认规则。

## 5. 需要用户决策的设计缺口

以下缺口不能直接写成默认计划；进入实施前必须由用户确认取舍：

- 发布权限由谁持有、如何授权、如何审计、如何回滚。
- 跨版本兼容治理的最低承诺范围。

已确认的本地执行、任务审计、任务分支自动推进和 Jira `transition` 裁决边界见 `docs/decision-log.md` 与 `docs/architecture/remaining-governance-design-v1.md`。尚未决策的发布事项仍保持阻断并输出稳定缺口、建议动作和所需人工角色。
