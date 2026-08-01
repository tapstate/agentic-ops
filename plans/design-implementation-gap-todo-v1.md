# AgenticOps 设计实现差距代办

> **For agentic workers:** 本计划用于承接当前设计与实现的差距。执行时必须保持设计文档、操作契约、CLI、测试、运行资产和文档同步；涉及产品、流程、权限或事实源取舍的事项，必须先获得用户决策。

**Goal:** 将当前已确认的 AgenticOps 完整设计能力边界转化为可执行实现代办。

**Architecture:** 本计划是当前设计与实现差距的跟踪入口。已完成任务保留验证过的实现基线；部分实现任务同时列出已完成边界和剩余缺口；涉及产品、流程、权限或事实源取舍的事项保持待决策状态。历史计划只用于追溯，不作为当前能力清单。

**Tech Stack:** Go 1.22+、标准库优先、`gopkg.in/yaml.v3`、shell 仅用于安装、构建和 e2e 编排。

## Global Constraints

- 不把设计缺口直接写成默认实现；涉及产品、流程、权限或事实源取舍时先提示用户决策。
- stdout 只输出结构化 JSON；stderr 输出人类诊断日志。
- 真实 Jira 写操作、Git 推送、GitHub 拉取请求、合并和发布必须经过策略、门禁、人工确认和审计记录。
- AIAgent 不直接猜测 Jira 字段、状态、目标仓库、验证方式、任务分类或流程阶段。
- 任务级审计记录不能被本地反馈报告替代。
- shell 不承载 Jira、GitHub、Git、操作契约、策略门禁、证据或反馈业务逻辑。
- 每个实现任务必须补充或更新对应测试与 e2e 验证。

---

## 0. 现有计划状态

检查命令：

```sh
rg -n '^- \[ \]' plans
```

截至 2026-08-01，`resume-takeover`、问题修复基线、AO profile、反馈分析和独立 PR 证据入口本地基线已完成；当前仍待处理的是更新回滚与兼容治理、受控发布治理，以及列出的产品/权限/事实源决策。三组剩余治理事项的设计草案见 `docs/architecture/remaining-governance-design-v1.md`，实现状态以本文件和当前验证结果为准。

已有计划文件：

- `plans/implementation-plan-v1.md`
- `plans/full-design-implementation-plan-v1.md`
- `plans/problem-resolution-plan-v1.md`
- `plans/documentation-layering-cleanup-plan-v1.md`
- `plans/fact-source-convergence-plan-v1.md`
- `plans/install-init-configuration-guidance-plan-v1.md`
- `plans/ao-agentic-defect-jira-configuration-plan.md`

## 1. 可直接推进的实现代办

### Task 1: 补齐 `resume-takeover` 恢复门禁

**Design source:**
- `install-resources/basic/contracts/operations/resume-takeover.yaml`
- `docs/architecture/full-design-implementation-design.md`

**Current status:**
- 本任务已完成，不再作为当前剩余实现差距；后续只做回归验证。
- 已通过统一 `RunContextReader` 按 `agentic_run_id` 恢复历史上下文，并校验不可变事实、最近任务阶段、终态和人工门禁。
- fake 与真实 Jira 模式都会重新读取卡片；真实模式额外复核 `assignee` 和 `agentic_id`。
- 操作阶段由 `resume-takeover` 契约校验，Jira 状态映射后的业务阶段由 Standard Process Registry 校验。
- 历史 `target_repo` 存在时必须与当前 Jira/profile 解析结果一致；旧 run 缺失时允许使用当前确定性映射补齐。
- 可信任务级阻塞会生成中文 Jira 评论材料，并通过现有 `add-task-comment` 原子操作受控回写。

**Implementation evidence:**
- `packages/agentic-cli/internal/commands/resume-takeover/resume_takeover_cmd.go`
- `packages/agentic-cli/internal/runcontext/context.go`
- `packages/agentic-cli/internal/jira/resume_gate.go`
- `packages/agentic-cli/internal/clihandlers/task.go`
- `packages/agentic-cli/internal/clihandlers/resume_feedback.go`
- `packages/agentic-cli/internal/cli/task_command_test.go`
- `install-resources/basic/contracts/operations/resume-takeover.yaml`
- `tests/e2e/local-fake-flow.sh`

