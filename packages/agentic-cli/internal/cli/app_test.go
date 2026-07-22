package cli

import (
	"bytes"
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestVersionOutputsJSON(t *testing.T) {
	var stdout bytes.Buffer
	var stderr bytes.Buffer
	code := Run([]string{"--version"}, &stdout, &stderr)
	if code != 0 {
		t.Fatalf("code = %d, want 0", code)
	}
	assertJSONField(t, stdout.String(), "operation", "version")
	assertJSONField(t, stdout.String(), "version", "SRC-source")
	assertJSONField(t, stdout.String(), "version_state", "SRC")
	assertJSONField(t, stdout.String(), "iteration_version", "source")
	assertJSONNumber(t, stdout.String(), "commit_index", 0)
	assertJSONField(t, stdout.String(), "commit", "unknown")
	if stderr.String() != "" {
		t.Fatalf("stderr = %s", stderr.String())
	}
}

func TestUnknownCommandFailsWithStableCode(t *testing.T) {
	var stdout bytes.Buffer
	var stderr bytes.Buffer
	code := Run([]string{"missing"}, &stdout, &stderr)
	if code != 1 {
		t.Fatalf("code = %d, want 1", code)
	}
	assertJSONField(t, stdout.String(), "code", "unknown_command")
	if !strings.Contains(stderr.String(), "unknown command: missing") {
		t.Fatalf("stderr = %s", stderr.String())
	}
}

func TestPreflightOutputsInstallDirAndNextAction(t *testing.T) {
	installDir := t.TempDir()
	t.Setenv("AGENTIC_OPS_HOME", installDir)
	var stdout bytes.Buffer
	var stderr bytes.Buffer
	code := Run([]string{"preflight", "--workspace", "tapstate"}, &stdout, &stderr)
	if code != 0 {
		t.Fatalf("code = %d", code)
	}
	assertJSONField(t, stdout.String(), "operation", "preflight")
	assertJSONField(t, stdout.String(), "workspace", "tapstate")
	assertJSONField(t, stdout.String(), "install_dir", installDir)
	assertJSONField(t, stdout.String(), "go_runtime", "not_required_for_installed_cli")
	assertJSONField(t, stdout.String(), "next_action", "workspace_init")
}

func TestDoctorOutputsLocalDiagnosticChecks(t *testing.T) {
	var stdout bytes.Buffer
	var stderr bytes.Buffer
	code := Run([]string{"doctor", "--workspace", "tapstate"}, &stdout, &stderr)
	if code != 0 {
		t.Fatalf("code = %d stdout = %s stderr = %s", code, stdout.String(), stderr.String())
	}
	assertJSONField(t, stdout.String(), "operation", "doctor")
	assertJSONField(t, stdout.String(), "workspace", "tapstate")
	assertJSONField(t, stdout.String(), "version", "SRC-source")
	assertJSONField(t, stdout.String(), "next_action", "continue")
	assertJSONField(t, stdout.String(), "status", "ok")
	assertNestedJSONField(t, stdout.String(), []string{"checks", "profile", "status"}, "ok")
	assertNestedJSONField(t, stdout.String(), []string{"checks", "policy", "status"}, "ok")
	assertNestedJSONField(t, stdout.String(), []string{"checks", "jira_adapter", "status"}, "ok")
	assertNestedJSONField(t, stdout.String(), []string{"checks", "github", "status"}, "skipped")
	assertNestedJSONField(t, stdout.String(), []string{"checks", "workspace", "status"}, "ok")
	assertNestedJSONField(t, stdout.String(), []string{"checks", "contracts", "status"}, "ok")
}

func TestWorkspaceInitOutputsNextAction(t *testing.T) {
	root := t.TempDir()
	t.Setenv("AGENTIC_OPS_WORKSPACE_ROOT", root)
	var stdout bytes.Buffer
	var stderr bytes.Buffer
	code := Run([]string{"workspace", "init", "--workspace", "tapstate"}, &stdout, &stderr)
	if code != 0 {
		t.Fatalf("code = %d", code)
	}
	assertJSONField(t, stdout.String(), "operation", "workspace_init")
	assertJSONField(t, stdout.String(), "next_action", "init_agent_capability")
	if _, err := os.Stat(filepath.Join(root, ".agentic-ops", "runs")); err != nil {
		t.Fatalf("workspace root was not used: %v", err)
	}
}

func TestAgentInitOutputsTaskModel(t *testing.T) {
	var stdout bytes.Buffer
	var stderr bytes.Buffer
	code := Run([]string{"agent", "init", "--workspace", "tapstate"}, &stdout, &stderr)
	if code != 0 {
		t.Fatalf("code = %d", code)
	}
	assertJSONField(t, stdout.String(), "operation", "agent_init")
	assertJSONField(t, stdout.String(), "task_type", "capability_initialization")
	assertJSONField(t, stdout.String(), "current_stage", "agent_capability_initialized")
	assertJSONField(t, stdout.String(), "next_action", "list_tasks")
	if !strings.Contains(stdout.String(), `"contract_validate"`) {
		t.Fatalf("stdout missing contract_validate capability: %s", stdout.String())
	}
}

func TestAssetsInstallCopiesAssetsToInstallDir(t *testing.T) {
	source := t.TempDir()
	writeCLITestFile(t, filepath.Join(source, "handbooks", "ai-employee-handbook.md"), "# handbook\n")
	installDir := t.TempDir()

	var stdout bytes.Buffer
	var stderr bytes.Buffer
	code := Run([]string{"assets", "install", "--source", source, "--install-dir", installDir, "--version", "RES-v0.1.1-a68372d"}, &stdout, &stderr)
	if code != 0 {
		t.Fatalf("code = %d stdout = %s stderr = %s", code, stdout.String(), stderr.String())
	}

	assertJSONField(t, stdout.String(), "operation", "assets_install")
	assertJSONField(t, stdout.String(), "asset_version", "RES-v0.1.1-a68372d")
	if _, err := os.Stat(filepath.Join(installDir, "assets", "RES-v0.1.1-a68372d", "handbooks", "ai-employee-handbook.md")); err != nil {
		t.Fatalf("installed asset missing: %v", err)
	}
}

func TestListTasksUsesFakeJira(t *testing.T) {
	var stdout bytes.Buffer
	var stderr bytes.Buffer
	code := Run([]string{"list-tasks", "--workspace", "tapstate"}, &stdout, &stderr)
	if code != 0 {
		t.Fatalf("code = %d", code)
	}
	for _, want := range []string{`"operation":"list_tasks"`, `"workspace":"tapstate"`, `"key":"TAP-123"`} {
		if !strings.Contains(stdout.String(), want) {
			t.Fatalf("stdout missing %s: %s", want, stdout.String())
		}
	}
}

func TestTakeoverTaskReturnsRunIDAndStage(t *testing.T) {
	root := t.TempDir()
	t.Chdir(root)
	var stdout bytes.Buffer
	var stderr bytes.Buffer
	code := Run([]string{"takeover-task", "TAP-123", "--workspace", "tapstate"}, &stdout, &stderr)
	if code != 0 {
		t.Fatalf("code = %d", code)
	}
	for _, want := range []string{`"operation":"takeover_task"`, `"task_type":"task_takeover"`, `"current_stage":"takeover_started"`, `"next_action":"proceed"`} {
		if !strings.Contains(stdout.String(), want) {
			t.Fatalf("stdout missing %s: %s", want, stdout.String())
		}
	}
	assertJSONField(t, stdout.String(), "agent_id", "agentic-cli-local-agent")
	assertJSONField(t, stdout.String(), "current_agent_id", "agentic-cli-local-agent")
	assertJSONField(t, stdout.String(), "takeover_at", "2026-07-21T10:30:12Z")
	assertJSONField(t, stdout.String(), "task_class", "technical_task")
	assertJSONField(t, stdout.String(), "process_id", "development_change_v1")
	events, err := os.ReadFile(filepath.Join(root, ".agentic-ops", "feedback", "events.ndjson"))
	if err != nil {
		t.Fatalf("ReadFile events error = %v", err)
	}
	if !strings.Contains(string(events), `"operation":"takeover_task"`) {
		t.Fatalf("events = %s", string(events))
	}
	if !strings.Contains(string(events), `"agentic_cli_version":"SRC-source"`) {
		t.Fatalf("events = %s", string(events))
	}
	if !strings.Contains(string(events), `"version_state":"SRC"`) {
		t.Fatalf("events = %s", string(events))
	}
	if !strings.Contains(string(events), `"asset_version":"unknown"`) {
		t.Fatalf("events = %s", string(events))
	}
	if !strings.Contains(string(events), `"gate_status":"passed"`) {
		t.Fatalf("events = %s", string(events))
	}
	for _, want := range []string{
		`"agent_id":"agentic-cli-local-agent"`,
		`"current_agent_id":"agentic-cli-local-agent"`,
		`"takeover_at":"2026-07-21T10:30:12Z"`,
		`"task_class":"technical_task"`,
		`"process_id":"development_change_v1"`,
	} {
		if !strings.Contains(string(events), want) {
			t.Fatalf("events missing %s: %s", want, string(events))
		}
	}
}

func TestTakeoverTaskBlocksMissingTargetRepo(t *testing.T) {
	root := t.TempDir()
	t.Setenv("AGENTIC_OPS_WORKSPACE_ROOT", root)
	var stdout bytes.Buffer
	var stderr bytes.Buffer
	code := Run([]string{"takeover-task", "TAP-MISSING-REPO", "--workspace", "tapstate"}, &stdout, &stderr)
	if code != 1 {
		t.Fatalf("code = %d stdout = %s", code, stdout.String())
	}
	assertJSONField(t, stdout.String(), "operation", "takeover_task")
	assertJSONField(t, stdout.String(), "code", "missing_target_repo")
	assertJSONField(t, stdout.String(), "current_stage", "takeover_gate")
	assertJSONField(t, stdout.String(), "next_action", "ask_owner")
	assertJSONField(t, stdout.String(), "required_human_action", "请在 Jira 卡片补充目标仓库，或维护 workspace repo 映射")
	events, err := os.ReadFile(filepath.Join(root, ".agentic-ops", "feedback", "events.ndjson"))
	if err != nil {
		t.Fatalf("ReadFile events error = %v", err)
	}
	if !strings.Contains(string(events), `"code":"missing_target_repo"`) {
		t.Fatalf("events = %s", string(events))
	}
	if !strings.Contains(string(events), `"gate_status":"blocked"`) {
		t.Fatalf("events = %s", string(events))
	}
}

func TestResumeTakeoverReturnsRunIDAndNextAction(t *testing.T) {
	t.Chdir(t.TempDir())
	var stdout bytes.Buffer
	var stderr bytes.Buffer
	code := Run([]string{"resume-takeover", "--workspace", "tapstate", "--run-id", "run-1"}, &stdout, &stderr)
	if code != 0 {
		t.Fatalf("code = %d", code)
	}
	assertJSONField(t, stdout.String(), "operation", "resume_takeover")
	assertJSONField(t, stdout.String(), "run_id", "run-1")
	assertJSONField(t, stdout.String(), "next_action", "continue_development")
}

func TestWriteEvidenceRequiresRunID(t *testing.T) {
	root := t.TempDir()
	t.Chdir(root)
	var stdout bytes.Buffer
	var stderr bytes.Buffer
	code := Run([]string{"write-evidence", "--workspace", "tapstate"}, &stdout, &stderr)
	if code != 1 {
		t.Fatalf("code = %d", code)
	}
	assertJSONField(t, stdout.String(), "code", "missing_run_id")
	assertJSONField(t, stdout.String(), "task_type", "evidence_write")
	assertJSONField(t, stdout.String(), "current_stage", "input_validation")
	assertJSONField(t, stdout.String(), "next_action", "ask_owner")
	assertJSONField(t, stdout.String(), "required_human_action", "请提供 --run-id")
	events, err := os.ReadFile(filepath.Join(root, ".agentic-ops", "feedback", "events.ndjson"))
	if err != nil {
		t.Fatalf("ReadFile events error = %v", err)
	}
	if !strings.Contains(string(events), `"code":"missing_run_id"`) {
		t.Fatalf("events = %s", string(events))
	}
	if !strings.Contains(string(events), `"gate_status":"blocked"`) {
		t.Fatalf("events = %s", string(events))
	}
}

func TestWriteEvidenceOutputsNextAction(t *testing.T) {
	root := t.TempDir()
	t.Chdir(root)
	var stdout bytes.Buffer
	var stderr bytes.Buffer
	code := Run([]string{"write-evidence", "--workspace", "tapstate", "--run-id", "run-1"}, &stdout, &stderr)
	if code != 0 {
		t.Fatalf("code = %d", code)
	}
	assertJSONField(t, stdout.String(), "operation", "write_evidence")
	assertJSONField(t, stdout.String(), "next_action", "request_owner_confirmation")
	events, err := os.ReadFile(filepath.Join(root, ".agentic-ops", "feedback", "events.ndjson"))
	if err != nil {
		t.Fatalf("ReadFile events error = %v", err)
	}
	if !strings.Contains(string(events), `"operation":"write_evidence"`) {
		t.Fatalf("events = %s", string(events))
	}
}

func TestContractValidateOutputsIssueCount(t *testing.T) {
	var stdout bytes.Buffer
	var stderr bytes.Buffer
	code := Run([]string{"contract", "validate"}, &stdout, &stderr)
	if code != 0 {
		t.Fatalf("code = %d stdout = %s stderr = %s", code, stdout.String(), stderr.String())
	}
	assertJSONField(t, stdout.String(), "operation", "contract_validate")
	assertJSONNumber(t, stdout.String(), "issues", 0)
	assertJSONField(t, stdout.String(), "next_action", "continue")
}

func TestProfileValidateOutputsIssueCount(t *testing.T) {
	var stdout bytes.Buffer
	var stderr bytes.Buffer
	code := Run([]string{"profile", "validate", "--workspace", "tapstate"}, &stdout, &stderr)
	if code != 0 {
		t.Fatalf("code = %d stdout = %s stderr = %s", code, stdout.String(), stderr.String())
	}
	assertJSONField(t, stdout.String(), "operation", "profile_validate")
	assertJSONField(t, stdout.String(), "workspace", "tapstate")
	assertJSONNumber(t, stdout.String(), "issues", 0)
	assertJSONField(t, stdout.String(), "next_action", "continue")
}

func TestProfileUpdateAndRollbackUseLocalProfileBackup(t *testing.T) {
	repo := t.TempDir()
	t.Chdir(repo)
	writeCLITestFile(t, filepath.Join(repo, "go.mod"), "module example.local/test\n")
	writeCLITestFile(t, filepath.Join(repo, "contracts", "operations", ".keep"), "")
	target := filepath.Join(repo, "profiles", "tapstate.yaml")
	source := filepath.Join(repo, "incoming", "tapstate.yaml")
	writeCLITestFile(t, target, validCLIProfileYAML("tapstate", "TAP"))
	writeCLITestFile(t, source, validCLIProfileYAML("tapstate", "OPS"))

	var updateStdout bytes.Buffer
	var updateStderr bytes.Buffer
	updateCode := Run([]string{"profile", "update", "--workspace", "tapstate", "--source", source}, &updateStdout, &updateStderr)
	if updateCode != 0 {
		t.Fatalf("updateCode = %d stdout = %s stderr = %s", updateCode, updateStdout.String(), updateStderr.String())
	}
	assertJSONField(t, updateStdout.String(), "operation", "profile_update")
	assertJSONField(t, updateStdout.String(), "workspace", "tapstate")
	assertJSONField(t, updateStdout.String(), "next_action", "profile_validate")
	updated, err := os.ReadFile(target)
	if err != nil {
		t.Fatalf("ReadFile updated error = %v", err)
	}
	if !strings.Contains(string(updated), "project: OPS") {
		t.Fatalf("updated profile = %s", string(updated))
	}

	var rollbackStdout bytes.Buffer
	var rollbackStderr bytes.Buffer
	rollbackCode := Run([]string{"profile", "rollback", "--workspace", "tapstate"}, &rollbackStdout, &rollbackStderr)
	if rollbackCode != 0 {
		t.Fatalf("rollbackCode = %d stdout = %s stderr = %s", rollbackCode, rollbackStdout.String(), rollbackStderr.String())
	}
	assertJSONField(t, rollbackStdout.String(), "operation", "profile_rollback")
	assertJSONField(t, rollbackStdout.String(), "workspace", "tapstate")
	assertJSONField(t, rollbackStdout.String(), "next_action", "profile_validate")
	restored, err := os.ReadFile(target)
	if err != nil {
		t.Fatalf("ReadFile restored error = %v", err)
	}
	if !strings.Contains(string(restored), "project: TAP") {
		t.Fatalf("restored profile = %s", string(restored))
	}
}

func TestReleaseAgentRecordsCurrentAgentCleanup(t *testing.T) {
	root := t.TempDir()
	t.Setenv("AGENTIC_OPS_WORKSPACE_ROOT", root)
	runID := "TAP-123-takeover-20260721103012-a8f3"
	Run([]string{"takeover-task", "TAP-123", "--workspace", "tapstate"}, &bytes.Buffer{}, &bytes.Buffer{})

	var stdout bytes.Buffer
	var stderr bytes.Buffer
	code := Run([]string{"release-agent", "--workspace", "tapstate", "--run-id", runID, "--issue-key", "TAP-123", "--completion-evidence", "evidence.md"}, &stdout, &stderr)
	if code != 0 {
		t.Fatalf("code = %d stdout = %s stderr = %s", code, stdout.String(), stderr.String())
	}
	assertJSONField(t, stdout.String(), "operation", "release_agent")
	assertJSONField(t, stdout.String(), "run_id", runID)
	assertJSONField(t, stdout.String(), "current_stage", "completed")
	assertJSONField(t, stdout.String(), "current_agent_id_cleared", true)
	assertJSONField(t, stdout.String(), "next_action", "feedback_report")

	events, err := os.ReadFile(filepath.Join(root, ".agentic-ops", "feedback", "events.ndjson"))
	if err != nil {
		t.Fatalf("ReadFile events error = %v", err)
	}
	for _, want := range []string{
		`"operation":"release_agent"`,
		`"current_agent_id":"agentic-cli-local-agent"`,
		`"current_agent_id_cleared":true`,
		`"completed_at":"2026-07-21T10:30:12Z"`,
		`"completion_evidence":"evidence.md"`,
	} {
		if !strings.Contains(string(events), want) {
			t.Fatalf("events missing %s: %s", want, string(events))
		}
	}
}

func TestFeedbackReportOutputsReportPath(t *testing.T) {
	root := t.TempDir()
	t.Chdir(root)
	Run([]string{"takeover-task", "TAP-123", "--workspace", "tapstate"}, &bytes.Buffer{}, &bytes.Buffer{})
	Run([]string{"write-evidence", "--workspace", "tapstate", "--run-id", "run-1"}, &bytes.Buffer{}, &bytes.Buffer{})

	var stdout bytes.Buffer
	var stderr bytes.Buffer
	code := Run([]string{"feedback", "report", "--workspace", "tapstate", "--date", "2026-07-21"}, &stdout, &stderr)
	if code != 0 {
		t.Fatalf("code = %d", code)
	}
	assertJSONField(t, stdout.String(), "operation", "feedback_report")
	assertJSONField(t, stdout.String(), "next_action", "review_proposals")
	assertJSONNumber(t, stdout.String(), "runs", 2)
	data, err := os.ReadFile(filepath.Join(root, ".agentic-ops", "feedback", "daily", "2026-07-21.md"))
	if err != nil {
		t.Fatalf("ReadFile report error = %v", err)
	}
	if !strings.Contains(string(data), "runs: 2") {
		t.Fatalf("report = %s", string(data))
	}
}

func assertJSONField(t *testing.T, raw string, key string, want any) {
	t.Helper()
	var got map[string]any
	if err := json.Unmarshal([]byte(raw), &got); err != nil {
		t.Fatalf("invalid JSON %q: %v", raw, err)
	}
	if got[key] != want {
		t.Fatalf("%s = %v, want %v; raw = %s", key, got[key], want, raw)
	}
}

func assertJSONNumber(t *testing.T, raw string, key string, want float64) {
	t.Helper()
	var got map[string]any
	if err := json.Unmarshal([]byte(raw), &got); err != nil {
		t.Fatalf("invalid JSON %q: %v", raw, err)
	}
	if got[key] != want {
		t.Fatalf("%s = %v, want %v; raw = %s", key, got[key], want, raw)
	}
}

func assertNestedJSONField(t *testing.T, raw string, path []string, want any) {
	t.Helper()
	var got any
	if err := json.Unmarshal([]byte(raw), &got); err != nil {
		t.Fatalf("invalid JSON %q: %v", raw, err)
	}
	current := got
	for _, key := range path {
		object, ok := current.(map[string]any)
		if !ok {
			t.Fatalf("path %v reached non-object %T in raw = %s", path, current, raw)
		}
		current = object[key]
	}
	if current != want {
		t.Fatalf("%v = %v, want %v; raw = %s", path, current, want, raw)
	}
}

func writeCLITestFile(t *testing.T, path string, content string) {
	t.Helper()
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		t.Fatalf("MkdirAll error = %v", err)
	}
	if err := os.WriteFile(path, []byte(content), 0o644); err != nil {
		t.Fatalf("WriteFile error = %v", err)
	}
}

func validCLIProfileYAML(workspace string, jiraProject string) string {
	return "workspace: " + workspace + "\n" +
		"jira:\n" +
		"  project: " + jiraProject + "\n" +
		"  task_query: project = " + jiraProject + "\n" +
		"jira_form_mapping:\n" +
		"  fields:\n" +
		"    owner:\n" +
		"      source: jira_assignee\n" +
		"task_class_mapping:\n" +
		"  issue_types:\n" +
		"    Task: technical_task\n" +
		"standard_process_mapping:\n" +
		"  technical_task: development-change-v1\n" +
		"status_mapping:\n" +
		"  in_progress: In Progress\n" +
		"transition_mapping:\n" +
		"  start_progress: Start Progress\n" +
		"local:\n" +
		"  source_root: /tmp/source\n"
}
