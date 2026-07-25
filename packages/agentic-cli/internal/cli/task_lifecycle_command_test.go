package cli

import (
	"bytes"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/tapstate/agentic-ops/packages/agentic-cli/internal/clihandlers"
)

func TestTaskRunCompletesEmptyLifecycle(t *testing.T) {
	root := t.TempDir()
	t.Setenv("AGENTIC_OPS_WORKSPACE_ROOT", root)

	var stdout bytes.Buffer
	var stderr bytes.Buffer
	code := Run([]string{"task", "run", "TAP-123", "--workspace", "tapstate", "--process", "empty"}, &stdout, &stderr)
	if code != 0 {
		t.Fatalf("code = %d stdout = %s stderr = %s", code, stdout.String(), stderr.String())
	}

	assertJSONField(t, stdout.String(), "operation", "task_run")
	assertJSONField(t, stdout.String(), "issue_key", "TAP-123")
	assertJSONField(t, stdout.String(), "task_class", "technical_task")
	assertJSONField(t, stdout.String(), "process_id", "development_change_v1")
	assertJSONField(t, stdout.String(), "capability_id", "empty_task_v1")
	assertJSONField(t, stdout.String(), "current_stage", "completed")
	assertJSONField(t, stdout.String(), "next_action", "task_audit_submitted")
	assertJSONField(t, stdout.String(), "current_agent_id_cleared", true)
	assertJSONField(t, stdout.String(), "audit_submitted", true)

	eventsPath := filepath.Join(root, ".agentic-ops", "feedback", "events.ndjson")
	assertFileContains(t, eventsPath, `"operation":"task_run"`)
	assertFileContains(t, eventsPath, `"gate":"takeover"`)
	assertFileContains(t, eventsPath, `"gate":"process"`)
	assertFileContains(t, eventsPath, `"gate":"writeback"`)
	assertFileContains(t, eventsPath, `"capability_id":"empty_task_v1"`)
	assertFileContains(t, eventsPath, `"current_agent_id_cleared":true`)
}

func TestTaskRunSelectsDefectFixCapability(t *testing.T) {
	root := t.TempDir()
	t.Setenv("AGENTIC_OPS_WORKSPACE_ROOT", root)

	var stdout bytes.Buffer
	var stderr bytes.Buffer
	code := Run([]string{"task", "run", "TAP-BUG-123", "--workspace", "tapstate"}, &stdout, &stderr)
	if code != 0 {
		t.Fatalf("code = %d stdout = %s stderr = %s", code, stdout.String(), stderr.String())
	}

	assertJSONField(t, stdout.String(), "operation", "task_run")
	assertJSONField(t, stdout.String(), "issue_key", "TAP-BUG-123")
	assertJSONField(t, stdout.String(), "task_class", "bug_fix")
	assertJSONField(t, stdout.String(), "capability_id", "defect_fix_v1")
	assertJSONField(t, stdout.String(), "defect_complexity", "normal")
	assertJSONField(t, stdout.String(), "current_stage", "implementation")
	assertJSONField(t, stdout.String(), "next_action", "start_defect_fix")
	assertJSONField(t, stdout.String(), "current_agent_id_cleared", false)
	assertJSONField(t, stdout.String(), "audit_submitted", false)
	assertEventLogContains(t, root, `"capability_id":"defect_fix_v1"`)
	assertEventLogContains(t, root, `"defect_complexity":"normal"`)
}

func TestTaskRunBlocksOtherAgentOwnership(t *testing.T) {
	root := t.TempDir()
	t.Setenv("AGENTIC_OPS_WORKSPACE_ROOT", root)

	var stdout bytes.Buffer
	var stderr bytes.Buffer
	code := Run([]string{"task", "run", "TAP-AGENT-CONFLICT", "--workspace", "tapstate"}, &stdout, &stderr)
	if code != 1 {
		t.Fatalf("code = %d stdout = %s stderr = %s", code, stdout.String(), stderr.String())
	}

	assertJSONField(t, stdout.String(), "operation", "task_run")
	assertJSONField(t, stdout.String(), "code", "agent_ownership_conflict")
	assertJSONField(t, stdout.String(), "current_stage", "takeover_gate")
	assertJSONField(t, stdout.String(), "next_action", "ask_owner")
	assertEventLogContains(t, root, `"gate":"takeover"`)
	assertEventLogContains(t, root, `"gate_status":"blocked"`)
	assertEventLogContains(t, root, `"code":"agent_ownership_conflict"`)
}

func TestTaskRunRequiresConfirmationForRealJiraWrite(t *testing.T) {
	root := t.TempDir()
	t.Setenv("AGENTIC_OPS_WORKSPACE_ROOT", root)
	withJiraClientForTest(t, clihandlers.JiraClientSelection{Client: &recordingJiraClient{issue: realModeIssue()}, Mode: "real"})

	var stdout bytes.Buffer
	var stderr bytes.Buffer
	code := Run([]string{"task", "run", "TAP-123", "--workspace", "tapstate", "--process", "empty"}, &stdout, &stderr)
	if code != 1 {
		t.Fatalf("code = %d stdout = %s stderr = %s", code, stdout.String(), stderr.String())
	}

	assertJSONField(t, stdout.String(), "operation", "task_run")
	assertJSONField(t, stdout.String(), "code", "real_jira_confirmation_required")
	assertJSONField(t, stdout.String(), "current_stage", "takeover_gate")
	assertJSONField(t, stdout.String(), "next_action", "ask_owner")
	assertEventLogContains(t, root, `"gate":"real_jira_write"`)
	assertEventLogContains(t, root, `"gate_status":"blocked"`)
}

func TestTaskRunBlocksRealTapdataWithoutStableOwnershipFields(t *testing.T) {
	root := t.TempDir()
	t.Setenv("AGENTIC_OPS_WORKSPACE_ROOT", root)
	withJiraClientForTest(t, clihandlers.JiraClientSelection{Client: &recordingJiraClient{issue: realModeIssue()}, Mode: "real"})

	var stdout bytes.Buffer
	var stderr bytes.Buffer
	code := Run([]string{"task", "run", "TAP-123", "--workspace", "tapdata", "--process", "empty", "--confirm-real-jira-write"}, &stdout, &stderr)
	if code != 1 {
		t.Fatalf("code = %d stdout = %s stderr = %s", code, stdout.String(), stderr.String())
	}

	assertJSONField(t, stdout.String(), "operation", "task_run")
	assertJSONField(t, stdout.String(), "code", "missing_jira_write_mapping")
	assertJSONField(t, stdout.String(), "current_stage", "takeover_gate")
	assertJSONField(t, stdout.String(), "next_action", "ask_owner")
	assertEventLogContains(t, root, `"gate":"real_jira_write"`)
	assertEventLogContains(t, root, `"gate_status":"blocked"`)
}

func assertFileContains(t *testing.T, path string, want string) {
	t.Helper()
	data, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("ReadFile %s error = %v", path, err)
	}
	if !strings.Contains(string(data), want) {
		t.Fatalf("%s missing %s: %s", path, want, string(data))
	}
}
