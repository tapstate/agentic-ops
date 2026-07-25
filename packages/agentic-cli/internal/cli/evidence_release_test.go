package cli

import (
	"bytes"
	"errors"
	"github.com/tapstate/agentic-ops/packages/agentic-cli/internal/clihandlers"
	"github.com/tapstate/agentic-ops/packages/agentic-cli/internal/jira"
	"github.com/tapstate/agentic-ops/packages/agentic-cli/internal/profile"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

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
	t.Setenv("AGENTIC_OPS_WORKSPACE_ROOT", root)
	runID := "TAP-123-takeover-20260721103012-a8f3"
	writeCLITestFile(t, filepath.Join(root, ".agentic-ops", "feedback", "events.ndjson"), `{"timestamp":"2026-07-21T10:30:12Z","workspace":"tapstate","run_id":"TAP-123-takeover-20260721103012-a8f3","issue_key":"TAP-123","operation":"takeover_task","task_type":"task_takeover","current_stage":"takeover_started","next_action":"proceed","agent_id":"agentic-cli-local-agent","current_agent_id":"agentic-cli-local-agent","task_class":"technical_task","process_id":"development_change_v1","target_repo":"tapstate/example-repo","ok":true,"gate":"takeover_task","gate_status":"passed"}
`)
	var stdout bytes.Buffer
	var stderr bytes.Buffer
	code := Run([]string{"write-evidence", "--workspace", "tapstate", "--run-id", runID}, &stdout, &stderr)
	if code != 0 {
		t.Fatalf("code = %d", code)
	}
	assertJSONField(t, stdout.String(), "operation", "write_evidence")
	assertJSONField(t, stdout.String(), "issue_key", "TAP-123")
	assertJSONField(t, stdout.String(), "task_class", "technical_task")
	assertJSONField(t, stdout.String(), "process_id", "development_change_v1")
	assertJSONField(t, stdout.String(), "target_repo", "tapstate/example-repo")
	assertJSONField(t, stdout.String(), "audit_submitted", true)
	assertJSONField(t, stdout.String(), "next_action", "request_owner_confirmation")
	evidencePath := filepath.Join(root, ".agentic-ops", "runs", runID, "evidence.md")
	evidenceData, err := os.ReadFile(evidencePath)
	if err != nil {
		t.Fatalf("ReadFile evidence error = %v", err)
	}
	for _, want := range []string{"issue_key: TAP-123", "task_class: technical_task", "process_id: development_change_v1", "target_repo: tapstate/example-repo"} {
		if !strings.Contains(string(evidenceData), want) {
			t.Fatalf("evidence missing %q: %s", want, string(evidenceData))
		}
	}
	events, err := os.ReadFile(filepath.Join(root, ".agentic-ops", "feedback", "events.ndjson"))
	if err != nil {
		t.Fatalf("ReadFile events error = %v", err)
	}
	if !strings.Contains(string(events), `"operation":"write_evidence"`) {
		t.Fatalf("events = %s", string(events))
	}
	if !strings.Contains(string(events), `"audit_submitted":true`) {
		t.Fatalf("events = %s", string(events))
	}
}

