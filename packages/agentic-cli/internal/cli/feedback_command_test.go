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
	assertJSONField(t, stdout.String(), "next_action", "review_proposals")
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
