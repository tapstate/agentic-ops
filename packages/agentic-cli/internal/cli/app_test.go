package cli

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/tapstate/agentic-ops/packages/agentic-cli/internal/jira"
	"github.com/tapstate/agentic-ops/packages/agentic-cli/internal/profile"
	"github.com/tapstate/agentic-ops/packages/agentic-cli/internal/update"
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

func TestDoctorChecksRealJiraAdapterWhenRequested(t *testing.T) {
	withJiraClientForTest(t, jiraClientSelection{Client: &recordingJiraClient{issue: realModeIssue()}, Mode: "real"})

	var stdout bytes.Buffer
	var stderr bytes.Buffer
	code := Run([]string{"doctor", "--workspace", "tapstate", "--check-real-jira"}, &stdout, &stderr)
	if code != 0 {
		t.Fatalf("code = %d stdout = %s stderr = %s", code, stdout.String(), stderr.String())
	}
	assertJSONField(t, stdout.String(), "operation", "doctor")
	assertJSONField(t, stdout.String(), "status", "ok")
	assertNestedJSONField(t, stdout.String(), []string{"checks", "jira_adapter", "status"}, "ok")
	assertNestedJSONField(t, stdout.String(), []string{"checks", "jira_adapter", "message"}, "real adapter authenticated as current-user")
}

func TestDoctorFailsRealJiraCheckWhenAdapterIsNotReal(t *testing.T) {
	withJiraClientForTest(t, jiraClientSelection{Client: &recordingJiraClient{issue: realModeIssue()}, Mode: "fake"})

	var stdout bytes.Buffer
	var stderr bytes.Buffer
	code := Run([]string{"doctor", "--workspace", "tapstate", "--check-real-jira"}, &stdout, &stderr)
	if code != 0 {
		t.Fatalf("code = %d stdout = %s stderr = %s", code, stdout.String(), stderr.String())
	}
	assertJSONField(t, stdout.String(), "operation", "doctor")
	assertJSONField(t, stdout.String(), "status", "failed")
	assertJSONField(t, stdout.String(), "next_action", "fix_environment")
	assertNestedJSONField(t, stdout.String(), []string{"checks", "jira_adapter", "status"}, "failed")
}