- [x] 实现按 `agentic_run_id` 读取历史事件并找出最近一次有效接管事件。
- [x] 校验历史事件中的 `workspace` 和当前命令 `--workspace` 一致。
- [x] 校验历史事件中存在 `issue_key`、`agent_id`、`agentic_id`、`task_class` 和 `process_id`。
- [x] 在真实 Jira 模式下重新读取 Jira 卡片，校验 `assignee` 和 `agentic_id` 仍匹配当前代理。
- [x] 从历史事件或当前 profile 映射恢复并校验 `target_repo`，不得在恢复时临场猜测。
- [x] 分别按操作契约和 Standard Process Registry 校验操作阶段与 Jira 映射后的业务流程阶段。
- [x] 当本地事件缺失或不一致时返回稳定错误码：`run_not_found`、`workspace_mismatch`、`local_state_mismatch`。
- [x] 增加单元测试覆盖成功恢复、run 缺失、workspace 不匹配和本地状态不完整。
- [x] 更新 e2e 验证恢复输出包含 `previous_stage`、`current_stage` 和 `agentic_next_action`。

**Verification:**

```sh
go test ./packages/agentic-cli/internal/cli ./packages/agentic-cli/internal/feedback
bash tests/e2e/local-fake-flow.sh
```

### Task 2: 补齐证据写入与完成清理的审计闭环

**Design source:**
- `install-resources/basic/contracts/operations/write-evidence.yaml`
- `install-resources/basic/contracts/operations/release-agent.yaml`
- `docs/architecture/agenticops-current-design.md`

**Current status:**
- 已实现证据模板读取、任务上下文解析、策略门禁、完成证据校验和任务级审计状态记录。
- `feedback.Event` 已包含 `audit_target`、`audit_submitted` 和 `audit_reference`。
- 本任务的本地基线已经完成，后续只做回归验证。

**Implementation evidence:**
- `packages/agentic-cli/internal/evidence/`
- `packages/agentic-cli/internal/feedback/`
- `install-resources/basic/contracts/operations/write-evidence.yaml`
- `install-resources/basic/contracts/operations/release-agent.yaml`
- `install-resources/basic/templates/`
- `tests/e2e/local-fake-flow.sh`

- [x] 为 `feedback.Event` 增加 `audit_target`、`audit_submitted`、`audit_reference` 安全字段。
- [x] `write-evidence` 根据 run 事件读取 `issue_key`、`task_class`、`process_id`、当前阶段和目标仓库。
- [x] `write-evidence` 校验证据模板存在，不存在时返回 `evidence_template_missing`。
- [x] `write-evidence` 读取 `install-resources/basic/policies/default.yaml` 校验 Jira 评论或本地证据写入；门禁阻断时返回 `policy_gate_required`。
- [x] `release-agent` 校验 `agentic_run_id` 存在、`agentic_completion_evidence` 文件存在或是已记录的审计引用。
- [x] `release-agent` 输出并记录任务级审计状态；未提交审计时不得把本地反馈报告当作事实源。
- [x] 增加单元测试覆盖 run 缺失、完成证据缺失和审计已提交。
- [x] 更新 e2e 覆盖本地审计引用和 `agentic_id_cleared=true`。

**Verification:**

```sh
go test ./packages/agentic-cli/internal/cli ./packages/agentic-cli/internal/evidence ./packages/agentic-cli/internal/feedback
bash tests/e2e/local-fake-flow.sh
```

### Task 3: 补齐标准流程注册处和工作流配置深度校验

**Design source:**
- `docs/architecture/full-design-implementation-design.md`
- `install-resources/basic/contracts/processes/development-change-v1.yaml`
- `install-resources/basic/projects/tapdata/profile.yaml`

**Current status:**
- 已实现 process loader、process/profile 引用校验、接管入口阶段校验、任务分类映射和目标仓库 fallback。
- 已校验 `review_gates`、`retry_redo` 与 process stage、`agentic_next_action` 的一致性。
- 本任务基线已经完成，后续只做回归验证。

