# AgenticOps 完整设计实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 AgenticOps 从本地 fake flow 升级为符合完整设计的受控 CLI Runtime。

**Architecture:** 先把 Operation Contract 变成可验证的机器可读源头，再接入 Workflow Profile、Standard Process Registry、Jira adapter、ownership gate、problem resolution commands 和完成清理。每个阶段都保留 fake adapter 作为本地测试入口，真实写操作必须受 policy / gate / confirmation 控制。

**Tech Stack:** Go 1.22+、标准库优先、`gopkg.in/yaml.v3`、shell 仅用于安装和 e2e 编排。

## Global Constraints

- CLI 统一入口为 `agentic-cli`。
- Go 是主实现语言；shell 只用于安装引导、轻量环境检测、下载或切换 Go release 二进制。
- `agentic-cli` 运行时不得依赖本地 Python、`jq` 或 shell 业务脚本。
- stdout 只输出结构化 JSON；stderr 输出人类诊断日志。
- 所有失败必须返回稳定 `code`、`message`、`required_human_action`、`task_type`、`current_stage` 和 `next_action`。
- secrets 不允许出现在 stdout、stderr、事件日志或诊断包中。
- `contracts/operations/` 是唯一机器可读 Operation Contract 源头。
- AIAgent 执行 Jira 任务前必须先识别 `task_class`，再选择 Standard Process Registry 中的 `process_id`。
- `agent_id` 是 AIAgent 唯一编号；`current_agent_id` 是任务运行中绑定字段，任务完成或交接结束后必须清理。
- 真实 Jira 写操作、Git push、GitHub PR 创建、merge 和发布必须经过 policy / gate / confirmation。
- 历史 `rd-agentic` / `td-agentic` 只作为参考来源，不作为当前事实源。

---

## 1. 目标文件结构

```text
contracts/
  operations/
    *.yaml
  processes/
    development-change-v1.yaml
profiles/
  tapstate.yaml
assets/
  profiles/
  policies/
  processes/
packages/agentic-cli/internal/
  contract/
  profile/
  process/
  jira/
  ownership/
  diagnosis/
  update/
  policy/
tests/e2e/
  problem-resolution-flow.sh
```

## 2. 阶段 1: Contract / Schema 基线

### Task 1: 扩展 Contract Model

**Files:**
- Modify: `packages/agentic-cli/internal/contract/model.go`
- Modify: `packages/agentic-cli/internal/contract/loader_test.go`

**Interfaces:**
- Produces: `contract.Operation`
- Produces: `contract.FieldSpec`
- Produces: `contract.FailureSpec`
- Produces: `contract.RetryPolicy`

- [x] **Step 1: Write failing loader test**

Add a test that loads `contracts/operations/takeover-task.yaml` and asserts:

```go
if op.Input["issue_key"].Required != true {
	t.Fatalf("issue_key required = %v, want true", op.Input["issue_key"].Required)
}
if !contains(op.Preconditions, "current_user_must_match_owner") {
	t.Fatalf("missing owner precondition: %#v", op.Preconditions)
}
if !contains(op.Failure.Codes, "assignee_mismatch") {
	t.Fatalf("missing assignee_mismatch failure code: %#v", op.Failure.Codes)
}
if op.RetryPolicy.Retryable != false {
	t.Fatalf("retryable = %v, want false for takeover gate failures", op.RetryPolicy.Retryable)
}
```

Run: `go test ./packages/agentic-cli/internal/contract`

Expected: FAIL because the current model does not expose these fields.

- [x] **Step 2: Implement model structs**

Update `model.go` so `Operation` includes:

```go
type Operation struct {
	Operation      string               `yaml:"operation"`
	Version        int                  `yaml:"version"`
	Purpose        string               `yaml:"purpose"`
	TaskType       string               `yaml:"task_type"`
	AllowedStages  []string             `yaml:"allowed_stages"`
	RequiredInputs []string             `yaml:"required_inputs"`
	Input          map[string]FieldSpec `yaml:"input"`
	Preconditions  []string             `yaml:"preconditions"`
	Output         map[string]FieldSpec `yaml:"output"`
	Failure        FailureSpec          `yaml:"failure"`
	SideEffects    []string             `yaml:"side_effects"`
	HumanGate      HumanGate            `yaml:"human_gate"`
	RetryPolicy    RetryPolicy          `yaml:"retry_policy"`
	RedoFromStage  string               `yaml:"redo_from_stage"`
}

type FieldSpec struct {
	Type     string   `yaml:"type"`
	Required bool    `yaml:"required"`
	Enum     []string `yaml:"enum"`
	Fields   []string `yaml:"fields"`
}

type FailureSpec struct {
	Codes []string `yaml:"codes"`
}

type RetryPolicy struct {
	Retryable bool   `yaml:"retryable"`
	MaxAttempts int  `yaml:"max_attempts"`
	RedoFromStage string `yaml:"redo_from_stage"`
}
```

