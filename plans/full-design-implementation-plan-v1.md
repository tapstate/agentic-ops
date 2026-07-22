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

### Later Phase 2 Tasks

- `profile update --workspace <name>`
- `profile rollback --workspace <name>`
- profile hotfix e2e

## 5. Later Phases

- Phase 3: Jira adapter and ownership gate.
- Phase 4: doctor, feedback bundle, update check/apply, policy validate/update/rollback.
- Phase 5: completion cleanup and problem-resolution e2e.

Do not start later phases until Phase 1 contract/schema baseline is passing and committed.