**Implementation evidence:**
- `packages/agentic-cli/internal/process/`
- `packages/agentic-cli/internal/profile/`
- `packages/agentic-cli/internal/jira/`
- `install-resources/basic/contracts/processes/`
- `install-resources/basic/projects/tapdata/profile.yaml`

- [x] 增加 process loader，读取 `install-resources/basic/contracts/processes/*.yaml`。
- [x] `profile validate` 校验所有 `standard_process_mapping` 目标都有对应 process 文件。
- [x] 校验 Jira status 映射结果符合 process `entry_stage` 或允许接管阶段。
- [x] 为 Jira issue model 增加 labels / components，真实 Jira 和 fake Jira 均映射。
- [x] `taskClassFor` 按 issue type、label、component 顺序解析任务分类，并输出映射来源。
- [x] 实现 `target_repo` fallback：字段缺失时按 component / label / issue type / default repository 映射。
- [x] 校验 `review_gates`、`retry_redo` 引用的 stage 和 `agentic_next_action` 与 process 定义一致。
- [x] 增加单元测试覆盖缺失 process、非法入口阶段、label 映射、component 映射、repo fallback 和映射来源输出。

**Verification:**

```sh
go test ./packages/agentic-cli/internal/profile ./packages/agentic-cli/internal/process ./packages/agentic-cli/internal/jira
go test ./...
```

### Task 4: 建立 Git / GitHub 受控操作基线

**Design source:**
- `docs/architecture/agenticops-current-design.md`
- `docs/runtime/cli-runtime.md`
- `install-resources/basic/policies/default.yaml`

**Current status:**
- 已实现 `inspect-workspace`、`prepare-pr`、`read-pr-comments`、`fix-pr-comments`、`check-ci-status` 和独立 `write-pr-evidence` 的受控基线。
- 策略门禁已读取 `install-resources/basic/policies/default.yaml`，高风险动作在未满足人工确认和审计条件时保持阻断。
- `write-pr-evidence` 独立读取 GitHub PR、CI 和 Review 事实，写入本地 PR 证据和审计事件；真实 Jira 评论仍沿用现有策略与显式确认门禁。
- 自动 push、创建或更新拉取请求和 merge 等副作用仍未开放。

**Implementation evidence:**
- `packages/agentic-cli/internal/git/`
- `packages/agentic-cli/internal/github/`
- `install-resources/basic/contracts/operations/inspect-workspace.yaml`
- `install-resources/basic/contracts/operations/prepare-pr.yaml`
- `install-resources/basic/contracts/operations/read-pr-comments.yaml`
- `install-resources/basic/contracts/operations/fix-pr-comments.yaml`
- `install-resources/basic/contracts/operations/check-ci-status.yaml`

**Contract-only gap:**
- `install-resources/basic/contracts/operations/write-pr-evidence.yaml` 已存在，但命令注册表中没有 `write-pr-evidence`。
- 需要确认该契约应新增 CLI 入口，还是由现有 `write-evidence` 统一承载拉取请求证据后删除重复契约。

**Design status:**
- 研发工程师已确认保留两个契约和两个入口；该项已按设计实现并完成契约、CLI 单元测试和资源校验。

- [x] 实现只读 Git 检查：`inspect-workspace` 输出 branch、dirty status、changed files、安全摘要。
- [x] 实现 `prepare-pr` 只生成结构化拉取请求计划，不自动创建拉取请求。
- [x] 实现 GitHub 读取型接口：读取拉取请求审查意见和 CI 状态。
- [x] `fix-pr-comments` 输出按评论分类后的修复计划，并要求人工确认后再进入修改。
- [x] 策略门禁读取 `install-resources/basic/policies/default.yaml`，不再只靠硬编码。
- [x] 对 `git_commit`、`git_push`、`create_pr`、`merge` 等高风险动作返回 `policy_gate_required`，直到人工确认路径具备审计记录。
- [x] 增加契约验证、CLI 单元测试和 fake `gh` / fake git 测试。
- [x] 明确 `write-pr-evidence` 与 `write-evidence` 的职责边界，并让机器可读契约与命令注册入口保持一致。