- [x] **Step 3: Run contract tests**

Run: `go test ./packages/agentic-cli/internal/contract`

Expected: PASS after YAML is expanded in Task 2.

### Task 2: Expand Operation YAML

**Files:**
- Modify: `contracts/operations/takeover-task.yaml`
- Modify: `contracts/operations/resume-takeover.yaml`
- Modify: `contracts/operations/write-evidence.yaml`
- Modify: `contracts/operations/list-tasks.yaml`
- Modify: `contracts/operations/agent-init.yaml`
- Modify: `contracts/operations/workspace-init.yaml`
- Modify: `contracts/operations/assets-install.yaml`
- Modify: `contracts/operations/feedback-report.yaml`

**Interfaces:**
- Consumes: `contract.Operation`
- Produces: YAML fields consumed by `contract.LoadFile`

- [x] **Step 1: Expand `takeover-task.yaml`**

Add `input`, `preconditions`, `output`, `failure`, `retry_policy`, and `redo_from_stage` matching `docs/contracts/operation-contract.md`.

The failure code list must include:

```yaml
failure:
  codes:
    - owner_mismatch
    - assignee_mismatch
    - assignee_changed
    - agent_ownership_conflict
    - task_class_mapping_gap
    - missing_acceptance_criteria
    - missing_target_repo
    - missing_verification_method
    - missing_permission
    - workflow_transition_not_allowed
```

- [x] **Step 2: Expand resume and evidence contracts**

`resume-takeover.yaml` must include preconditions for existing run, workspace match, issue match, ownership check, and local state check.

`write-evidence.yaml` must include preconditions for run existence, ownership check, evidence template availability, and policy check.

- [x] **Step 3: Expand remaining current-operation contracts**

Every current operation YAML must include at least:

```yaml
input:
output:
failure:
  codes:
side_effects:
human_gate:
retry_policy:
```

- [x] **Step 4: Run contract tests**

Run: `go test ./packages/agentic-cli/internal/contract`

Expected: PASS.

### Task 3: Add Contract Validation

**Files:**
- Create: `packages/agentic-cli/internal/contract/validator.go`
- Create: `packages/agentic-cli/internal/contract/validator_test.go`

**Interfaces:**
- Consumes: `contract.Operation`
- Produces: `contract.Validate(op Operation) []ValidationIssue`

- [x] **Step 1: Write failing validation tests**

Test that validation returns issues when:

- `operation` is empty.
- `input` is empty.
- `output` is empty.
- `failure.codes` is empty.
- `side_effects` is empty.
- `human_gate` is omitted.

Run: `go test ./packages/agentic-cli/internal/contract`

Expected: FAIL because `Validate` does not exist.

- [x] **Step 2: Implement validator**

Implement:

```go
type ValidationIssue struct {
	Code string
	Message string
}

func Validate(op Operation) []ValidationIssue
```

Validation codes must use lowercase snake_case:

- `missing_operation`
- `missing_input`
- `missing_output`
- `missing_failure_codes`
- `missing_side_effects`
- `missing_human_gate`

- [x] **Step 3: Add repository contract test**

Add a test that loads all files in `contracts/operations/*.yaml` and requires `Validate(op)` to return no issues.

Run: `go test ./packages/agentic-cli/internal/contract`

Expected: PASS.

### Task 4: Add CLI Contract Validation Command

**Files:**
- Modify: `packages/agentic-cli/internal/cli/app.go`
- Modify: `packages/agentic-cli/internal/cli/app_test.go`
- Create: `packages/agentic-cli/internal/command/contract_validate.go`
- Add: `contracts/operations/contract-validate.yaml`

**Interfaces:**
- Produces CLI command: `agentic-cli contract validate`

- [x] **Step 1: Write failing CLI test**

Add test:

```go
code := Run([]string{"contract", "validate"}, &stdout, &stderr)
if code != 0 {
	t.Fatalf("code = %d stdout = %s", code, stdout.String())
}
assertJSONField(t, stdout.String(), "operation", "contract_validate")
assertJSONField(t, stdout.String(), "next_action", "continue")
```

Run: `go test ./packages/agentic-cli/internal/cli`

Expected: FAIL because command is unknown.

- [x] **Step 2: Implement command route**

