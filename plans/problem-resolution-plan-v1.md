# AgenticOps 正式使用前问题修复计划

> **状态：** 历史计划 / 已完成基线（2026-08-01）。本计划中的问题修复路径、诊断、更新、profile 和 policy 基线已落地；当前更新回滚、兼容治理和受控发布差距以 `plans/design-implementation-gap-todo-v1.md` 为准。

> **For agentic workers:** 本计划用于实现正式使用前的成熟修复路径。执行时必须保持文档、契约、CLI、测试和发布资产同步，不得把未实现能力描述为当前能力。

**Goal:** 让 AgenticOps 在正式给研发日常使用前，具备问题诊断、分类修复和快速同步能力；项目采用 latest-only 支持策略，BUG 只在最新版本修复，有新版本时推荐自动更新应用。

**Reference:** `docs/runtime/problem-resolution-and-update.md`

## 1. 范围

本计划覆盖四类问题：

- `agentic-cli` 逻辑错误。
- Jira 流程状态没适配。
- Jira 卡片属性丢失。
- 关键步骤门禁调整。

不在本计划中处理：

- Web 控制台。
- 后台 daemon。
- 自动修改公司规范。
- 自动推送、自动创建拉取请求、自动合并。

## 2. 必须实现的目标命令

```text
agentic-cli doctor --workspace <name>
agentic-cli feedback bundle --workspace <name> --run-id <agentic_run_id> --redact
agentic-cli update check
agentic-cli update apply
agentic-cli profile validate --workspace <name>
agentic-cli profile update --workspace <name>
agentic-cli profile rollback --workspace <name>
agentic-cli policy validate --workspace <name>
agentic-cli policy update --workspace <name>
agentic-cli policy rollback --workspace <name>
```

## 3. 实施任务

- [x] **Task 0: 架构适配性复核**
  - 复核 `docs/runtime/problem-resolution-and-update.md` 中的架构适配性评估。
  - 确认正式使用前不依赖历史 `rd-agentic` / `td-agentic` 项目作为事实源。
  - 确认所有设计、计划、目标都以当前 `agentic-ops` 仓库文档为准。
  - 实现说明：用户已决策采用完整设计作为当前必须实现边界；实现顺序从 `docs/architecture/full-design-implementation-design.md` 和 `plans/full-design-implementation-plan-v1.md` 开始，先完成机器可读操作契约验证基线，再继续工作流配置、Jira 所有权门禁、问题修复命令和完成清理。

- [x] **Task 1: 稳定错误码与事件模型**
  - 为四类问题定义稳定 `code`。
  - 失败输出必须包含 `required_human_action`。
  - 事件日志记录 CLI 版本、资产版本、操作、`task_type`、`current_stage`、`agentic_next_action`、`code` 和门禁状态。
  - 实现说明：结构化失败输出和事件字段基线已落地；当前可执行边界以源码、命令注册和自动化测试为准，未完成的更新兼容治理与受控发布不在本历史计划的完成结论内。

- [x] **Task 2: 脱敏诊断包**
  - 实现 `doctor`。
  - 实现 `feedback bundle --redact`。
  - 测试诊断包不包含 secrets、tokens、原始 Jira 描述、敏感代码片段。
  - 实现说明：`doctor` 已覆盖本地安装、版本、工作流配置、策略、Jira 适配器、GitHub 检查入口和工作空间；`feedback bundle --redact` 会生成脱敏诊断包，`tests/e2e/problem-resolution-flow.sh` 验证 `token` / `password` 被替换为 `[REDACTED]`。

- [x] **Task 3: 二进制更新**
  - 定义 release manifest。
  - 实现 `update check / apply`。
  - 支持 `optional`、`recommended`、`required` 三种更新级别。
  - 必要更新只阻断受影响操作。
  - 不维护旧版本补丁线；BUG 修复只进入新的 latest 版本。
  - 如实现 rollback，只用于安装失败或新版本不可用时的本地恢复。
  - 实现说明：`update check / apply` 已支持本地和远程清单、三种严重程度、`blocked_operations`、产物校验和远程二进制激活；`tests/e2e/problem-resolution-flow.sh` 验证必要更新与阻断操作输出。

- [x] **Task 4: Workflow Profile更新**
  - 实现 `profile validate / update / rollback`。
  - 支持 `status_mapping`、`transition_mapping`、`field_mapping` 校验。
  - 未知 Jira 状态必须返回 `unknown_jira_status`，不允许 AIAgent 猜。
  - 实现说明：`profile validate / update / rollback` 已落地，工作流配置校验覆盖状态、标准 `transition`、Jira `transition` 和字段映射；未知 Jira 状态会返回 `unknown_jira_status`，`tests/e2e/problem-resolution-flow.sh` 验证工作流配置热修复和回滚。

- [x] **Task 5: Jira 卡片属性缺失处理**
  - 对负责人、验收标准、目标仓库、验证方式、风险等级等必填项做门禁。
  - 缺失时停止接管。
  - 输出补全模板和 `required_human_action`。
  - 把缺失字段写入反馈报告聚合。
  - 实现说明：`takeover-task` 在模拟或已映射 Jira 卡片缺少必填项时停止接管，输出 `missing_field`、渲染后的 `completion_template` 和 `required_human_action`；事件日志写入 `missing_field`，`feedback report` 输出并写入缺失字段聚合。

- [x] **Task 6: Policy / Gate 更新**
  - 实现 `policy validate / update / rollback`。
  - 支持推送、创建拉取请求、Jira 评论、范围变更等门禁配置。
  - 放宽门禁必须要求人工确认和决策记录。
  - 实现说明：`policy validate / update / rollback` 已落地，默认策略覆盖 Jira 评论、`transition`、`git commit`、推送、创建拉取请求和范围变更门禁；真实 Jira 写入仍要求显式确认并记录门禁审计事件，`tests/e2e/problem-resolution-flow.sh` 验证策略热修复和回滚。

- [x] **Task 7: 端到端验收**
  - 增加 fake release manifest 测试。
  - 增加工作流配置热修复端到端测试。
  - 增加策略回滚端到端测试。
  - 增加缺失 Jira 字段门禁端到端测试。
  - 实现说明：已新增 `tests/e2e/problem-resolution-flow.sh`，并与 `tests/e2e/local-fake-flow.sh`、`tests/e2e/local-install-flow.sh` 共同覆盖问题修复路径和本地安装闭环。

## 4. 验收命令

正式使用前至少运行：

```sh
go test ./...
bash scripts/test-install.sh
bash tests/e2e/local-fake-flow.sh
bash tests/e2e/problem-resolution-flow.sh
```

`tests/e2e/problem-resolution-flow.sh` 已新增，用于集中验收问题修复路径。

## 5. 完成标准

- 四类问题都有明确修复路径。
- 每条修复路径都有可执行命令。
- 每条修复路径都支持结构化输出。
- 当前架构适配性评估已完成，且不依赖历史项目作为当前事实源。
- 资产包更新不需要重新发布二进制。
- 二进制更新和资产包更新失败时可以本地恢复；BUG 修复不维护旧版本补丁线，只进入新的 latest 版本。
- 诊断包可交给维护者复现问题，且不包含敏感信息。
- 反馈报告能统计问题是否减少。