**Verification:**

```sh
go test ./packages/agentic-cli/internal/git ./packages/agentic-cli/internal/github ./packages/agentic-cli/internal/policy ./packages/agentic-cli/internal/cli
go test ./...
```

### Task 5: 加深 `preflight` 和 `doctor` 环境检查

**Design source:**
- `docs/runtime/cli-runtime.md`
- `docs/architecture/agenticops-current-design.md`

**Current status:**
- `preflight` 已检查 OS、CPU 架构、CLI 版本、Git、GitHub CLI、工作流配置和当前目录边界。
- `doctor` 已检查 CLI / 资产版本、本地路径，并保留显式 opt-in 的真实 Jira 和 GitHub 外部检查。
- 本任务基线已经完成，后续只做回归验证。

**Implementation evidence:**
- `packages/agentic-cli/internal/cli/`
- `packages/agentic-cli/internal/workspace/`
- `packages/agentic-cli/internal/config/`
- `install-resources/basic/contracts/operations/preflight.yaml`
- `install-resources/basic/contracts/operations/doctor.yaml`

- [x] `preflight` 检查 OS、CPU 架构、当前 CLI 版本、Git 可用性、GitHub CLI 可用性。
- [x] `preflight` 检查工作流配置存在且通过 `profile validate`。
- [x] `preflight` 检查当前目录是否位于工作流配置允许的项目 AI 工作空间或 source root。
- [x] `doctor` 检查 `current.json` 中的 CLI 版本、资产版本和当前运行版本是否一致或兼容。
- [x] `doctor` 检查 `local.source_root`、`local.runs_dir`、`local.feedback_dir` 是否存在或可创建。
- [x] 外部 Jira / GitHub 检查继续保持显式 opt-in，不默认访问外部服务。
- [x] 增加单元测试覆盖缺失 Git、缺失 source root、profile 失败、版本不匹配。

**Verification:**

```sh
go test ./packages/agentic-cli/internal/cli ./packages/agentic-cli/internal/workspace ./packages/agentic-cli/internal/config
bash tests/e2e/local-fake-flow.sh
```

### Task 6: 建立更新回滚、资产来源和跨版本兼容治理

**Design source:**
- `docs/architecture/full-design-implementation-design.md`
- `docs/runtime/problem-resolution-and-update.md`
- `docs/runtime/versioning.md`

**Current gap:**
- `update apply` 已支持远程清单、下载、校验和真实二进制切换，但没有 `update rollback`。
- release manifest 尚未表达最低兼容 CLI 版本、最低兼容资产版本、迁移策略和资产来源可信边界。
- `assets install` 只按 source directory 复制，不验证资产 manifest 与 CLI 兼容性。
- 必要更新的 `blocked_operations` 尚未进入所有 CLI 操作执行前的统一阻断检查。

**Design status:**
- 研发工程师已确认采用 `exact_pair`；版本关系、更新切换、回滚、资产来源和 required update guard 已按设计实施。

**Files:**
- Modify: `packages/agentic-cli/internal/update/manifest.go`
- Modify: `packages/agentic-cli/internal/assets/installer.go`
- Modify: `packages/agentic-cli/internal/cli/app.go`
- Modify: `packages/agentic-cli/internal/cli/app_test.go`
- Modify: `scripts/build.sh`
- Modify: `install-resources/basic/manifest.json`
- Create: `install-resources/basic/contracts/operations/update-rollback.yaml`
- Update: `install-resources/basic/contracts/operations/update-check.yaml`
- Update: `install-resources/basic/contracts/operations/update-apply.yaml`

- [x] 扩展 release manifest：`min_cli_version`、`min_asset_version`、`asset_source`、`compatibility_policy`、`migration_required`。
- [x] `update check` 输出兼容性判断和受影响操作。
- [x] `update apply` 在切换前保存可回滚的 previous metadata、previous binary path 和 SHA-256。
- [x] 实现 `update rollback`，仅用于安装失败或新版本不可用的本地恢复。
- [x] `assets install` 校验资产 manifest 与当前 CLI version / asset version 兼容。
- [x] 在 CLI 统一入口增加 required update 阻断检查，对 `blocked_operations` 中的操作返回稳定错误码。
- [x] 增加 update / assets 单元测试和构建、更新脚本测试。