Add route:

```go
case "contract":
	if len(args) >= 2 && args[1] == "validate" {
		return runContractValidate(args, stdout)
	}
```

`runContractValidate` loads all YAML files in `contracts/operations`, validates them, and outputs:

```json
{
  "ok": true,
  "operation": "contract_validate",
  "contracts": 10,
  "issues": 0,
  "next_action": "continue"
}
```

- [x] **Step 3: Run CLI tests**

Run: `go test ./packages/agentic-cli/internal/cli`

Expected: PASS.

### Task 5: Phase 1 E2E and Docs Sync

**Files:**
- Modify: `tests/e2e/local-fake-flow.sh`
- Modify: `docs/contracts/operation-contract.md`
- Modify: `plans/problem-resolution-plan-v1.md`

**Interfaces:**
- Consumes: `agentic-cli contract validate`

- [x] **Step 1: Add e2e assertion**

Add:

```sh
$cmd contract validate | grep '"operation":"contract_validate"'
```

Run: `bash tests/e2e/local-fake-flow.sh`

Expected: PASS.

- [x] **Step 2: Mark planning progress**

Update `plans/problem-resolution-plan-v1.md` Task 0 to checked after architecture fit is recorded, and add an implementation note that full design implementation now proceeds from contract validation first.

- [x] **Step 3: Verification**

Run:

```sh
go test ./...
bash scripts/test-init.sh
bash tests/e2e/local-fake-flow.sh
bash tests/e2e/local-release-install-flow.sh
```

Expected: all commands exit 0.

## 3. Later Phases

Phase 1 contract/schema baseline has passed and can be used by later phases.

## 4. 阶段 2: Profile / Process 映射

### Task 6: Profile Model and Validation Baseline

**Files:**
- Create: `profiles/tapstate.yaml`
- Create: `contracts/processes/development-change-v1.yaml`
- Create: `packages/agentic-cli/internal/profile/model.go`
- Create: `packages/agentic-cli/internal/profile/loader.go`
- Create: `packages/agentic-cli/internal/profile/validator.go`
- Create: `packages/agentic-cli/internal/profile/validator_test.go`
- Modify: `packages/agentic-cli/internal/cli/app.go`
- Modify: `packages/agentic-cli/internal/cli/app_test.go`

**Interfaces:**
- Produces: `profile.LoadFile(path string) (Profile, error)`
- Produces: `profile.Validate(p Profile) []ValidationIssue`
- Produces CLI command: `agentic-cli profile validate --workspace <name>`

- [x] **Step 1: Write failing profile validation test**

Test that `profiles/tapstate.yaml` loads and validates with zero issues:

```go
p, err := LoadFile(filepath.Join("..", "..", "..", "..", "profiles", "tapstate.yaml"))
if err != nil {
	t.Fatalf("LoadFile error = %v", err)
}
if issues := Validate(p); len(issues) != 0 {
	t.Fatalf("Validate issues = %#v", issues)
}
```

Run: `go test ./packages/agentic-cli/internal/profile`

Expected: FAIL because the profile package does not exist.

- [x] **Step 2: Create profile model and loader**

`Profile` must include `workspace`, `jira.project`, `jira.task_query`, `jira_form_mapping.fields`, `task_class_mapping.issue_types`, `standard_process_mapping`, `status_mapping`, `transition_mapping`, `github.organization`, `github.repositories`, `local.source_root`, `local.runs_dir`, `local.feedback_dir`, `human_gates`, `review_gates`, `retry_redo`, and `templates`.

- [x] **Step 3: Create default tapstate profile**

Create `profiles/tapstate.yaml` with mappings for:

- `Story -> feature_change`
- `Bug -> bug_fix`
- `Task -> technical_task`
- `feature_change`, `bug_fix`, and `technical_task -> development_change_v1`
- `To Do -> waiting_takeover`
- `In Progress -> implementation`
- `Done -> completed`
- `start_progress -> implementation`
- `complete -> completed`

- [x] **Step 4: Implement profile validator**

Validation must return stable codes:

- `missing_workspace`
- `missing_jira_project`
- `missing_task_query`
- `missing_form_mapping`
- `task_class_mapping_gap`
- `standard_process_mapping_gap`
- `lifecycle_mapping_gap`
- `transition_mapping_gap`
- `missing_local_source_root`

- [x] **Step 5: Add CLI failing test**

Test `Run([]string{"profile", "validate", "--workspace", "tapstate"}, ...)` returns:

```json
{
  "ok": true,
  "operation": "profile_validate",
  "workspace": "tapstate",
  "issues": 0,
  "next_action": "continue"
}
```