func TestWriteEvidencePreservesTargetRepoAfterResume(t *testing.T) {
	root := t.TempDir()
	t.Setenv("AGENTIC_OPS_WORKSPACE_ROOT", root)
	runID := "TAP-123-takeover-20260721103012-a8f3"
	writeCLITestFile(t, filepath.Join(root, ".agentic-ops", "feedback", "events.ndjson"), `{"timestamp":"2026-07-21T10:30:12Z","workspace":"tapstate","run_id":"TAP-123-takeover-20260721103012-a8f3","issue_key":"TAP-123","operation":"takeover_task","task_type":"task_takeover","current_stage":"takeover_started","next_action":"proceed","agent_id":"agentic-cli-local-agent","current_agent_id":"agentic-cli-local-agent","task_class":"technical_task","process_id":"development_change_v1","target_repo":"tapstate/example-repo","ok":true,"gate":"takeover_task","gate_status":"passed"}
{"timestamp":"2026-07-21T10:31:00Z","workspace":"tapstate","run_id":"TAP-123-takeover-20260721103012-a8f3","issue_key":"TAP-123","operation":"resume_takeover","task_type":"task_takeover","current_stage":"takeover_resumed","next_action":"continue_development","agent_id":"agentic-cli-local-agent","current_agent_id":"agentic-cli-local-agent","task_class":"technical_task","process_id":"development_change_v1","ok":true,"gate":"resume_takeover","gate_status":"passed"}
`)
	var stdout bytes.Buffer
	var stderr bytes.Buffer
	code := Run([]string{"write-evidence", "--workspace", "tapstate", "--run-id", runID}, &stdout, &stderr)
	if code != 0 {
		t.Fatalf("code = %d stdout = %s stderr = %s", code, stdout.String(), stderr.String())
	}
	assertJSONField(t, stdout.String(), "target_repo", "tapstate/example-repo")
	evidenceData, err := os.ReadFile(filepath.Join(root, ".agentic-ops", "runs", runID, "evidence.md"))
	if err != nil {
		t.Fatalf("ReadFile evidence error = %v", err)
	}
	if !strings.Contains(string(evidenceData), "target_repo: tapstate/example-repo") {
		t.Fatalf("evidence = %s", string(evidenceData))
	}
}

func TestWriteEvidenceBlocksWhenLocalPolicyRequiresHumanGate(t *testing.T) {
	repo := t.TempDir()
	t.Chdir(repo)
	writeCLITestFile(t, filepath.Join(repo, "go.mod"), "module example.local/test\n")
	writeCLITestFile(t, filepath.Join(repo, "install-resources", "basic", "contracts", "operations", ".keep"), "")
	writeCLITestFile(t, filepath.Join(repo, "install-resources", "basic", "policies", "default.yaml"), validCLIPolicyYAMLWithEvidenceGate("default", false, true))
	root := t.TempDir()
	t.Setenv("AGENTIC_OPS_WORKSPACE_ROOT", root)
	runID := "TAP-123-takeover-20260721103012-a8f3"
	writeCLITestFile(t, filepath.Join(root, ".agentic-ops", "feedback", "events.ndjson"), `{"timestamp":"2026-07-21T10:30:12Z","workspace":"tapstate","run_id":"TAP-123-takeover-20260721103012-a8f3","issue_key":"TAP-123","operation":"takeover_task","task_type":"task_takeover","current_stage":"takeover_started","next_action":"proceed","agent_id":"agentic-cli-local-agent","current_agent_id":"agentic-cli-local-agent","task_class":"technical_task","process_id":"development_change_v1","target_repo":"tapstate/example-repo","ok":true,"gate":"takeover_task","gate_status":"passed"}
`)
	var stdout bytes.Buffer
	var stderr bytes.Buffer
	code := Run([]string{"write-evidence", "--workspace", "tapstate", "--run-id", runID}, &stdout, &stderr)
	if code != 1 {
		t.Fatalf("code = %d stdout = %s stderr = %s", code, stdout.String(), stderr.String())
	}
	assertJSONField(t, stdout.String(), "operation", "write_evidence")
	assertJSONField(t, stdout.String(), "code", "policy_gate_required")
	assertJSONField(t, stdout.String(), "current_stage", "evidence_write_gate")
	assertJSONField(t, stdout.String(), "next_action", "ask_owner")
	if _, err := os.Stat(filepath.Join(root, ".agentic-ops", "runs", runID, "evidence.md")); !os.IsNotExist(err) {
		t.Fatalf("evidence file should not be written when policy gate blocks, stat err = %v", err)
	}
}

func TestWriteEvidenceRejectsMissingRun(t *testing.T) {
	root := t.TempDir()
	t.Setenv("AGENTIC_OPS_WORKSPACE_ROOT", root)
	var stdout bytes.Buffer
	var stderr bytes.Buffer
	code := Run([]string{"write-evidence", "--workspace", "tapstate", "--run-id", "missing-run"}, &stdout, &stderr)
	if code != 1 {
		t.Fatalf("code = %d stdout = %s stderr = %s", code, stdout.String(), stderr.String())
	}
	assertJSONField(t, stdout.String(), "operation", "write_evidence")
	assertJSONField(t, stdout.String(), "code", "run_not_found")
	assertJSONField(t, stdout.String(), "current_stage", "evidence_write_gate")
	assertJSONField(t, stdout.String(), "next_action", "ask_owner")
}