**Verification:**

```sh
go test ./packages/agentic-cli/internal/update ./packages/agentic-cli/internal/assets ./packages/agentic-cli/internal/cli
bash scripts/test-build.sh
bash tests/e2e/problem-resolution-flow.sh
```

### Task 7: 受控发布权限与发布审计

**Design source:**
- `docs/architecture/full-design-implementation-design.md`
- `docs/runtime/problem-resolution-and-update.md`

**Current gap:**
- 当前仓库不存在可用的 `scripts/publish-release.sh`，历史计划中的完成结论已撤销。
- 当前不存在受控 `agentic-cli release publish` 操作、发布权限策略、人工确认记录、发布审计事件和回滚说明。
- 发布属于高风险动作，必须先确认发布责任人、授权方式、审计位置和回滚责任，再进入实现。
- shell 只能作为轻量调用包装，不能直接承载 GitHub 发布业务流程。

**Design status:**
- 已形成 release owner、显式确认、GitHub Release 事实源、本地审计引用和 fake 发布测试设计；发布权责和入口包装选择等待研发工程师确认后实施。

**Files:**
- Create: `install-resources/basic/contracts/operations/release-publish.yaml`
- Create: `packages/agentic-cli/internal/release/`
- Modify: `packages/agentic-cli/internal/cli/app.go`
- Modify: `packages/agentic-cli/internal/cli/app_test.go`
- Modify: `scripts/test-build.sh`
- Update: `install-resources/basic/policies/default.yaml`

是否需要新增轻量 shell 调用包装，必须在发布治理设计确认后决定；本计划不预设恢复旧脚本。

- [ ] 定义 `release_publish` 操作契约，声明输入、输出、失败码、副作用和人工门禁。
- [ ] 将发布权限校验、release 资产集合校验、发布审计事件写入 Go CLI operation。
- [ ] 发布治理设计确认需要包装时，shell 只保留对受控 `agentic-cli release publish` 的轻量调用；不需要包装时不新增脚本。
- [ ] 发布前要求显式确认 release version、目标 repository、资产清单和 checksum。
- [ ] 发布成功后输出 `audit_reference`，失败时输出可执行人工动作。
- [ ] 增加 fake GitHub CLI 测试，覆盖 create、upload、权限失败和资产缺失。

**Verification:**

```sh
go test ./packages/agentic-cli/internal/release ./packages/agentic-cli/internal/cli
bash scripts/test-build.sh
```

### Task 8: 补齐反馈分析与改进建议闭环

**Design source:**
- `docs/architecture/agenticops-current-design.md`
- `docs/workflows/feedback-loop.md`

**Current gap:**
- 已实现 `feedback report`、`feedback analyze` 和 `feedback propose` 的本地分析基线。
- `feedback report` 已支持按 `agentic_run_id`、`issue_key`、`task_type`、失败码、日期和时间范围过滤。
- 事件模型已增加 `resolution_type`、`resolution_version`、`resolution_status`，为后续修复效果追踪保留稳定字段。
- 分析和建议只写入项目 AI 工作空间，不自动修改 AgenticOps 源头规则、Jira 或 GitHub。

**Files:**
- Modify: `packages/agentic-cli/internal/feedback/report.go`
- Modify: `packages/agentic-cli/internal/feedback/event.go`
- Modify: `packages/agentic-cli/internal/clihandlers/feedback.go`
- Modify: `packages/agentic-cli/internal/clihandlers/exports.go`
- Modify: `packages/agentic-cli/internal/commandcatalog/zz_generated.go`
- Modify: `packages/agentic-cli/internal/cli/feedback_command_test.go`
- Modify: `packages/agentic-cli/internal/feedback/report_test.go`
- Create: `packages/agentic-cli/internal/commands/feedback/analyze/feedback_analyze_cmd.go`
- Create: `packages/agentic-cli/internal/commands/feedback/propose/feedback_propose_cmd.go`
- Create: `install-resources/basic/contracts/operations/feedback-analyze.yaml`
- Create: `install-resources/basic/contracts/operations/feedback-propose.yaml`
- Update: `install-resources/basic/contracts/operations/feedback-report.yaml`
- Update: `tests/e2e/problem-resolution-flow.sh`