Run: `go test ./packages/agentic-cli/internal/cli`

Expected: FAIL because the CLI route does not exist.

- [x] **Step 6: Implement CLI route**

`profile validate` must load `profiles/<workspace>.yaml`, run `profile.Validate`, and return stable JSON. Validation failures must return `profile_validation_failed` with `required_human_action` in Chinese.

- [x] **Step 7: Verification**

Run:

```sh
go test ./...
bash tests/e2e/local-fake-flow.sh
```

Expected: all commands exit 0.

### Task 7: Profile Update / Rollback Baseline

Scope:

- Update: `packages/agentic-cli/internal/profile/updater.go`
- Update: `packages/agentic-cli/internal/profile/updater_test.go`
- Update: `packages/agentic-cli/internal/cli/app.go`
- Update: `packages/agentic-cli/internal/cli/app_test.go`
- Create: `contracts/operations/profile-update.yaml`
- Create: `contracts/operations/profile-rollback.yaml`
- Update: `tests/e2e/local-fake-flow.sh`

Definition:

- `profile update --workspace <name> --source <path>` loads and validates the source profile before replacing `profiles/<workspace>.yaml`.
- The source profile workspace must match the requested workspace.
- The previous profile must be saved as `profiles/<workspace>.yaml.bak`.
- `profile rollback --workspace <name>` restores that backup after validating it.
- Both commands return stable JSON and human-readable Chinese failure actions.

- [x] **Step 1: Write failing profile updater tests**

Add tests for `profile.UpdateFile` and `profile.RollbackFile` covering:

- source validation and installation.
- backup creation.
- workspace mismatch rejection.
- rollback restoration.

Expected: FAIL because updater functions do not exist.

- [x] **Step 2: Write failing CLI route test**

Add a CLI test that creates a temporary repo root, runs:

```sh
agentic-cli profile update --workspace tapstate --source <tmp-profile>
agentic-cli profile rollback --workspace tapstate
```

Expected: FAIL because the CLI routes do not exist.

- [x] **Step 3: Implement profile updater**

`profile.UpdateFile` must validate source before writing and must leave current profile unchanged on workspace mismatch.

- [x] **Step 4: Implement CLI routes**

Add `profile_update` and `profile_rollback` to `agent init` capabilities.

- [x] **Step 5: Add operation contracts and e2e coverage**

`contract validate` must include `profile_update` and `profile_rollback`. Local fake flow must exercise update, validate and rollback.

- [x] **Step 6: Verification**

Run:

```sh
go test ./...
bash tests/e2e/local-fake-flow.sh
```

Expected: all commands exit 0.

## 5. 阶段 3: Jira Adapter and Ownership Gate

### Task 8: Fake Jira Takeover Gate Baseline

Scope:

- Update: `packages/agentic-cli/internal/jira/model.go`
- Update: `packages/agentic-cli/internal/jira/fake.go`
- Create: `packages/agentic-cli/internal/jira/gate.go`
- Create: `packages/agentic-cli/internal/jira/gate_test.go`
- Update: `packages/agentic-cli/internal/cli/app.go`
- Update: `packages/agentic-cli/internal/cli/app_test.go`
- Update: `contracts/operations/takeover-task.yaml`
- Update: `tests/e2e/local-fake-flow.sh`

Definition:

- Fake Jira issues expose owner, assignee, issue type, status, risk level and `current_agent_id`.
- `takeover-task` validates owner, assignee, agent binding, task class mapping, process mapping, status mapping and required Jira fields before creating a run.
- Gate failures return stable JSON with `current_stage: takeover_gate`, `next_action: ask_owner` and Chinese `required_human_action`.
- Gate failures write blocked feedback events.

- [x] **Step 1: Write failing Jira gate tests**

Cover accepted issue, missing target repo, owner mismatch, agent conflict and unknown Jira status.

Expected: FAIL because the gate model and fields do not exist.

- [x] **Step 2: Write failing CLI blocked takeover test**

Run `takeover-task TAP-MISSING-REPO --workspace tapstate` and assert:

- `code: missing_target_repo`
- `current_stage: takeover_gate`
- `next_action: ask_owner`
- blocked feedback event exists.

Expected: FAIL because fake Jira does not return this issue and takeover does not run the gate.

- [x] **Step 3: Implement fake Jira issue fields and gate**

Add `jira.ValidateTakeover` and fake issues for blocked scenarios.

- [x] **Step 4: Wire takeover gate into CLI**

Successful takeover returns `task_class` and `process_id`; blocked takeover writes a feedback event and does not create a run.