func TestWriteEvidenceRequiresConfirmationForRealJiraComment(t *testing.T) {
	root := t.TempDir()
	t.Setenv("AGENTIC_OPS_WORKSPACE_ROOT", root)
	runID := "TAP-123-takeover-20260721103012-a8f3"
	writeCLITestFile(t, filepath.Join(root, ".agentic-ops", "feedback", "events.ndjson"), `{"timestamp":"2026-07-21T10:30:12Z","workspace":"tapstate","run_id":"TAP-123-takeover-20260721103012-a8f3","issue_key":"TAP-123","operation":"takeover_task","task_type":"task_takeover","current_stage":"takeover_started","next_action":"proceed","agent_id":"agentic-cli-local-agent","current_agent_id":"agentic-cli-local-agent","task_class":"technical_task","process_id":"development_change_v1","target_repo":"tapstate/example-repo","ok":true,"gate":"takeover_task","gate_status":"passed"}
`)
	withJiraClientForTest(t, clihandlers.JiraClientSelection{Client: &recordingJiraClient{issue: realModeBoundIssue()}, Mode: "real"})

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
	writeCLITestFile(t, filepath.Join(root, ".agentic-ops", "feedback", "events.ndjson"), `{"timestamp":"2026-07-21T10:30:12Z","workspace":"tapstate","run_id":"TAP-123-takeover-20260721103012-a8f3","issue_key":"TAP-123","operation":"takeover_task","task_type":"task_takeover","current_stage":"takeover_started","next_action":"proceed","agent_id":"agentic-cli-local-agent","current_agent_id":"agentic-cli-local-agent","task_class":"technical_task","process_id":"development_change_v1","target_repo":"tapstate/example-repo","ok":true,"gate":"takeover_task","gate_status":"passed"}
`)
	client := &recordingJiraClient{issue: realModeBoundIssue()}
	withJiraClientForTest(t, clihandlers.JiraClientSelection{Client: client, Mode: "real"})

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

func TestReleaseAgentRequiresConfirmationForRealJiraWrite(t *testing.T) {
	root := t.TempDir()
	t.Setenv("AGENTIC_OPS_WORKSPACE_ROOT", root)
	withJiraClientForTest(t, clihandlers.JiraClientSelection{Client: &recordingJiraClient{issue: realModeBoundIssue()}, Mode: "real"})

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

func TestReleaseAgentRecordsFailedRealJiraWriteGate(t *testing.T) {
	root := t.TempDir()
	t.Setenv("AGENTIC_OPS_WORKSPACE_ROOT", root)
	client := &recordingJiraClient{issue: realModeBoundIssue(), updateErr: errors.New("jira write denied")}
	withJiraClientForTest(t, clihandlers.JiraClientSelection{Client: client, Mode: "real"})

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
	withJiraClientForTest(t, clihandlers.JiraClientSelection{Client: client, Mode: "real"})

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

func TestReleaseAgentUsesProfileJiraTransitionMapping(t *testing.T) {
	root := t.TempDir()
	t.Setenv("AGENTIC_OPS_WORKSPACE_ROOT", root)
	client := &recordingJiraClient{issue: realModeBoundIssue()}
	withJiraClientForTest(t, clihandlers.JiraClientSelection{Client: client, Mode: "real"})

	var stdout bytes.Buffer
	var stderr bytes.Buffer
	code := Run([]string{"release-agent", "--workspace", "tapstate", "--run-id", "run-1", "--issue-key", "TAP-123", "--completion-evidence", "evidence.md", "--confirm-real-jira-write"}, &stdout, &stderr)
	if code != 0 {
		t.Fatalf("code = %d stdout = %s stderr = %s", code, stdout.String(), stderr.String())
	}
	if client.transitionKey != "TAP-123" || client.transitionID != "31" {
		t.Fatalf("transition = %s %s", client.transitionKey, client.transitionID)
	}
	assertJSONField(t, stdout.String(), "jira_transition_id", "31")
	assertEventLogContains(t, root, `"current_stage":"jira_transition"`)
	assertEventLogContains(t, root, `"gate_status":"passed"`)
}

func TestReleaseAgentRecordsFailedRealJiraTransitionGate(t *testing.T) {
	root := t.TempDir()
	t.Setenv("AGENTIC_OPS_WORKSPACE_ROOT", root)
	client := &recordingJiraClient{issue: realModeBoundIssue(), transitionErr: errors.New("transition denied")}
	withJiraClientForTest(t, clihandlers.JiraClientSelection{Client: client, Mode: "real"})

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

	takeoverFields := clihandlers.JiraTakeoverFields(p, "agent-1", "2026-07-21T10:30:12Z")
	if takeoverFields["customfield_current_agent_id"] != "agent-1" {
		t.Fatalf("takeoverFields = %#v", takeoverFields)
	}
	if takeoverFields["customfield_takeover_at"] != "2026-07-21T10:30:12Z" {
		t.Fatalf("takeoverFields = %#v", takeoverFields)
	}
	releaseFields := clihandlers.JiraReleaseFields(p)
	if _, ok := releaseFields["customfield_current_agent_id"]; !ok {
		t.Fatalf("releaseFields missing current agent field: %#v", releaseFields)
	}
	if releaseFields["customfield_current_agent_id"] != nil {
		t.Fatalf("release current agent field = %#v", releaseFields["customfield_current_agent_id"])
	}
}

func TestDefaultJiraClientRequiresRealAdapterConfig(t *testing.T) {
	t.Setenv("AGENTIC_OPS_JIRA_ADAPTER", "real")
	if _, err := clihandlers.DefaultJiraClient("tapstate", profile.Profile{}); err == nil {
		t.Fatalf("clihandlers.DefaultJiraClient error = nil, want missing config error")
	}

	t.Setenv("AGENTIC_OPS_JIRA_BASE_URL", "https://jira.example.test")
	t.Setenv("AGENTIC_OPS_JIRA_EMAIL", "bot@example.com")
	t.Setenv("AGENTIC_OPS_JIRA_API_TOKEN", "token-123")
	selection, err := clihandlers.DefaultJiraClient("tapstate", profile.Profile{})
	if err != nil {
		t.Fatalf("clihandlers.DefaultJiraClient error = %v", err)
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
	writeCLITestFile(t, filepath.Join(root, ".agentic-ops", "runs", runID, "evidence.md"), "# Evidence\n")

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
	assertJSONField(t, stdout.String(), "audit_submitted", true)
	assertJSONField(t, stdout.String(), "audit_target", "local_file")
	assertJSONField(t, stdout.String(), "next_action", "task_audit_submitted")

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
		`"audit_submitted":true`,
		`"audit_target":"local_file"`,
	} {
		if !strings.Contains(string(events), want) {
			t.Fatalf("events missing %s: %s", want, string(events))
		}
	}
}

func TestReleaseAgentRejectsMissingCompletionEvidenceFile(t *testing.T) {
	root := t.TempDir()
	t.Setenv("AGENTIC_OPS_WORKSPACE_ROOT", root)
	runID := "TAP-123-takeover-20260721103012-a8f3"
	Run([]string{"takeover-task", "TAP-123", "--workspace", "tapstate"}, &bytes.Buffer{}, &bytes.Buffer{})

	var stdout bytes.Buffer
	var stderr bytes.Buffer
	code := Run([]string{"release-agent", "--workspace", "tapstate", "--run-id", runID, "--issue-key", "TAP-123", "--completion-evidence", "missing.md"}, &stdout, &stderr)
	if code != 1 {
		t.Fatalf("code = %d stdout = %s stderr = %s", code, stdout.String(), stderr.String())
	}
	assertJSONField(t, stdout.String(), "operation", "release_agent")
	assertJSONField(t, stdout.String(), "code", "completion_evidence_missing")
	assertJSONField(t, stdout.String(), "current_stage", "completion_cleanup")
	assertJSONField(t, stdout.String(), "next_action", "ask_owner")
}
