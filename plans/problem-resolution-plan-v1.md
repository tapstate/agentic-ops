# AgenticOps 正式使用前问题修复计划

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
- 自动 push、自动 PR、自动 merge。

## 2. 必须实现的目标命令

```text
agentic-cli doctor --workspace <name>
agentic-cli feedback bundle --workspace <name> --run-id <run_id> --redact
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

- [ ] **Task 0: 架构适配性复核**
  - 复核 `docs/runtime/problem-resolution-and-update.md` 中的架构适配性评估。
  - 确认正式使用前不依赖历史 `rd-agentic` / `td-agentic` 项目作为事实源。
  - 确认所有设计、计划、目标都以当前 `agentic-ops` 仓库文档为准。

- [x] **Task 1: 稳定错误码与事件模型**
  - 为四类问题定义稳定 `code`。
  - 失败输出必须包含 `required_human_action`。
  - 事件日志记录 CLI version、asset version、operation、task_type、current_stage、next_action、code 和 gate 状态。
  - Implementation note: 当前已完成结构化失败输出基线，失败输出包含 `required_human_action`、`task_type`、`current_stage` 和 `next_action`；事件模型已包含 `agentic_cli_version`、`version_state`、`asset_version`、`code`、`gate` 和 `gate_status`。当前只覆盖已实现本地 fake flow 的命令，四类问题的完整业务 gate 分别在后续 Task 4、Task 5 和 Task 6 中继续落地。

- [ ] **Task 2: 脱敏诊断包**
  - 实现 `doctor`。
  - 实现 `feedback bundle --redact`。
  - 测试诊断包不包含 secrets、tokens、原始 Jira 描述、敏感代码片段。

- [ ] **Task 3: 二进制更新**
  - 定义 release manifest。
  - 实现 `update check / apply`。
  - 支持 `optional`、`recommended`、`required` 三种更新级别。
  - required update 只阻断受影响 operation。
  - 不维护旧版本补丁线；BUG 修复只进入新的 latest 版本。
  - 如实现 rollback，只用于安装失败或新版本不可用时的本地恢复。

- [ ] **Task 4: Workflow Profile 更新**
  - 实现 `profile validate / update / rollback`。
  - 支持 `status_mapping`、`transition_mapping`、`field_mapping` 校验。
  - 未知 Jira 状态必须返回 `unknown_jira_status`，不允许 AIAgent 猜。

- [ ] **Task 5: Jira 卡片属性缺失处理**
  - 对 owner、验收标准、目标仓库、验证方式、风险等级等必填项做 gate。
  - 缺失时停止接管。
  - 输出补全模板和 `required_human_action`。
  - 把 missing field 写入 feedback report 聚合。

- [ ] **Task 6: Policy / Gate 更新**
  - 实现 `policy validate / update / rollback`。
  - 支持 push、PR、Jira comment、scope change 等 gate 配置。
  - 放宽 gate 必须要求人工确认和决策记录。

- [ ] **Task 7: 端到端验收**
  - 增加 fake release manifest 测试。
  - 增加 profile hotfix e2e。
  - 增加 policy rollback e2e。
  - 增加 missing Jira field gate e2e。

## 4. 验收命令

正式使用前至少运行：

```sh
go test ./...
bash scripts/test-init.sh
bash tests/e2e/local-fake-flow.sh
bash tests/e2e/problem-resolution-flow.sh
```

`tests/e2e/problem-resolution-flow.sh` 需要在实现本计划时新增。

## 5. 完成标准

- 四类问题都有明确修复路径。
- 每条修复路径都有可执行命令。
- 每条修复路径都支持结构化输出。
- 当前架构适配性评估已完成，且不依赖历史项目作为当前事实源。
- 资产包更新不需要重新发布二进制。
- 二进制更新和资产包更新失败时可以本地恢复；BUG 修复不维护旧版本补丁线，只进入新的 latest 版本。
- 诊断包可交给维护者复现问题，且不包含敏感信息。
- feedback report 能统计问题是否减少。