- [x] **Step 5: Update contract and e2e coverage**

Add newly exercised failure codes to `takeover-task.yaml`. Local fake flow must include a blocked takeover and count it in feedback report.

- [x] **Step 6: Verification**

Run:

```sh
go test ./...
bash tests/e2e/local-fake-flow.sh
```

Expected: all commands exit 0.

### Task 8.5: Real Jira REST Client Contract Baseline

Scope:

- Create: `packages/agentic-cli/internal/jira/adapter.go`
- Create: `packages/agentic-cli/internal/jira/real.go`
- Create: `packages/agentic-cli/internal/jira/real_test.go`
- Update: `packages/agentic-cli/internal/jira/fake.go`
- Update: `packages/agentic-cli/internal/cli/app.go`

Definition:

- Jira package defines an adapter interface for current user, JQL search, issue get, comment write and controlled field update.
- Fake client implements the same adapter boundary and remains the default CLI adapter.
- Real client builds Jira Cloud REST API requests for `/rest/api/3/myself`, `/rest/api/3/search/jql`, `/rest/api/3/issue/{issueIdOrKey}`, `/rest/api/3/issue/{issueIdOrKey}/comment` and issue field updates.
- Real client tests use an in-process `RoundTripper`, not a real Jira server.
- CLI `list-tasks` and `takeover-task` use the adapter interface instead of directly coupling to `FakeClient`.
- This baseline does not enable real Jira writes in CLI runtime; that still requires policy / gate / confirmation wiring.

- [x] **Step 1: Write adapter and real client tests**

Covered current user lookup, JQL search field mapping, issue field update and ADF comment payloads.

- [x] **Step 2: Implement adapter interface and fake compatibility**

Added `jira.Client` and made fake client implement the adapter boundary while preserving existing fake helper methods.

- [x] **Step 3: Implement real Jira REST client**

Added Basic Auth request construction, profile-based field selection, Jira issue field mapping, ADF comment body generation and controlled `fields` update payloads.

- [x] **Step 4: Wire CLI to adapter boundary**

Updated `list-tasks` and `takeover-task` to use `jira.Client`; default factory still returns fake adapter.

- [x] **Step 5: Verification**

Run:

```sh
go test ./packages/agentic-cli/internal/jira ./packages/agentic-cli/internal/cli
```

Expected: all commands exit 0.

### Task 8.6: Guarded Real Jira Adapter Activation

Scope:

- Update: `packages/agentic-cli/internal/cli/app.go`
- Update: `packages/agentic-cli/internal/cli/app_test.go`
- Update: `contracts/operations/takeover-task.yaml`
- Update: `contracts/operations/release-agent.yaml`

Definition:

- Default runtime still uses fake Jira adapter.
- `AGENTIC_OPS_JIRA_ADAPTER=real` selects the real Jira client only when `AGENTIC_OPS_JIRA_BASE_URL`, `AGENTIC_OPS_JIRA_EMAIL` and `AGENTIC_OPS_JIRA_API_TOKEN` are all present.
- Real Jira field writes in `takeover-task` and `release-agent` require `--confirm-real-jira-write`.
- `takeover-task` writes profile-mapped `current_agent_id` and `takeover_at` fields only after ownership gate and explicit confirmation.
- `release-agent` checks real Jira assignee and `current_agent_id` before clearing the profile-mapped `current_agent_id` field after explicit confirmation.
- Missing real Jira confirmation returns `real_jira_confirmation_required` with `next_action: ask_owner`.

- [x] **Step 1: Write CLI confirmation gate tests**

Covered unconfirmed real-mode `takeover-task` and `release-agent` failures plus profile field mapping helpers.

- [x] **Step 2: Implement real adapter selection**

Added environment-based adapter selection with explicit required Jira connection configuration.

- [x] **Step 3: Guard real Jira writes**

Added `--confirm-real-jira-write` checks before real takeover binding and release cleanup field updates.

- [x] **Step 4: Update operation contracts**

Added adapter config, issue read, current user, confirmation and write mapping failure codes.

- [x] **Step 5: Verification**

Run:

```sh
go test ./packages/agentic-cli/internal/cli
```

Expected: all commands exit 0.

### Task 8.7: Real Jira Write Gate Audit Events

Scope:

- Update: `packages/agentic-cli/internal/cli/app.go`
- Update: `packages/agentic-cli/internal/cli/app_test.go`
- Update: `docs/runtime/problem-resolution-and-update.md`

Definition:

- Real Jira write confirmation failures record a local feedback event with `gate: real_jira_write`, `gate_status: blocked` and `code: real_jira_confirmation_required`.
- Confirmed successful real Jira field writes record `gate_status: passed`.
- Real Jira field write failures record `gate_status: failed` with the operation failure code.
- Audit events apply to takeover binding and release cleanup field writes.

- [x] **Step 1: Write failing gate audit tests**

Covered blocked release confirmation, passed takeover field write and failed release cleanup write.

- [x] **Step 2: Implement gate audit event helper**

Added a dedicated `appendRealJiraWriteGateEvent` path that records `real_jira_write` gate status separately from the normal operation completion event.

- [x] **Step 3: Wire takeover and release write paths**

Recorded blocked, passed and failed gate events around real Jira field writes.

- [x] **Step 4: Verification**

Run:

```sh
go test ./packages/agentic-cli/internal/cli
```

Expected: all commands exit 0.

### Task 8.8: Real Jira Comment Gate for Evidence

Scope:

- Update: `packages/agentic-cli/internal/cli/app.go`
- Update: `packages/agentic-cli/internal/cli/app_test.go`
- Update: `contracts/operations/write-evidence.yaml`
- Update: `docs/runtime/problem-resolution-and-update.md`

Definition:

- In real Jira mode, `write-evidence` resolves the Jira issue key from `--issue-key` or the existing run event.
- Real Jira comment writes require `--confirm-real-jira-write`.
- Missing confirmation returns `real_jira_confirmation_required` and records a blocked `real_jira_write` gate event.
- Confirmed comment writes call the Jira adapter `AddComment` path and record a passed `real_jira_write` gate event.
- Jira comment write failures return `jira_comment_write_failed` and record a failed gate event.
- Fake mode remains local-only and keeps existing e2e behavior.

- [x] **Step 1: Write failing comment gate tests**

Covered missing confirmation and confirmed real Jira comment write for `write-evidence`.

- [x] **Step 2: Implement issue lookup and comment gate**

Added run event lookup for issue key, confirmation guard, Jira comment write and `real_jira_write` gate events.

- [x] **Step 3: Update operation contract**

Added real Jira adapter config, confirmation and comment write failure codes to `write-evidence.yaml`.

- [x] **Step 4: Verification**

Run:

```sh
go test ./packages/agentic-cli/internal/cli
```

Expected: all commands exit 0.

### Task 9: Takeover Form Event Baseline

Scope:

- Update: `packages/agentic-cli/internal/feedback/event.go`
- Update: `packages/agentic-cli/internal/cli/app.go`
- Update: `packages/agentic-cli/internal/cli/app_test.go`
- Update: `contracts/operations/takeover-task.yaml`
- Update: `tests/e2e/local-fake-flow.sh`

Definition:

- Successful fake takeover returns `agent_id`, `current_agent_id`, `takeover_at`, `task_class` and `process_id`.
- Successful fake takeover writes the same fields into the local feedback event.
- This is the local event/form baseline for later real Jira adapter writeback; it does not claim real Jira mutation is implemented.

- [x] **Step 1: Write failing CLI test for takeover form fields**

Assert successful `takeover-task TAP-123 --workspace tapstate` output and event contain:

- `agent_id`
- `current_agent_id`
- `takeover_at`
- `task_class`
- `process_id`

Expected: FAIL because takeover success does not expose these fields.

- [x] **Step 2: Extend event model and takeover success output**

Add optional fields to feedback events and populate them in successful takeover.

- [x] **Step 3: Update contract and e2e coverage**

`takeover-task.yaml` must declare the new output fields. Local fake flow must assert the current agent binding appears in takeover output.

- [x] **Step 4: Verification**

Run:

```sh
go test ./...
bash tests/e2e/local-fake-flow.sh
```

Expected: all commands exit 0.

### Task 10: Local Agent Release Event Baseline

Scope:

- Update: `packages/agentic-cli/internal/feedback/event.go`
- Update: `packages/agentic-cli/internal/cli/app.go`
- Update: `packages/agentic-cli/internal/cli/app_test.go`
- Create: `contracts/operations/release-agent.yaml`
- Update: `docs/contracts/operation-contract.md`
- Update: `tests/e2e/local-fake-flow.sh`

Definition:

- Add `release-agent --workspace <name> --run-id <run_id> --issue-key <key> --completion-evidence <ref>`.
- The command records a local completion cleanup event with `current_agent_id_cleared=true`, `completed_at` and `completion_evidence`.
- The command is the local event/form baseline for later real Jira `current_agent_id` clearing; it does not claim real Jira mutation is implemented.

