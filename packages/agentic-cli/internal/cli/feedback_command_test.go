package cli

import (
	"bytes"
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestFeedbackReportOutputsReportPath(t *testing.T) {
	root := t.TempDir()
	t.Setenv("AGENTIC_OPS_WORKSPACE_ROOT", root)
	Run([]string{"takeover-task", "TAP-123", "--workspace", "tapstate"}, &bytes.Buffer{}, &bytes.Buffer{})
	Run([]string{"write-evidence", "--workspace", "tapstate", "--run-id", "TAP-123-takeover-20260721103012-a8f3"}, &bytes.Buffer{}, &bytes.Buffer{})
	Run([]string{"takeover-task", "TAP-MISSING-REPO", "--workspace", "tapstate"}, &bytes.Buffer{}, &bytes.Buffer{})

	var stdout bytes.Buffer
	var stderr bytes.Buffer
	code := Run([]string{"feedback", "report", "--workspace", "tapstate", "--date", "2026-07-21"}, &stdout, &stderr)
	if code != 0 {
		t.Fatalf("code = %d", code)
	}
	assertJSONField(t, stdout.String(), "operation", "feedback_report")
	assertJSONField(t, stdout.String(), "agentic_next_action", "review_proposals")
	assertJSONNumber(t, stdout.String(), "runs", 3)
	assertJSONNumber(t, stdout.String(), "succeeded", 3)
	assertJSONNumber(t, stdout.String(), "blocked", 0)
	data, err := os.ReadFile(filepath.Join(root, ".agentic-ops", "feedback", "reports", "2026-07-21.md"))
	if err != nil {
		t.Fatalf("ReadFile report error = %v", err)
	}
	if !strings.Contains(string(data), "runs: 3") || !strings.Contains(string(data), "blocked: 0") {
		t.Fatalf("report = %s", string(data))
	}
}

func TestFeedbackBundleRedactsSensitiveEventData(t *testing.T) {
	root := t.TempDir()
	t.Setenv("AGENTIC_OPS_WORKSPACE_ROOT", root)
	runID := "run-secret"
	writeCLITestFile(t, filepath.Join(root, ".agentic-ops", "feedback", "events.ndjson"), `{"timestamp":"2026-07-21T10:30:12Z","workspace":"tapstate","agentic_run_id":"run-secret","agentic_cli_version":"SRC-source","version_state":"SRC","asset_version":"unknown","task_type":"task_takeover","operation":"takeover_task","current_stage":"takeover_gate","agentic_next_action":"ask_owner","ok":false,"code":"missing_jira_field","gate":"takeover_gate","gate_status":"blocked","human_gate":true,"requires_human_action":true,"message":"token=abc123 password=hidden"}
`)

	var stdout bytes.Buffer
	var stderr bytes.Buffer
	code := Run([]string{"feedback", "bundle", "--workspace", "tapstate", "--run-id", runID, "--redact"}, &stdout, &stderr)
	if code != 0 {
		t.Fatalf("code = %d stdout = %s stderr = %s", code, stdout.String(), stderr.String())
	}
	assertJSONField(t, stdout.String(), "operation", "feedback_bundle")
	assertJSONField(t, stdout.String(), "agentic_run_id", runID)
	assertJSONField(t, stdout.String(), "redacted", true)
	assertJSONField(t, stdout.String(), "agentic_next_action", "share_bundle_with_maintainer")
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

func TestFeedbackReportFiltersEventsByRunAndCode(t *testing.T) {
	root := t.TempDir()
	t.Setenv("AGENTIC_OPS_WORKSPACE_ROOT", root)
	writeCLITestFile(t, filepath.Join(root, ".agentic-ops", "feedback", "events.ndjson"), ""+
		`{"timestamp":"2026-07-21T10:30:12Z","workspace":"tapstate","agentic_run_id":"run-1","issue_key":"TAP-123","task_type":"defect","operation":"takeover_task","ok":false,"code":"missing_jira_field"}`+"\n"+`{"timestamp":"2026-07-21T10:40:12Z","workspace":"tapstate","agentic_run_id":"run-2","issue_key":"TAP-123","task_type":"defect","operation":"write_evidence","ok":false,"code":"policy_gate_required"}`+"\n")

	var stdout bytes.Buffer
	code := Run([]string{"feedback", "report", "--workspace", "tapstate", "--date", "2026-07-21", "--run-id", "run-1", "--code", "missing_jira_field"}, &stdout, &bytes.Buffer{})
	if code != 0 {
		t.Fatalf("code = %d stdout = %s", code, stdout.String())
	}
	assertJSONNumber(t, stdout.String(), "runs", 1)
	assertJSONNumber(t, stdout.String(), "failed", 1)
}

func TestFeedbackAnalyzeAndProposeReturnStructuredOutputs(t *testing.T) {
	root := t.TempDir()
	t.Setenv("AGENTIC_OPS_WORKSPACE_ROOT", root)
	writeCLITestFile(t, filepath.Join(root, ".agentic-ops", "feedback", "events.ndjson"), ""+
		`{"timestamp":"2026-07-21T10:30:12Z","workspace":"tapstate","agentic_run_id":"run-1","task_type":"defect","operation":"takeover_task","ok":false,"code":"missing_jira_field","missing_field":"target_repo","gate":"takeover_gate","requires_human_action":true}`+"\n")

	var analysisStdout bytes.Buffer
	code := Run([]string{"feedback", "analyze", "--workspace", "tapstate", "--date", "2026-07-21"}, &analysisStdout, &bytes.Buffer{})
	if code != 0 {
		t.Fatalf("analyze code = %d stdout = %s", code, analysisStdout.String())
	}
	assertJSONField(t, analysisStdout.String(), "operation", "feedback_analyze")
	if !strings.Contains(analysisStdout.String(), "missing_jira_field") {
		t.Fatalf("analysis = %s", analysisStdout.String())
	}

	var proposalStdout bytes.Buffer
	code = Run([]string{"feedback", "propose", "--workspace", "tapstate", "--date", "2026-07-21"}, &proposalStdout, &bytes.Buffer{})
	if code != 0 {
		t.Fatalf("propose code = %d stdout = %s", code, proposalStdout.String())
	}
	assertJSONField(t, proposalStdout.String(), "operation", "feedback_propose")
	if !strings.Contains(proposalStdout.String(), "recommended_asset") {
		t.Fatalf("proposals = %s", proposalStdout.String())
	}
}