- [x] `feedback report` 支持 `--run-id`、`--issue-key`、`--task-type`、`--code`、`--from`、`--to` 过滤。
- [x] `feedback analyze` 输出重复失败模式、人工确认热点、缺失字段趋势和建议修复载体。
- [x] `feedback propose` 输出结构化改进建议，但不自动修改 AgenticOps 源头规则。
- [x] 增加事件模型字段以支持修复效果追踪：`resolution_type`、`resolution_version`、`resolution_status`。
- [x] 增加单元测试和 e2e，覆盖缺失字段聚合、失败码过滤、建议输出。

**Verification:**

```sh
go test ./packages/agentic-cli/internal/feedback ./packages/agentic-cli/internal/cli
bash tests/e2e/problem-resolution-flow.sh
```

### Task 9: 接入 AO Jira 项目与 AgenticOps 测试代码工程

**Design source:**
- `docs/architecture/ao-agentic-defect-jira-workflow-design.md`
- `docs/configuration/ao-agentic-defect-jira-configuration.md`

**Implementation evidence:**
- `install-resources/basic/projects/ao/profile.yaml` 绑定 Jira project `AO`、工作类型 `Agentic 缺陷` 和 `agenticops_improvement_v1`。
- AO 默认代码仓库映射为 `tapstate/agentic-ops`，Jira 自定义字段使用已回读的 `customfield_10353`、`customfield_10360` 至 `customfield_10369`。
- `tests/e2e/ao-profile-flow.sh` 验证 AO profile 与新增反馈契约；`tests/e2e/problem-resolution-flow.sh` 使用当前 `agentic-ops` 源码目录作为测试工程。
- 本地 profile/e2e 验证不执行真实 Jira 写入；真实 AO 卡片只做显式只读回读。

- [x] 增加 AO project profile、任务分类、状态和 transition 映射。
- [x] 将默认测试代码工程映射为 `tapstate/agentic-ops`。
- [x] 增加 profile 单元测试、AO profile e2e 和资源校验。
- [x] 使用 Jira Cloud AO 项目元数据及 `AO-1` 只读回读结果核对项目、工作类型、字段和状态。

**Verification:**

```sh
go test ./packages/agentic-cli/internal/profile
bash tests/e2e/ao-profile-flow.sh
bash scripts/test-resources.sh
```

## 2. 需要用户先决策的事项

以下事项涉及产品、流程、权限或事实源取舍，不能直接写成默认实现任务；具体候选方案和推荐项见 `docs/architecture/remaining-governance-design-v1.md`：

- [ ] 是否引入 Web 控制台或后台常驻进程。
- [ ] 是否允许某些低风险场景自动创建拉取请求或自动推送。
- [ ] 发布权限由谁持有、如何授权、如何审计、如何回滚。
- [ ] 跨版本兼容治理的最低承诺范围。
- [ ] 任务级审计记录最终写入 Jira 卡片、审计服务还是目标仓库证据链。
- [ ] 真实 Jira 工作流中名称相同或含义冲突的 `transition` 如何裁决。

## 3. 建议推进顺序

1. 先确认 Task 4 的 `write-pr-evidence` 与 `write-evidence` 职责边界；未决前不新增运行入口，也不把孤立契约当作已实现能力。
2. 确认最低兼容承诺后推进 Task 6 的更新回滚、资产兼容校验和必要更新阻断。
3. 确认发布权责、授权方式、审计位置、回滚责任和 shell 包装选择后推进 Task 7。
4. Task 1、Task 2、Task 3、Task 5、Task 8 和 Task 9 只保留已完成基线和回归验证，不再作为待开发事项。

## 4. 总体验证入口

每完成一组任务后至少运行：

```sh
go test ./...
bash scripts/test-install.sh
bash scripts/test-build.sh
bash tests/e2e/local-fake-flow.sh
bash tests/e2e/problem-resolution-flow.sh
```