- [x] **Step 1: Write failing CLI test for cleanup event**

Assert `release-agent` returns `current_agent_id_cleared=true` and writes a `release_agent` event containing:

- `current_agent_id`
- `current_agent_id_cleared`
- `completed_at`
- `completion_evidence`

Expected: FAIL because the command does not exist.

- [x] **Step 2: Implement command and event fields**

Add optional `completed_at` and `completion_evidence` to feedback events. Add the `release-agent` CLI route and `release_agent` capability.

- [x] **Step 3: Add operation contract and e2e coverage**

`contract validate` must include `release_agent`. Local fake flow must exercise the cleanup event and count it in feedback report.

- [x] **Step 4: Verification**

Run:

```sh
go test ./...
bash tests/e2e/local-fake-flow.sh
```

Expected: all commands exit 0.

## 6. 阶段 4: Problem Resolution Commands

### Task 11: Doctor Local Diagnostic Baseline

Scope:

- Update: `packages/agentic-cli/internal/cli/app.go`
- Update: `packages/agentic-cli/internal/cli/app_test.go`
- Create: `contracts/operations/doctor.yaml`
- Update: `docs/contracts/operation-contract.md`
- Update: `docs/runtime/problem-resolution-and-update.md`
- Update: `tests/e2e/local-fake-flow.sh`

Definition:

- Add `doctor --workspace <name>`.
- The command outputs structured JSON containing local checks for install dir, version, workspace root, profile, policy, operation contracts, fake Jira adapter and GitHub baseline status.
- GitHub is `skipped` in the local baseline because real external checks are not implemented yet.
- This is the local diagnostic baseline for later real Jira / GitHub / update checks.

- [x] **Step 1: Write failing CLI test**

Assert `doctor --workspace tapstate` returns:

- `operation: doctor`
- `workspace`
- `version`
- `status`
- checks for `profile`, `policy`, `jira_adapter`, `github`, `workspace` and `contracts`.

Expected: FAIL because the command does not exist.

- [x] **Step 2: Implement local checks**

Use current repo and workspace state to check profile validation, policy file presence and operation contract validation. Do not call network services.

- [x] **Step 3: Add operation contract and e2e coverage**

`contract validate` must include `doctor`. Local fake flow must run doctor.

- [x] **Step 4: Verification**

Run:

```sh
go test ./...
bash tests/e2e/local-fake-flow.sh
```

Expected: all commands exit 0.

### Task 12: Feedback Bundle Redaction Baseline

Scope:

- Update: `packages/agentic-cli/internal/cli/app.go`
- Update: `packages/agentic-cli/internal/cli/app_test.go`
- Create: `contracts/operations/feedback-bundle.yaml`
- Update: `docs/contracts/operation-contract.md`
- Update: `docs/runtime/problem-resolution-and-update.md`
- Update: `tests/e2e/local-fake-flow.sh`

Definition:

- Add `feedback bundle --workspace <name> --run-id <run_id> --redact`.
- The command reads local feedback events and writes `.agentic-ops/feedback/bundles/<run_id>.md`.
- When `--redact` is present, obvious `token=...`, `password=...`, `secret=...` and `authorization=...` values must be replaced with `[REDACTED]`.
- This is the local diagnostic bundle baseline; it does not call external services or include raw Jira descriptions.

- [x] **Step 1: Write failing CLI test**

Create an event log containing sensitive-looking text, run `feedback bundle --redact`, and assert the generated bundle exists and excludes the original sensitive values.

Expected: FAIL because the command does not exist.

- [x] **Step 2: Implement bundle generation and redaction**

Generate a Markdown bundle from local feedback events. Keep redaction deliberately narrow and auditable.

- [x] **Step 3: Add operation contract and e2e coverage**

`contract validate` must include `feedback_bundle`. Local fake flow must generate a bundle and verify the file exists.

- [x] **Step 4: Verification**

Run:

```sh
go test ./...
bash tests/e2e/local-fake-flow.sh
```

Expected: all commands exit 0.

### Task 13: Update Check / Apply Local Manifest Baseline

Scope:

- Create: `packages/agentic-cli/internal/update/manifest.go`
- Create: `packages/agentic-cli/internal/update/manifest_test.go`
- Update: `packages/agentic-cli/internal/cli/app.go`
- Update: `packages/agentic-cli/internal/cli/app_test.go`
- Create: `contracts/operations/update-check.yaml`
- Create: `contracts/operations/update-apply.yaml`
- Update: `docs/contracts/operation-contract.md`
- Update: `docs/runtime/problem-resolution-and-update.md`
- Update: `tests/e2e/local-fake-flow.sh`