func TestDoctorChecksGitHubAuthWhenRequested(t *testing.T) {
	original := runGitHubAuthStatus
	called := false
	runGitHubAuthStatus = func(ctx context.Context) error {
		called = true
		return nil
	}
	t.Cleanup(func() {
		runGitHubAuthStatus = original
	})

	var stdout bytes.Buffer
	var stderr bytes.Buffer
	code := Run([]string{"doctor", "--workspace", "tapstate", "--check-github"}, &stdout, &stderr)
	if code != 0 {
		t.Fatalf("code = %d stdout = %s stderr = %s", code, stdout.String(), stderr.String())
	}
	if !called {
		t.Fatalf("runGitHubAuthStatus was not called")
	}
	assertJSONField(t, stdout.String(), "operation", "doctor")
	assertNestedJSONField(t, stdout.String(), []string{"checks", "github", "status"}, "ok")
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

func TestUpdateCheckAndApplyUseLocalManifest(t *testing.T) {
	dir := t.TempDir()
	manifestPath := filepath.Join(dir, "manifest.json")
	installDir := filepath.Join(dir, "install")
	writeCLITestFile(t, manifestPath, `{
  "version": "RES-v0.1.20-deadbee",
  "asset_version": "RES-v0.1.20-deadbee",
  "severity": "required",
  "reason": "takeover_task may write invalid evidence",
  "blocked_operations": ["takeover_task"]
}
`)

	var checkStdout bytes.Buffer
	var checkStderr bytes.Buffer
	checkCode := Run([]string{"update", "check", "--manifest", manifestPath}, &checkStdout, &checkStderr)
	if checkCode != 0 {
		t.Fatalf("checkCode = %d stdout = %s stderr = %s", checkCode, checkStdout.String(), checkStderr.String())
	}
	assertJSONField(t, checkStdout.String(), "operation", "update_check")
	assertJSONField(t, checkStdout.String(), "update_available", true)
	assertJSONField(t, checkStdout.String(), "severity", "required")
	assertJSONField(t, checkStdout.String(), "next_action", "update_apply")
	if !strings.Contains(checkStdout.String(), `"takeover_task"`) {
		t.Fatalf("check stdout missing blocked operation: %s", checkStdout.String())
	}

	var applyStdout bytes.Buffer
	var applyStderr bytes.Buffer
	applyCode := Run([]string{"update", "apply", "--manifest", manifestPath, "--install-dir", installDir}, &applyStdout, &applyStderr)
	if applyCode != 0 {
		t.Fatalf("applyCode = %d stdout = %s stderr = %s", applyCode, applyStdout.String(), applyStderr.String())
	}
	assertJSONField(t, applyStdout.String(), "operation", "update_apply")
	assertJSONField(t, applyStdout.String(), "version", "RES-v0.1.20-deadbee")
	assertJSONField(t, applyStdout.String(), "asset_version", "RES-v0.1.20-deadbee")
	assertJSONField(t, applyStdout.String(), "next_action", "doctor")
	if _, err := os.Stat(filepath.Join(installDir, "current.json")); err != nil {
		t.Fatalf("current.json missing: %v", err)
	}
}

func TestUpdateCheckUsesRemoteManifestURL(t *testing.T) {
	restore := update.SetHTTPClientForTest(&http.Client{Transport: cliRoundTripFunc(func(r *http.Request) *http.Response {
		if r.URL.String() != "https://updates.example.test/manifest.json" {
			t.Fatalf("url = %s", r.URL.String())
		}
		return cliHTTPResponse(http.StatusOK, `{
  "version": "RES-v0.1.20-deadbee",
  "asset_version": "RES-v0.1.20-deadbee",
  "severity": "required"
}
`)
	})})
	defer restore()

	var stdout bytes.Buffer
	var stderr bytes.Buffer
	code := Run([]string{"update", "check", "--manifest-url", "https://updates.example.test/manifest.json"}, &stdout, &stderr)
	if code != 0 {
		t.Fatalf("code = %d stdout = %s stderr = %s", code, stdout.String(), stderr.String())
	}
	assertJSONField(t, stdout.String(), "operation", "update_check")
	assertJSONField(t, stdout.String(), "latest_version", "RES-v0.1.20-deadbee")
	assertJSONField(t, stdout.String(), "source", "remote")
	assertJSONField(t, stdout.String(), "next_action", "update_apply")
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

func TestWriteEvidenceRequiresConfirmationForRealJiraComment(t *testing.T) {
	root := t.TempDir()
	t.Setenv("AGENTIC_OPS_WORKSPACE_ROOT", root)
	runID := "TAP-123-takeover-20260721103012-a8f3"
	writeCLITestFile(t, filepath.Join(root, ".agentic-ops", "feedback", "events.ndjson"), `{"timestamp":"2026-07-21T10:30:12Z","workspace":"tapstate","run_id":"TAP-123-takeover-20260721103012-a8f3","issue_key":"TAP-123","operation":"takeover_task","task_type":"task_takeover","current_stage":"takeover_started","next_action":"proceed","ok":true,"gate":"takeover_task","gate_status":"passed"}
`)
	withJiraClientForTest(t, jiraClientSelection{Client: &recordingJiraClient{issue: realModeBoundIssue()}, Mode: "real"})

	var stdout bytes.Buffer
	var stderr bytes.Buffer
	code := Run([]string{"write-evidence", "--workspace", "tapstate", "--run-id", runID}, &stdout, &stderr)
	if code != 1 {
		t.Fatalf("code = %d stdout = %s stderr = %s", code, stdout.String(), stderr.String())
	}
	assertJSONField(t, stdout.String(), "operation", "write_evidence")
	assertJSONField(t, stdout.String(), "code", "real_jira_confirmation_required")
	assertEventLogContains(t, root, `"operation":"write_evidence"`)
	assertEventLogContains(t, root, `"gate":"real_jira_write"`)
	assertEventLogContains(t, root, `"gate_status":"blocked"`)
}

func TestWriteEvidenceRecordsPassedRealJiraCommentGate(t *testing.T) {
	root := t.TempDir()
	t.Setenv("AGENTIC_OPS_WORKSPACE_ROOT", root)
	runID := "TAP-123-takeover-20260721103012-a8f3"
	writeCLITestFile(t, filepath.Join(root, ".agentic-ops", "feedback", "events.ndjson"), `{"timestamp":"2026-07-21T10:30:12Z","workspace":"tapstate","run_id":"TAP-123-takeover-20260721103012-a8f3","issue_key":"TAP-123","operation":"takeover_task","task_type":"task_takeover","current_stage":"takeover_started","next_action":"proceed","ok":true,"gate":"takeover_task","gate_status":"passed"}
`)
	client := &recordingJiraClient{issue: realModeBoundIssue()}
	withJiraClientForTest(t, jiraClientSelection{Client: client, Mode: "real"})

	var stdout bytes.Buffer
	var stderr bytes.Buffer
	code := Run([]string{"write-evidence", "--workspace", "tapstate", "--run-id", runID, "--confirm-real-jira-write"}, &stdout, &stderr)
	if code != 0 {
		t.Fatalf("code = %d stdout = %s stderr = %s", code, stdout.String(), stderr.String())
	}
	if client.commentKey != "TAP-123" {
		t.Fatalf("commentKey = %s", client.commentKey)
	}
	if !strings.Contains(client.commentBody, "status: evidence_written") {
		t.Fatalf("commentBody = %s", client.commentBody)
	}
	assertEventLogContains(t, root, `"operation":"write_evidence"`)
	assertEventLogContains(t, root, `"gate":"real_jira_write"`)
	assertEventLogContains(t, root, `"gate_status":"passed"`)
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

func TestPolicyValidateOutputsIssueCount(t *testing.T) {
	var stdout bytes.Buffer
	var stderr bytes.Buffer
	code := Run([]string{"policy", "validate", "--workspace", "tapstate"}, &stdout, &stderr)
	if code != 0 {
		t.Fatalf("code = %d stdout = %s stderr = %s", code, stdout.String(), stderr.String())
	}
	assertJSONField(t, stdout.String(), "operation", "policy_validate")
	assertJSONField(t, stdout.String(), "workspace", "tapstate")
	assertJSONField(t, stdout.String(), "policy", "default")
	assertJSONNumber(t, stdout.String(), "issues", 0)
	assertJSONField(t, stdout.String(), "next_action", "continue")
}

func TestPolicyUpdateAndRollbackUseLocalPolicyBackup(t *testing.T) {
	repo := t.TempDir()
	t.Chdir(repo)
	writeCLITestFile(t, filepath.Join(repo, "go.mod"), "module example.local/test\n")
	writeCLITestFile(t, filepath.Join(repo, "contracts", "operations", ".keep"), "")
	target := filepath.Join(repo, "assets", "policies", "default.yaml")
	source := filepath.Join(repo, "incoming", "default-policy.yaml")
	writeCLITestFile(t, target, validCLIPolicyYAML("default", false))
	writeCLITestFile(t, source, validCLIPolicyYAML("default", true))

	var updateStdout bytes.Buffer
	var updateStderr bytes.Buffer
	updateCode := Run([]string{"policy", "update", "--workspace", "tapstate", "--source", source}, &updateStdout, &updateStderr)
	if updateCode != 0 {
		t.Fatalf("updateCode = %d stdout = %s stderr = %s", updateCode, updateStdout.String(), updateStderr.String())
	}
	assertJSONField(t, updateStdout.String(), "operation", "policy_update")
	assertJSONField(t, updateStdout.String(), "workspace", "tapstate")
	assertJSONField(t, updateStdout.String(), "policy", "default")
	assertJSONField(t, updateStdout.String(), "next_action", "policy_validate")
	updated, err := os.ReadFile(target)
	if err != nil {
		t.Fatalf("ReadFile updated error = %v", err)
	}
	if !strings.Contains(string(updated), "write_jira_comment:\n    required: true") {
		t.Fatalf("updated policy = %s", string(updated))
	}

	var rollbackStdout bytes.Buffer
	var rollbackStderr bytes.Buffer
	rollbackCode := Run([]string{"policy", "rollback", "--workspace", "tapstate"}, &rollbackStdout, &rollbackStderr)
	if rollbackCode != 0 {
		t.Fatalf("rollbackCode = %d stdout = %s stderr = %s", rollbackCode, rollbackStdout.String(), rollbackStderr.String())
	}
	assertJSONField(t, rollbackStdout.String(), "operation", "policy_rollback")
	assertJSONField(t, rollbackStdout.String(), "workspace", "tapstate")
	assertJSONField(t, rollbackStdout.String(), "policy", "default")
	assertJSONField(t, rollbackStdout.String(), "next_action", "policy_validate")
	restored, err := os.ReadFile(target)
	if err != nil {
		t.Fatalf("ReadFile restored error = %v", err)
	}
	if !strings.Contains(string(restored), "write_jira_comment:\n    required: false") {
		t.Fatalf("restored policy = %s", string(restored))
	}
}

func TestTakeoverTaskRequiresConfirmationForRealJiraWrite(t *testing.T) {
	root := t.TempDir()
	t.Setenv("AGENTIC_OPS_WORKSPACE_ROOT", root)
	withJiraClientForTest(t, jiraClientSelection{Client: &recordingJiraClient{issue: realModeIssue()}, Mode: "real"})

	var stdout bytes.Buffer
	var stderr bytes.Buffer
	code := Run([]string{"takeover-task", "TAP-123", "--workspace", "tapstate"}, &stdout, &stderr)
	if code != 1 {
		t.Fatalf("code = %d stdout = %s stderr = %s", code, stdout.String(), stderr.String())
	}
	assertJSONField(t, stdout.String(), "operation", "takeover_task")
	assertJSONField(t, stdout.String(), "code", "real_jira_confirmation_required")
	assertJSONField(t, stdout.String(), "current_stage", "takeover_gate")
	assertJSONField(t, stdout.String(), "next_action", "ask_owner")
	assertEventLogContains(t, root, `"gate":"real_jira_write"`)
	assertEventLogContains(t, root, `"gate_status":"blocked"`)
	assertEventLogContains(t, root, `"code":"real_jira_confirmation_required"`)
}

func TestReleaseAgentRequiresConfirmationForRealJiraWrite(t *testing.T) {
	root := t.TempDir()
	t.Setenv("AGENTIC_OPS_WORKSPACE_ROOT", root)
	withJiraClientForTest(t, jiraClientSelection{Client: &recordingJiraClient{issue: realModeBoundIssue()}, Mode: "real"})

	var stdout bytes.Buffer
	var stderr bytes.Buffer
	code := Run([]string{"release-agent", "--workspace", "tapstate", "--run-id", "run-1", "--issue-key", "TAP-123", "--completion-evidence", "evidence.md"}, &stdout, &stderr)
	if code != 1 {
		t.Fatalf("code = %d stdout = %s stderr = %s", code, stdout.String(), stderr.String())
	}
	assertJSONField(t, stdout.String(), "operation", "release_agent")
	assertJSONField(t, stdout.String(), "code", "real_jira_confirmation_required")
	assertJSONField(t, stdout.String(), "current_stage", "completion_cleanup")
	assertJSONField(t, stdout.String(), "next_action", "ask_owner")
	assertEventLogContains(t, root, `"operation":"release_agent"`)
	assertEventLogContains(t, root, `"gate":"real_jira_write"`)
	assertEventLogContains(t, root, `"gate_status":"blocked"`)
	assertEventLogContains(t, root, `"code":"real_jira_confirmation_required"`)
}

func TestTakeoverTaskRecordsPassedRealJiraWriteGate(t *testing.T) {
	root := t.TempDir()
	t.Setenv("AGENTIC_OPS_WORKSPACE_ROOT", root)
	client := &recordingJiraClient{issue: realModeIssue()}
	withJiraClientForTest(t, jiraClientSelection{Client: client, Mode: "real"})

	var stdout bytes.Buffer
	var stderr bytes.Buffer
	code := Run([]string{"takeover-task", "TAP-123", "--workspace", "tapstate", "--confirm-real-jira-write"}, &stdout, &stderr)
	if code != 0 {
		t.Fatalf("code = %d stdout = %s stderr = %s", code, stdout.String(), stderr.String())
	}
	if client.updatedKey != "TAP-123" {
		t.Fatalf("updatedKey = %s", client.updatedKey)
	}
	assertEventLogContains(t, root, `"operation":"takeover_task"`)
	assertEventLogContains(t, root, `"gate":"real_jira_write"`)
	assertEventLogContains(t, root, `"gate_status":"passed"`)
}

func TestReleaseAgentRecordsFailedRealJiraWriteGate(t *testing.T) {
	root := t.TempDir()
	t.Setenv("AGENTIC_OPS_WORKSPACE_ROOT", root)
	client := &recordingJiraClient{issue: realModeBoundIssue(), updateErr: errors.New("jira write denied")}
	withJiraClientForTest(t, jiraClientSelection{Client: client, Mode: "real"})

	var stdout bytes.Buffer
	var stderr bytes.Buffer
	code := Run([]string{"release-agent", "--workspace", "tapstate", "--run-id", "run-1", "--issue-key", "TAP-123", "--completion-evidence", "evidence.md", "--confirm-real-jira-write"}, &stdout, &stderr)
	if code != 1 {
		t.Fatalf("code = %d stdout = %s stderr = %s", code, stdout.String(), stderr.String())
	}
	assertJSONField(t, stdout.String(), "code", "agent_release_failed")
	assertEventLogContains(t, root, `"operation":"release_agent"`)
	assertEventLogContains(t, root, `"gate":"real_jira_write"`)
	assertEventLogContains(t, root, `"gate_status":"failed"`)
	assertEventLogContains(t, root, `"code":"agent_release_failed"`)
}

func TestReleaseAgentTransitionsRealJiraIssueWhenTransitionIDProvided(t *testing.T) {
	root := t.TempDir()
	t.Setenv("AGENTIC_OPS_WORKSPACE_ROOT", root)
	client := &recordingJiraClient{issue: realModeBoundIssue()}
	withJiraClientForTest(t, jiraClientSelection{Client: client, Mode: "real"})

	var stdout bytes.Buffer
	var stderr bytes.Buffer
	code := Run([]string{"release-agent", "--workspace", "tapstate", "--run-id", "run-1", "--issue-key", "TAP-123", "--completion-evidence", "evidence.md", "--confirm-real-jira-write", "--jira-transition-id", "31"}, &stdout, &stderr)
	if code != 0 {
		t.Fatalf("code = %d stdout = %s stderr = %s", code, stdout.String(), stderr.String())
	}
	if client.transitionKey != "TAP-123" || client.transitionID != "31" {
		t.Fatalf("transition = %s %s", client.transitionKey, client.transitionID)
	}
	assertEventLogContains(t, root, `"operation":"release_agent"`)
	assertEventLogContains(t, root, `"current_stage":"jira_transition"`)
	assertEventLogContains(t, root, `"gate":"real_jira_write"`)
	assertEventLogContains(t, root, `"gate_status":"passed"`)
}

func TestReleaseAgentRecordsFailedRealJiraTransitionGate(t *testing.T) {
	root := t.TempDir()
	t.Setenv("AGENTIC_OPS_WORKSPACE_ROOT", root)
	client := &recordingJiraClient{issue: realModeBoundIssue(), transitionErr: errors.New("transition denied")}
	withJiraClientForTest(t, jiraClientSelection{Client: client, Mode: "real"})

	var stdout bytes.Buffer
	var stderr bytes.Buffer
	code := Run([]string{"release-agent", "--workspace", "tapstate", "--run-id", "run-1", "--issue-key", "TAP-123", "--completion-evidence", "evidence.md", "--confirm-real-jira-write", "--jira-transition-id", "31"}, &stdout, &stderr)
	if code != 1 {
		t.Fatalf("code = %d stdout = %s stderr = %s", code, stdout.String(), stderr.String())
	}
	assertJSONField(t, stdout.String(), "code", "jira_transition_failed")
	assertJSONField(t, stdout.String(), "current_stage", "jira_transition")
	assertEventLogContains(t, root, `"operation":"release_agent"`)
	assertEventLogContains(t, root, `"current_stage":"jira_transition"`)
	assertEventLogContains(t, root, `"gate":"real_jira_write"`)
	assertEventLogContains(t, root, `"gate_status":"failed"`)
	assertEventLogContains(t, root, `"code":"jira_transition_failed"`)
}

func TestJiraWriteFieldsUseProfileMapping(t *testing.T) {
	p := profile.Profile{
		JiraFormMapping: profile.FormMapping{
			Fields: map[string]profile.FormField{
				"current_agent_id": {JiraField: "customfield_current_agent_id"},
				"takeover_at":      {JiraField: "customfield_takeover_at"},
			},
		},
	}

	takeoverFields := jiraTakeoverFields(p, "agent-1", "2026-07-21T10:30:12Z")
	if takeoverFields["customfield_current_agent_id"] != "agent-1" {
		t.Fatalf("takeoverFields = %#v", takeoverFields)
	}
	if takeoverFields["customfield_takeover_at"] != "2026-07-21T10:30:12Z" {
		t.Fatalf("takeoverFields = %#v", takeoverFields)
	}
	releaseFields := jiraReleaseFields(p)
	if _, ok := releaseFields["customfield_current_agent_id"]; !ok {
		t.Fatalf("releaseFields missing current agent field: %#v", releaseFields)
	}
	if releaseFields["customfield_current_agent_id"] != nil {
		t.Fatalf("release current agent field = %#v", releaseFields["customfield_current_agent_id"])
	}
}

func TestDefaultJiraClientRequiresRealAdapterConfig(t *testing.T) {
	t.Setenv("AGENTIC_OPS_JIRA_ADAPTER", "real")
	if _, err := defaultJiraClient("tapstate", profile.Profile{}); err == nil {
		t.Fatalf("defaultJiraClient error = nil, want missing config error")
	}

	t.Setenv("AGENTIC_OPS_JIRA_BASE_URL", "https://jira.example.test")
	t.Setenv("AGENTIC_OPS_JIRA_EMAIL", "bot@example.com")
	t.Setenv("AGENTIC_OPS_JIRA_API_TOKEN", "token-123")
	selection, err := defaultJiraClient("tapstate", profile.Profile{})
	if err != nil {
		t.Fatalf("defaultJiraClient error = %v", err)
	}
	if selection.Mode != "real" {
		t.Fatalf("Mode = %s", selection.Mode)
	}
	if _, ok := selection.Client.(*jira.RealClient); !ok {
		t.Fatalf("Client type = %T", selection.Client)
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

func TestFeedbackBundleRedactsSensitiveEventData(t *testing.T) {
	root := t.TempDir()
	t.Setenv("AGENTIC_OPS_WORKSPACE_ROOT", root)
	runID := "run-secret"
	writeCLITestFile(t, filepath.Join(root, ".agentic-ops", "feedback", "events.ndjson"), `{"timestamp":"2026-07-21T10:30:12Z","workspace":"tapstate","run_id":"run-secret","agentic_cli_version":"SRC-source","version_state":"SRC","asset_version":"unknown","task_type":"task_takeover","operation":"takeover_task","current_stage":"takeover_gate","next_action":"ask_owner","ok":false,"code":"missing_jira_field","gate":"takeover_gate","gate_status":"blocked","human_gate":true,"requires_human_action":true,"message":"token=abc123 password=hidden"}
`)

	var stdout bytes.Buffer
	var stderr bytes.Buffer
	code := Run([]string{"feedback", "bundle", "--workspace", "tapstate", "--run-id", runID, "--redact"}, &stdout, &stderr)
	if code != 0 {
		t.Fatalf("code = %d stdout = %s stderr = %s", code, stdout.String(), stderr.String())
	}
	assertJSONField(t, stdout.String(), "operation", "feedback_bundle")
	assertJSONField(t, stdout.String(), "run_id", runID)
	assertJSONField(t, stdout.String(), "redacted", true)
	assertJSONField(t, stdout.String(), "next_action", "share_bundle_with_maintainer")
	var got map[string]any
	if err := json.Unmarshal([]byte(stdout.String()), &got); err != nil {
		t.Fatalf("invalid JSON %s: %v", stdout.String(), err)
	}
	bundlePath, ok := got["bundle"].(string)
	if !ok || bundlePath == "" {
		t.Fatalf("bundle path missing: %s", stdout.String())
	}
	data, err := os.ReadFile(bundlePath)
	if err != nil {
		t.Fatalf("ReadFile bundle error = %v", err)
	}
	if strings.Contains(string(data), "abc123") || strings.Contains(string(data), "hidden") {
		t.Fatalf("bundle was not redacted: %s", string(data))
	}
	if !strings.Contains(string(data), "[REDACTED]") {
		t.Fatalf("bundle missing redaction marker: %s", string(data))
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

func validCLIPolicyYAML(policyName string, requireJiraComment bool) string {
	jiraCommentRequired := "false"
	if requireJiraComment {
		jiraCommentRequired = "true"
	}
	return "policy: " + policyName + "\n" +
		"version: 1\n" +
		"gates:\n" +
		"  write_jira_comment:\n" +
		"    required: " + jiraCommentRequired + "\n" +
		"  transition_jira_status:\n" +
		"    required: true\n" +
		"  git_commit:\n" +
		"    required: true\n" +
		"  git_push:\n" +
		"    required: true\n" +
		"  create_pr:\n" +
		"    required: true\n" +
		"  scope_change:\n" +
		"    required: true\n"
}

func withJiraClientForTest(t *testing.T, selection jiraClientSelection) {
	t.Helper()
	original := selectJiraClient
	selectJiraClient = func(workspaceName string, workspaceProfile profile.Profile) (jiraClientSelection, error) {
		return selection, nil
	}
	t.Cleanup(func() {
		selectJiraClient = original
	})
}

type recordingJiraClient struct {
	issue         jira.Issue
	updatedKey    string
	updatedFields map[string]any
	updateErr     error
	commentKey    string
	commentBody   string
	commentErr    error
	transitionKey string
	transitionID  string
	transitionErr error
}

func (client *recordingJiraClient) CurrentUser(ctx context.Context) (string, error) {
	return "current-user", nil
}

func (client *recordingJiraClient) SearchIssues(ctx context.Context, workspace string, jql string) ([]jira.Issue, error) {
	return []jira.Issue{client.issue}, nil
}

func (client *recordingJiraClient) GetIssueByKey(ctx context.Context, workspace string, key string) (jira.Issue, bool, error) {
	if client.issue.Key == key {
		return client.issue, true, nil
	}
	return jira.Issue{}, false, nil
}

func (client *recordingJiraClient) AddComment(ctx context.Context, key string, body string) error {
	client.commentKey = key
	client.commentBody = body
	if client.commentErr != nil {
		return client.commentErr
	}
	return nil
}

func (client *recordingJiraClient) UpdateFields(ctx context.Context, key string, fields map[string]any) error {
	client.updatedKey = key
	client.updatedFields = fields
	if client.updateErr != nil {
		return client.updateErr
	}
	return nil
}

func (client *recordingJiraClient) TransitionIssue(ctx context.Context, key string, transitionID string) error {
	client.transitionKey = key
	client.transitionID = transitionID
	if client.transitionErr != nil {
		return client.transitionErr
	}
	return nil
}

func assertEventLogContains(t *testing.T, root string, want string) {
	t.Helper()
	events, err := os.ReadFile(filepath.Join(root, ".agentic-ops", "feedback", "events.ndjson"))
	if err != nil {
		t.Fatalf("ReadFile events error = %v", err)
	}
	if !strings.Contains(string(events), want) {
		t.Fatalf("events missing %s: %s", want, string(events))
	}
}

type cliRoundTripFunc func(*http.Request) *http.Response

func (fn cliRoundTripFunc) RoundTrip(request *http.Request) (*http.Response, error) {
	return fn(request), nil
}

func cliHTTPResponse(statusCode int, body string) *http.Response {
	return &http.Response{
		StatusCode: statusCode,
		Header:     make(http.Header),
		Body:       io.NopCloser(strings.NewReader(body)),
	}
}

func realModeIssue() jira.Issue {
	return jira.Issue{
		Key:                "TAP-123",
		Summary:            "修复示例任务",
		Owner:              "current-user",
		Assignee:           "current-user",
		IssueType:          "Task",
		Status:             "To Do",
		TargetRepo:         "tapstate/example-repo",
		AcceptanceCriteria: "单元测试通过",
		VerificationMethod: "go test ./...",
		RiskLevel:          "low",
	}
}

func realModeBoundIssue() jira.Issue {
	issue := realModeIssue()
	issue.CurrentAgentID = "agentic-cli-local-agent"
	return issue
}
