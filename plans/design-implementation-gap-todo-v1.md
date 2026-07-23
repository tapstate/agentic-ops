# AgenticOps 设计实现差距代办

> **For agentic workers:** 本计划用于承接当前设计与实现的差距。执行时必须保持设计文档、操作契约、CLI、测试、运行资产和文档同步；涉及产品、流程、权限或事实源取舍的事项，必须先获得用户决策。

**Goal:** 将当前已确认的 AgenticOps 完整设计能力边界转化为可执行实现代办。

**Architecture:** 当前 `plans/` 中既有计划已无未勾选项。本计划从 `docs/architecture/full-design-implementation-design.md`、`docs/architecture/agenticops-current-design.md` 和 `docs/runtime/problem-resolution-and-update.md` 对照 `packages/agentic-cli/`、`contracts/`、`profiles/`、`assets/`、`scripts/`、`tests/` 后形成。实现顺序优先补齐已经有契约但实现较浅的闭环，再推进 Git / GitHub 受控操作和发布治理。

**Tech Stack:** Go 1.22+、标准库优先、`gopkg.in/yaml.v3`、shell 仅用于安装、构建、发布脚本和 e2e 编排。

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
rg -n "^- \[ \]" plans
```

结果：当前 `plans/` 中没有未勾选项。

已有计划文件：

- `plans/implementation-plan-v1.md`
- `plans/full-design-implementation-plan-v1.md`
- `plans/problem-resolution-plan-v1.md`
- `plans/documentation-layering-cleanup-plan-v1.md`

## 1. 可直接推进的实现代办

### Task 1: 补齐 `resume-takeover` 恢复门禁

**Design source:**
- `contracts/operations/resume-takeover.yaml`
- `docs/architecture/full-design-implementation-design.md`

**Current gap:**
- 当前 `resume-takeover` 只校验 `--run-id` 并追加本地事件。
- 尚未读取已有 run summary / events。
- 尚未校验 `workspace`、`issue_key`、负责人、`current_agent_id`、目标仓库和可恢复阶段。

**Files:**
- Modify: `packages/agentic-cli/internal/cli/app.go`
- Modify: `packages/agentic-cli/internal/feedback/event.go`
- Modify: `packages/agentic-cli/internal/cli/app_test.go`
- Update if needed: `contracts/operations/resume-takeover.yaml`
- Update: `tests/e2e/local-fake-flow.sh`

- [ ] 实现按 `run_id` 读取历史事件并找出最近一次有效接管事件。
- [ ] 校验历史事件中的 `workspace` 和当前命令 `--workspace` 一致。
- [ ] 校验历史事件中存在 `issue_key`、`agent_id`、`current_agent_id`、`task_class` 和 `process_id`。
- [ ] 在真实 Jira 模式下重新读取 Jira 卡片，校验 `assignee` 和 `current_agent_id` 仍匹配当前代理。
- [ ] 当本地事件缺失或不一致时返回稳定错误码：`run_not_found`、`workspace_mismatch`、`issue_mismatch`、`local_state_mismatch`。
- [ ] 增加单元测试覆盖成功恢复、run 缺失、workspace 不匹配、真实 Jira 所有权冲突。
- [ ] 更新 e2e 验证恢复输出包含 `previous_stage`、`current_stage` 和 `next_action`。

**Verification:**

```sh
go test ./packages/agentic-cli/internal/cli ./packages/agentic-cli/internal/feedback
bash tests/e2e/local-fake-flow.sh
```

### Task 2: 补齐证据写入与完成清理的审计闭环

**Design source:**
- `contracts/operations/write-evidence.yaml`
- `contracts/operations/release-agent.yaml`
- `docs/architecture/agenticops-current-design.md`

**Current gap:**
- `write-evidence` 本地证据内容仍是固定模板，不读取任务上下文和证据模板。
- fake / local 模式未校验 run 所有权、证据模板存在和策略门禁。
- `release-agent` fake / local 模式未校验 run 存在、完成证据文件存在、任务级审计记录已提交或可追溯。
- 事件模型缺少 `audit_target`、`audit_submitted`、`audit_reference` 等字段，不能表达任务级审计提交状态。

**Files:**
- Modify: `packages/agentic-cli/internal/cli/app.go`
- Modify: `packages/agentic-cli/internal/evidence/writer.go`
- Modify: `packages/agentic-cli/internal/feedback/event.go`
- Modify: `packages/agentic-cli/internal/cli/app_test.go`
- Modify: `contracts/operations/write-evidence.yaml`
- Modify: `contracts/operations/release-agent.yaml`
- Update: `tests/e2e/local-fake-flow.sh`
- Update if needed: `assets/templates/`

- [ ] 为 `feedback.Event` 增加 `audit_target`、`audit_submitted`、`audit_reference` 安全字段。
- [ ] `write-evidence` 根据 run 事件读取 `issue_key`、`task_class`、`process_id`、当前阶段和目标仓库。
- [ ] `write-evidence` 校验证据模板存在，不存在时返回 `evidence_template_missing`。
- [ ] `write-evidence` 校验策略允许 Jira 评论或本地证据写入；门禁阻断时返回 `policy_gate_required`。
- [ ] `release-agent` 校验 `run_id` 存在、`completion_evidence` 文件存在或是已记录的审计引用。
- [ ] `release-agent` 输出并记录任务级审计状态；未提交审计时不得把本地反馈报告当作事实源。
- [ ] 增加单元测试覆盖证据模板缺失、完成证据缺失、审计已提交、审计缺失阻断。
- [ ] 更新 e2e 覆盖本地审计引用和 `current_agent_id_cleared=true`。

**Verification:**

```sh
go test ./packages/agentic-cli/internal/cli ./packages/agentic-cli/internal/evidence ./packages/agentic-cli/internal/feedback
bash tests/e2e/local-fake-flow.sh
```

### Task 3: 补齐标准流程注册处和工作流配置深度校验

**Design source:**
- `docs/architecture/full-design-implementation-design.md`
- `contracts/processes/development-change-v1.yaml`
- `profiles/tapstate.yaml`

**Current gap:**
- `profile validate` 只校验字段存在，尚未校验 `standard_process_mapping` 指向的 process 文件是否存在。
- `takeover-task` 只检查 Jira status 是否在 `status_mapping` 中，不校验其是否允许进入接管入口阶段。
- `taskClassFor` 只使用 `issue_type`，尚未使用 labels 映射。
- `target_repo` fallback 到 workspace repo mapping 尚未形成 CLI 校验和解析闭环。
- `review_gates`、`retry_redo` 与 process stage 的一致性尚未校验。

**Files:**
- Create: `packages/agentic-cli/internal/process/loader.go`
- Create: `packages/agentic-cli/internal/process/validator.go`
- Create: `packages/agentic-cli/internal/process/validator_test.go`
- Modify: `packages/agentic-cli/internal/profile/validator.go`
- Modify: `packages/agentic-cli/internal/jira/gate.go`
- Modify: `packages/agentic-cli/internal/jira/model.go`
- Modify: `packages/agentic-cli/internal/jira/real.go`
- Modify: `packages/agentic-cli/internal/jira/fake.go`
- Modify: `packages/agentic-cli/internal/jira/gate_test.go`

- [ ] 增加 process loader，读取 `contracts/processes/*.yaml`。
- [ ] `profile validate` 校验所有 `standard_process_mapping` 目标都有对应 process 文件。
- [ ] 校验 Jira status 映射结果符合 process `entry_stage` 或允许接管阶段。
- [ ] 为 Jira issue model 增加 labels / components，真实 Jira 和 fake Jira 均映射。
- [ ] `taskClassFor` 按 issue type、label、component 顺序解析任务分类，并输出映射来源。
- [ ] 实现 `target_repo` fallback：字段缺失时按 component / label / issue type / default repository 映射。
- [ ] 校验 `review_gates`、`retry_redo` 引用的 stage 和 `next_action` 与 process 定义一致。
- [ ] 增加单元测试覆盖缺失 process、非法入口阶段、label 映射、repo fallback。

**Verification:**

```sh
go test ./packages/agentic-cli/internal/profile ./packages/agentic-cli/internal/process ./packages/agentic-cli/internal/jira
go test ./...
```

### Task 4: 建立 Git / GitHub 受控操作基线

**Design source:**
- `docs/architecture/agenticops-current-design.md`
- `docs/runtime/cli-runtime.md`
- `assets/policies/default.yaml`

**Current gap:**
- `packages/agentic-cli/internal/git` 和 `packages/agentic-cli/internal/github` 目录尚未实现。
- `prepare-pr`、`fix-pr-comments`、`read-pr-comments`、`check-ci-status` 等设计中的受控操作尚无 CLI 路由和契约。
- `policy.RequiresHumanGate` 只硬编码少数操作，未读取 `assets/policies/default.yaml` 的门禁配置。
- 高风险 Git / GitHub 动作尚不能通过 AgenticCLI 输出结构化计划、阻断或人工确认记录。

**Files:**
- Create: `packages/agentic-cli/internal/git/`
- Create: `packages/agentic-cli/internal/github/`
- Modify: `packages/agentic-cli/internal/policy/gate.go`
- Modify: `packages/agentic-cli/internal/cli/app.go`
- Modify: `packages/agentic-cli/internal/cli/app_test.go`
- Create: `contracts/operations/inspect-workspace.yaml`
- Create: `contracts/operations/prepare-pr.yaml`
- Create: `contracts/operations/read-pr-comments.yaml`
- Create: `contracts/operations/fix-pr-comments.yaml`
- Create: `contracts/operations/check-ci-status.yaml`
- Create: `contracts/operations/write-pr-evidence.yaml`
- Update: `assets/policies/default.yaml`

- [ ] 实现只读 Git 检查：`inspect-workspace` 输出 branch、dirty status、changed files、安全摘要。
- [ ] 实现 `prepare-pr` 只生成结构化拉取请求计划，不自动创建拉取请求。
- [ ] 实现 GitHub 读取型接口：读取拉取请求审查意见和 CI 状态。
- [ ] `fix-pr-comments` 输出按评论分类后的修复计划，并要求人工确认后再进入修改。
- [ ] 策略门禁读取 `assets/policies/default.yaml`，不再只靠硬编码。
- [ ] 对 `git_commit`、`git_push`、`create_pr`、`merge` 等高风险动作返回 `policy_gate_required`，直到人工确认路径具备审计记录。
- [ ] 增加契约验证、CLI 单元测试和 fake `gh` / fake git 测试。

**Verification:**

```sh
go test ./packages/agentic-cli/internal/git ./packages/agentic-cli/internal/github ./packages/agentic-cli/internal/policy ./packages/agentic-cli/internal/cli
go test ./...
```

### Task 5: 加深 `preflight` 和 `doctor` 环境检查

**Design source:**
- `docs/runtime/cli-runtime.md`
- `docs/architecture/agenticops-current-design.md`

**Current gap:**
- `preflight` 当前输出仍是静态摘要，未实际检查 Git、GitHub CLI、Jira 凭证、工作流配置和当前业务仓库匹配关系。
- `doctor` 已支持显式外部检查，但尚未检查安装资产版本与当前 CLI 兼容性、workspace 覆盖配置、source root 是否存在。

**Files:**
- Modify: `packages/agentic-cli/internal/cli/app.go`
- Modify: `packages/agentic-cli/internal/workspace/workspace.go`
- Modify: `packages/agentic-cli/internal/config/paths.go`
- Modify: `packages/agentic-cli/internal/cli/app_test.go`
- Update: `contracts/operations/preflight.yaml` if created during implementation
- Update: `contracts/operations/doctor.yaml`

- [ ] `preflight` 检查 OS、CPU 架构、当前 CLI 版本、Git 可用性、GitHub CLI 可用性。
- [ ] `preflight` 检查工作流配置存在且通过 `profile validate`。
- [ ] `preflight` 检查当前目录是否位于工作流配置允许的项目 AI 工作空间或 source root。
- [ ] `doctor` 检查 `current.json` 中的 CLI 版本、资产版本和当前运行版本是否一致或兼容。
- [ ] `doctor` 检查 `local.source_root`、`local.runs_dir`、`local.feedback_dir` 是否存在或可创建。
- [ ] 外部 Jira / GitHub 检查继续保持显式 opt-in，不默认访问外部服务。
- [ ] 增加单元测试覆盖缺失 Git、缺失 source root、profile 失败、版本不匹配。

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

**Files:**
- Modify: `packages/agentic-cli/internal/update/manifest.go`
- Modify: `packages/agentic-cli/internal/assets/installer.go`
- Modify: `packages/agentic-cli/internal/cli/app.go`
- Modify: `packages/agentic-cli/internal/cli/app_test.go`
- Modify: `scripts/release.sh`
- Modify: `assets/manifest.json`
- Create: `contracts/operations/update-rollback.yaml`
- Update: `contracts/operations/update-check.yaml`
- Update: `contracts/operations/update-apply.yaml`

- [ ] 扩展 release manifest：`min_cli_version`、`min_asset_version`、`asset_source`、`compatibility_policy`、`migration_required`。
- [ ] `update check` 输出兼容性判断和受影响操作。
- [ ] `update apply` 在切换前保存可回滚的 previous metadata 和 previous binary path。
- [ ] 实现 `update rollback`，仅用于安装失败或新版本不可用的本地恢复。
- [ ] `assets install` 校验资产 manifest 与当前 CLI version / asset version 兼容。
- [ ] 在 CLI 统一入口增加 required update 阻断检查，对 `blocked_operations` 中的操作返回稳定错误码。
- [ ] 增加 update / assets 单元测试和 release 脚本测试。

**Verification:**

```sh
go test ./packages/agentic-cli/internal/update ./packages/agentic-cli/internal/assets ./packages/agentic-cli/internal/cli
bash scripts/test-build-release.sh
bash tests/e2e/problem-resolution-flow.sh
```

### Task 7: 受控发布权限与发布审计

**Design source:**
- `docs/architecture/full-design-implementation-design.md`
- `docs/runtime/problem-resolution-and-update.md`
- `scripts/publish-release.sh`

**Current gap:**
- `scripts/publish-release.sh` 可以创建或更新 GitHub Release，但没有发布权限策略、人工确认记录、发布审计事件或回滚说明。
- 发布属于高风险动作；当前仍由 shell 直接承载 GitHub 发布业务流程。

**Files:**
- Create: `contracts/operations/release-publish.yaml`
- Create: `packages/agentic-cli/internal/release/`
- Modify: `packages/agentic-cli/internal/cli/app.go`
- Modify: `packages/agentic-cli/internal/cli/app_test.go`
- Modify: `scripts/publish-release.sh`
- Modify: `scripts/test-build-release.sh`
- Update: `assets/policies/default.yaml`

- [ ] 定义 `release_publish` 操作契约，声明输入、输出、失败码、副作用和人工门禁。
- [ ] 将发布权限校验、release 资产集合校验、发布审计事件写入 Go CLI operation。
- [ ] shell 脚本只保留轻量包装，调用受控 `agentic-cli release publish` 或在测试模式下使用 fake `gh`。
- [ ] 发布前要求显式确认 release version、目标 repository、资产清单和 checksum。
- [ ] 发布成功后输出 `audit_reference`，失败时输出可执行人工动作。
- [ ] 增加 fake GitHub CLI 测试，覆盖 create、upload、权限失败和资产缺失。

**Verification:**

```sh
go test ./packages/agentic-cli/internal/release ./packages/agentic-cli/internal/cli
bash scripts/test-build-release.sh
```

### Task 8: 补齐反馈分析与改进建议闭环

**Design source:**
- `docs/architecture/agenticops-current-design.md`
- `docs/workflows/feedback-loop.md`

**Current gap:**
- 当前只有 `feedback report` 和 `feedback bundle`。
- 设计中出现的 `feedback analyze`、`feedback propose` 尚未实现。
- `feedback report` 只按 date 输出汇总，尚未支持按 `run_id`、`issue_key`、`task_type`、失败码或时间范围过滤。
- 尚未追踪修复前后问题是否减少。

**Files:**
- Modify: `packages/agentic-cli/internal/feedback/report.go`
- Modify: `packages/agentic-cli/internal/cli/app.go`
- Modify: `packages/agentic-cli/internal/cli/app_test.go`
- Create: `contracts/operations/feedback-analyze.yaml`
- Create: `contracts/operations/feedback-propose.yaml`
- Update: `contracts/operations/feedback-report.yaml`
- Update: `tests/e2e/problem-resolution-flow.sh`

- [ ] `feedback report` 支持 `--run-id`、`--issue-key`、`--task-type`、`--code`、`--from`、`--to` 过滤。
- [ ] `feedback analyze` 输出重复失败模式、人工确认热点、缺失字段趋势和建议修复载体。
- [ ] `feedback propose` 输出结构化改进建议，但不自动修改 AgenticOps 源头规则。
- [ ] 增加事件模型字段以支持修复效果追踪：`resolution_type`、`resolution_version`、`resolution_status`。
- [ ] 增加单元测试和 e2e，覆盖缺失字段聚合、失败码过滤、建议输出。

**Verification:**

```sh
go test ./packages/agentic-cli/internal/feedback ./packages/agentic-cli/internal/cli
bash tests/e2e/problem-resolution-flow.sh
```

## 2. 需要用户先决策的事项

以下事项涉及产品、流程、权限或事实源取舍，不能直接写成默认实现任务：

- [ ] 是否引入 Web 控制台或后台常驻进程。
- [ ] 是否允许某些低风险场景自动创建拉取请求或自动推送。
- [ ] 发布权限由谁持有、如何授权、如何审计、如何回滚。
- [ ] 跨版本兼容治理的最低承诺范围。
- [ ] 任务级审计记录最终写入 Jira 卡片、审计服务还是目标仓库证据链。
- [ ] 真实 Jira 工作流中名称相同或含义冲突的 `transition` 如何裁决。

## 3. 建议推进顺序

1. 先做 Task 1 和 Task 2，补齐已有契约对应的恢复、证据和完成清理闭环。
2. 再做 Task 3，让工作流配置和 Standard Process Registry 真正参与接管判断。
3. 接着做 Task 5 和 Task 6，补齐正式使用前的诊断、更新、回滚和兼容治理。
4. Task 4、Task 7、Task 8 分别推进 GitHub 协作、发布治理和反馈优化闭环；其中 Task 7 需要先确认发布权限责任人和审计位置。

## 4. 总体验证入口

每完成一组任务后至少运行：

```sh
go test ./...
bash scripts/test-init.sh
bash scripts/test-build-release.sh
bash tests/e2e/local-fake-flow.sh
bash tests/e2e/problem-resolution-flow.sh
```