Definition:

- Add `update check --manifest <path>`.
- Add `update apply --manifest <path> --install-dir <dir>`.
- Local manifest mode reads a local release manifest and does not download remote artifacts.
- `check` returns `update_available`, `severity`, `reason`, `blocked_operations` and `next_action`.
- `apply` writes `current.json` in the install dir and preserves previous `agentic_cli_version` / `asset_version`.
- This is the local manifest baseline; it does not claim remote self-update, checksum validation, artifact download or real binary replacement.

- [x] **Step 1: Write failing update package tests**

Cover:

- required update reports `blocked_operations`.
- same version reports no update.
- apply writes `current.json` and preserves previous versions.

Expected: FAIL because the update package does not exist.

- [x] **Step 2: Implement update manifest check/apply**

Add manifest loading, version comparison by exact string, severity defaulting and current.json writing.

- [x] **Step 3: Write failing CLI route test**

Assert `update check` and `update apply` use a local manifest and return structured JSON.

Expected: FAIL because the CLI routes do not exist.

- [x] **Step 4: Implement CLI routes and capabilities**

Add `update_check` and `update_apply` to `agent init` capabilities.

- [x] **Step 5: Add operation contracts and e2e coverage**

`contract validate` must include update contracts. Local fake flow must run check/apply against a temporary manifest.

- [x] **Step 6: Verification**

Run:

```sh
go test ./...
bash tests/e2e/local-fake-flow.sh
```

Expected: all commands exit 0.

## 6.5 Policy Validate / Update / Rollback Local Baseline

Goal: implement local policy validation and hot update rollback support for critical gate settings.

- [x] **Step 1: Write failing policy package tests**

Covered default policy validation, missing required fields/gates, update backup, rejected name mismatch and rollback restore.

- [x] **Step 2: Implement policy model, loader, validator and updater**

Added YAML-backed policy loading, required gate validation and local `.bak` update/rollback behavior.

- [x] **Step 3: Write failing CLI route test**

Covered `policy validate --workspace`, `policy update --workspace --source` and `policy rollback --workspace`.

- [x] **Step 4: Implement CLI routes and capabilities**

Added `policy_validate`, `policy_update` and `policy_rollback` to AgenticCLI capabilities and structured JSON outputs.

- [x] **Step 5: Add operation contracts and e2e coverage**

Added policy operation contracts and local fake flow coverage with temporary source policy and cleanup of generated backup.

- [x] **Step 6: Verification**

Run:

```sh
go test ./...
bash tests/e2e/local-fake-flow.sh
```

Expected: all commands exit 0.

## 7. Real Jira Transition Gate Baseline

- [x] **Step 1: Add transition REST client contract**

Added `TransitionIssue` to the Jira client interface and implemented the Jira Cloud v3 transition endpoint for the real adapter.

- [x] **Step 2: Guard release-agent transition writes**

`release-agent` now supports an explicit `--jira-transition-id` in real Jira mode. The transition runs only after `--confirm-real-jira-write`, ownership checks, and current_agent_id cleanup succeed.

- [x] **Step 3: Record transition gate events**

Successful and failed transition writes append `real_jira_write` gate events. Failed transitions return `jira_transition_failed` and require owner review.

- [ ] **Step 4: Decide profile-driven transition mapping**

Current `transition_mapping` maps standard workflow actions to standard stages. It does not yet identify concrete Jira transition ids or names, so automatic profile-driven transition selection remains a design decision.

## 8. Remote Update Manifest And Artifact Baseline

- [x] **Step 1: Support remote manifest checks**

`update check` now accepts `--manifest-url` and downloads the remote release manifest before comparing it with the current AgenticCLI version.

- [x] **Step 2: Download release artifacts**

`update apply` now accepts `--manifest-url`, selects the binary artifact for the current or explicit `--target`, downloads the matching assets artifact, and stores both under the install directory.

- [x] **Step 3: Verify checksums before switching current metadata**

Remote apply downloads `checksums.txt`, verifies each selected artifact with SHA-256, rejects checksum mismatches, and only then updates `current.json`.

- [ ] **Step 4: Switch the installed binary**

The current baseline verifies and stores remote artifacts but does not yet unpack and replace the running `agentic-cli` binary.

## 9. Later Phases

- Later Phase 3: profile-driven Jira transition id/name mapping.
- Later Phase 4: real binary switching and real external doctor checks.
- Phase 5: completion cleanup and problem-resolution e2e.

Do not start later phases until Phase 1 contract/schema baseline is passing and committed.
