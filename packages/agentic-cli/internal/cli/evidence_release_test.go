package cli

import (
	"bytes"
	"errors"
	"github.com/tapstate/agentic-ops/packages/agentic-cli/internal/clihandlers"
	"github.com/tapstate/agentic-ops/packages/agentic-cli/internal/github"
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
	assertJSONField(t, stdout.String(), "code", "missing_agentic_run_id")
	assertJSONField(t, stdout.String(), "task_type", "evidence_write")
	assertJSONField(t, stdout.String(), "current_stage", "input_validation")
	assertJSONField(t, stdout.String(), "agentic_next_action", "ask_owner")
	assertJSONField(t, stdout.String(), "required_human_action", "请提供 --run-id")
	events, err := os.ReadFile(filepath.Join(root, ".agentic-ops", "feedback", "events.ndjson"))
	if err != nil {
		t.Fatalf("ReadFile events error = %v", err)
	}
	if !strings.Contains(string(events), `"code":"missing_agentic_run_id"`) {
		t.Fatalf("events = %s", string(events))
	}
	if !strings.Contains(string(events), `"gate_status":"blocked"`) {
		t.Fatalf("events = %s", string(events))
	}
}

func TestWriteEvidenceRequiresCompletionContentBeforeWrites(t *testing.T) {
	root := t.TempDir()
	t.Setenv("AGENTIC_OPS_WORKSPACE_ROOT", root)
	runID := "TAP-123-takeover-20260721103012-a8f3"
	writeCLITestFile(t, filepath.Join(root, ".agentic-ops", "feedback", "events.ndjson"), `{"timestamp":"2026-07-21T10:30:12Z","workspace":"tapstate","agentic_run_id":"TAP-123-takeover-20260721103012-a8f3","issue_key":"TAP-123","operation":"takeover_task","task_type":"task_takeover","current_stage":"takeover_started","agentic_next_action":"proceed","agent_id":"agentic-cli-local-agent","agentic_id":"agentic-cli-local-agent","agentic_takeover_at":"2026-07-21T10:30:12Z","agentic_heartbeat_at":"2026-07-21T10:30:12Z","task_class":"technical_task","process_id":"development_change_v1","target_repo":"tapstate/example-repo","ok":true,"gate":"takeover_task","gate_status":"passed"}
`)
	client := &recordingJiraClient{issue: realModeBoundIssue()}
	withJiraClientForTest(t, clihandlers.JiraClientSelection{Client: client, Mode: "real"})

	var stdout bytes.Buffer
	var stderr bytes.Buffer
	code := Run([]string{"write-evidence", "--workspace", "tapstate", "--run-id", runID, "--confirm-real-jira-write"}, &stdout, &stderr)
	if code != 1 {
		t.Fatalf("code = %d stdout = %s stderr = %s", code, stdout.String(), stderr.String())
	}
	assertJSONField(t, stdout.String(), "code", "missing_evidence_content")
	if client.commentCalls != 0 {
		t.Fatalf("Jira comment calls = %d", client.commentCalls)
	}
	if _, err := os.Stat(filepath.Join(root, ".agentic-ops", "runs", runID, "evidence.md")); !os.IsNotExist(err) {
		t.Fatalf("evidence file should not exist, stat err = %v", err)
	}
}

func TestWriteEvidenceRejectsInvalidCompletionContentBeforeWrites(t *testing.T) {
	root := t.TempDir()
	t.Setenv("AGENTIC_OPS_WORKSPACE_ROOT", root)
	runID := "TAP-123-takeover-20260721103012-a8f3"
	writeCLITestFile(t, filepath.Join(root, ".agentic-ops", "feedback", "events.ndjson"), `{"timestamp":"2026-07-21T10:30:12Z","workspace":"tapstate","agentic_run_id":"TAP-123-takeover-20260721103012-a8f3","issue_key":"TAP-123","operation":"takeover_task","task_type":"task_takeover","current_stage":"takeover_started","agentic_next_action":"proceed","agent_id":"agentic-cli-local-agent","agentic_id":"agentic-cli-local-agent","agentic_takeover_at":"2026-07-21T10:30:12Z","agentic_heartbeat_at":"2026-07-21T10:30:12Z","task_class":"technical_task","process_id":"development_change_v1","target_repo":"tapstate/example-repo","ok":true,"gate":"takeover_task","gate_status":"passed"}
`)
	contentPath := filepath.Join(root, "completion.md")
	writeCLITestFile(t, contentPath, "## 变更内容\n\n只有一个章节。\n")
	client := &recordingJiraClient{issue: realModeBoundIssue()}
	withJiraClientForTest(t, clihandlers.JiraClientSelection{Client: client, Mode: "real"})

	var stdout bytes.Buffer
	var stderr bytes.Buffer
	code := Run([]string{"write-evidence", "--workspace", "tapstate", "--run-id", runID, "--content-file", contentPath, "--confirm-real-jira-write"}, &stdout, &stderr)
	if code != 1 {
		t.Fatalf("code = %d stdout = %s stderr = %s", code, stdout.String(), stderr.String())
	}
	assertJSONField(t, stdout.String(), "code", "invalid_evidence_sections")
	if client.commentCalls != 0 {
		t.Fatalf("Jira comment calls = %d", client.commentCalls)
	}
	if _, err := os.Stat(filepath.Join(root, ".agentic-ops", "runs", runID, "evidence.md")); !os.IsNotExist(err) {
		t.Fatalf("evidence file should not exist, stat err = %v", err)
	}
}

func TestWritePREvidenceRequiresPRURL(t *testing.T) {
	root := t.TempDir()
	t.Setenv("AGENTIC_OPS_WORKSPACE_ROOT", root)
	var stdout bytes.Buffer
	var stderr bytes.Buffer
	code := Run([]string{"write-pr-evidence", "--workspace", "tapstate", "--run-id", "run-1"}, &stdout, &stderr)
	if code != 1 {
		t.Fatalf("code = %d stdout = %s stderr = %s", code, stdout.String(), stderr.String())
	}
	assertJSONField(t, stdout.String(), "operation", "write_pr_evidence")
	assertJSONField(t, stdout.String(), "code", "missing_pr_url")
	assertJSONField(t, stdout.String(), "current_stage", "pr_evidence_gate")
}

func TestWritePREvidenceReadsGitHubFactsAndWritesLocalAudit(t *testing.T) {
	root := t.TempDir()
	t.Setenv("AGENTIC_OPS_WORKSPACE_ROOT", root)
	runID := "TAP-123-takeover-20260721103012-a8f3"
	sourceRoot := t.TempDir()
	initGitRepoForCLITest(t, sourceRoot, "feature/tap-123")
	writeCLITestFile(t, filepath.Join(sourceRoot, "README.md"), "# Demo\n")
	Run([]string{"takeover-task", "TAP-123", "--workspace", "tapstate"}, &bytes.Buffer{}, &bytes.Buffer{})
	var prepareStdout bytes.Buffer
	var prepareStderr bytes.Buffer
	if code := Run([]string{"prepare-pr", "--workspace", "tapstate", "--run-id", runID, "--source-root", sourceRoot, "--base", "main", "--title", "Fix TAP-123"}, &prepareStdout, &prepareStderr); code != 0 {
		t.Fatalf("prepare-pr code = %d stdout = %s stderr = %s", code, prepareStdout.String(), prepareStderr.String())
	}
	assertJSONField(t, prepareStdout.String(), "current_stage", "pr_plan_prepared")
	withGitHubClientForTest(t, github.Client{Runner: &cliFakeGitHubRunner{outputs: map[string]string{
		"api --method GET repos/tapstate/example-repo/pulls/42":                                     `{"html_url":"https://github.com/tapstate/example-repo/pull/42","head":{"sha":"abc123"}}`,
		"api --paginate --slurp repos/tapstate/example-repo/commits/abc123/check-runs?per_page=100": `[{"check_runs":[{"name":"unit","status":"completed","conclusion":"success","details_url":"https://github.example/check/1"}]}]`,
		"api --paginate --slurp repos/tapstate/example-repo/commits/abc123/status?per_page=100":     `[{"statuses":[]}]`,
		"api --paginate --slurp repos/tapstate/example-repo/issues/42/comments?per_page=100":        `[[]]`,
		"api --paginate --slurp repos/tapstate/example-repo/pulls/42/reviews?per_page=100":          `[[{"user":{"login":"reviewer"},"body":"通过","state":"APPROVED","html_url":"https://github.example/review/1"}]]`,
	}}})

	var stdout bytes.Buffer
	var stderr bytes.Buffer
	code := Run([]string{"write-pr-evidence", "--workspace", "tapstate", "--run-id", runID, "--pr-url", "https://github.com/tapstate/example-repo/pull/42"}, &stdout, &stderr)
	if code != 0 {
		t.Fatalf("code = %d stdout = %s stderr = %s", code, stdout.String(), stderr.String())
	}
	assertJSONField(t, stdout.String(), "operation", "write_pr_evidence")
	assertJSONField(t, stdout.String(), "pr_url", "https://github.com/tapstate/example-repo/pull/42")
	assertJSONField(t, stdout.String(), "ci_status", "passed")
	assertJSONField(t, stdout.String(), "head_sha", "abc123")
	assertJSONNumber(t, stdout.String(), "check_count", 1)
	assertJSONNumber(t, stdout.String(), "pending_check_count", 0)
	assertJSONField(t, stdout.String(), "review_status", "approved")
	assertJSONField(t, stdout.String(), "audit_submitted", true)
	assertJSONField(t, stdout.String(), "agentic_next_action", "request_owner_confirmation")

	evidencePath := filepath.Join(root, ".agentic-ops", "runs", runID, "pr-evidence.md")
	evidenceData, err := os.ReadFile(evidencePath)
	if err != nil {
		t.Fatalf("ReadFile PR evidence error = %v", err)
	}
	for _, want := range []string{"PR URL：https://github.com/tapstate/example-repo/pull/42", "PR head SHA：abc123", "CI 状态：passed", "Review 状态：approved", "事实来源：GitHub REST API"} {
		if !strings.Contains(string(evidenceData), want) {
			t.Fatalf("PR evidence missing %q: %s", want, string(evidenceData))
		}
	}
	assertEventLogContains(t, root, `"operation":"write_pr_evidence"`)
	assertEventLogContains(t, root, `"audit_reference":"`+evidencePath+`"`)
}

func TestWritePREvidenceReportsNotConfiguredCIRisk(t *testing.T) {
	root := t.TempDir()
	t.Setenv("AGENTIC_OPS_WORKSPACE_ROOT", root)
	runID := "TAP-123-takeover-20260721103012-a8f3"
	writeCLITestFile(t, filepath.Join(root, ".agentic-ops", "feedback", "events.ndjson"), `{"timestamp":"2026-07-21T10:30:12Z","workspace":"tapstate","agentic_run_id":"TAP-123-takeover-20260721103012-a8f3","issue_key":"TAP-123","operation":"takeover_task","current_stage":"takeover_started","agentic_next_action":"proceed","agent_id":"agentic-cli-local-agent","agentic_id":"agentic-cli-local-agent","agentic_takeover_at":"2026-07-21T10:30:12Z","agentic_heartbeat_at":"2026-07-21T10:30:12Z","task_class":"technical_task","process_id":"development_change_v1","target_repo":"tapstate/example-repo","ok":true}
{"timestamp":"2026-07-21T10:40:12Z","workspace":"tapstate","agentic_run_id":"TAP-123-takeover-20260721103012-a8f3","issue_key":"TAP-123","operation":"prepare_pr","current_stage":"pr_plan_prepared","agentic_next_action":"ask_owner_to_push_and_create_pr","agent_id":"agentic-cli-local-agent","agentic_id":"agentic-cli-local-agent","task_class":"technical_task","process_id":"development_change_v1","target_repo":"tapstate/example-repo","ok":true}
`)
	withGitHubClientForTest(t, github.Client{Runner: &cliFakeGitHubRunner{outputs: map[string]string{
		"api --method GET repos/tapstate/example-repo/pulls/42":                                     `{"html_url":"https://github.com/tapstate/example-repo/pull/42","head":{"sha":"abc123"}}`,
		"api --paginate --slurp repos/tapstate/example-repo/commits/abc123/check-runs?per_page=100": `[{"check_runs":[]}]`,
		"api --paginate --slurp repos/tapstate/example-repo/commits/abc123/status?per_page=100":     `[{"statuses":[]}]`,
		"api --paginate --slurp repos/tapstate/example-repo/issues/42/comments?per_page=100":        `[[]]`,
		"api --paginate --slurp repos/tapstate/example-repo/pulls/42/reviews?per_page=100":          `[[]]`,
	}}})

	var stdout bytes.Buffer
	var stderr bytes.Buffer
	code := Run([]string{"write-pr-evidence", "--workspace", "tapstate", "--run-id", runID, "--pr-url", "https://github.com/tapstate/example-repo/pull/42"}, &stdout, &stderr)
	if code != 0 {
		t.Fatalf("code = %d stdout = %s stderr = %s", code, stdout.String(), stderr.String())
	}
	assertJSONField(t, stdout.String(), "ci_status", "not_configured")
	assertJSONNumber(t, stdout.String(), "check_count", 0)
	evidenceData, err := os.ReadFile(filepath.Join(root, ".agentic-ops", "runs", runID, "pr-evidence.md"))
	if err != nil {
		t.Fatalf("ReadFile error = %v", err)
	}
	if !strings.Contains(string(evidenceData), "风险：GitHub 未配置 CI 检查，不能视为 CI 已通过；是否继续由项目策略和研发工程师决定。") {
		t.Fatalf("evidence = %s", string(evidenceData))
	}
}

func TestWritePREvidencePreservesCIReadFailureCode(t *testing.T) {
	root := t.TempDir()
	t.Setenv("AGENTIC_OPS_WORKSPACE_ROOT", root)
	runID := "TAP-123-takeover-20260721103012-a8f3"
	writeCLITestFile(t, filepath.Join(root, ".agentic-ops", "feedback", "events.ndjson"), `{"timestamp":"2026-07-21T10:30:12Z","workspace":"tapstate","agentic_run_id":"TAP-123-takeover-20260721103012-a8f3","issue_key":"TAP-123","operation":"takeover_task","current_stage":"takeover_started","agentic_next_action":"proceed","agent_id":"agentic-cli-local-agent","agentic_id":"agentic-cli-local-agent","agentic_takeover_at":"2026-07-21T10:30:12Z","agentic_heartbeat_at":"2026-07-21T10:30:12Z","task_class":"technical_task","process_id":"development_change_v1","target_repo":"tapstate/example-repo","ok":true}
{"timestamp":"2026-07-21T10:40:12Z","workspace":"tapstate","agentic_run_id":"TAP-123-takeover-20260721103012-a8f3","issue_key":"TAP-123","operation":"prepare_pr","current_stage":"pr_plan_prepared","agentic_next_action":"ask_owner_to_push_and_create_pr","target_repo":"tapstate/example-repo","ok":true}
`)
	withGitHubClientForTest(t, github.Client{Runner: &cliFakeGitHubRunner{
		outputs: map[string]string{
			"api --method GET repos/tapstate/example-repo/pulls/42": `{"html_url":"https://github.com/tapstate/example-repo/pull/42","head":{"sha":"abc123"}}`,
		},
		errors: map[string]error{
			"api --paginate --slurp repos/tapstate/example-repo/commits/abc123/check-runs?per_page=100": errors.New("check-runs unavailable"),
		},
	}})

	var stdout bytes.Buffer
	code := Run([]string{"write-pr-evidence", "--workspace", "tapstate", "--run-id", runID, "--pr-url", "https://github.com/tapstate/example-repo/pull/42"}, &stdout, &bytes.Buffer{})
	if code != 1 {
		t.Fatalf("code = %d stdout = %s", code, stdout.String())
	}
	assertJSONField(t, stdout.String(), "code", "github_ci_read_failed")
	assertEventLogContains(t, root, `"code":"github_ci_read_failed"`)
}

func TestWritePREvidenceRejectsStageBeforePRPlan(t *testing.T) {
	root := t.TempDir()
	t.Setenv("AGENTIC_OPS_WORKSPACE_ROOT", root)
	runID := "TAP-123-takeover-20260721103012-a8f3"
	writeCLITestFile(t, filepath.Join(root, ".agentic-ops", "feedback", "events.ndjson"), `{"timestamp":"2026-07-21T10:30:12Z","workspace":"tapstate","agentic_run_id":"TAP-123-takeover-20260721103012-a8f3","issue_key":"TAP-123","operation":"takeover_task","current_stage":"takeover_started","agentic_next_action":"proceed","agent_id":"agentic-cli-local-agent","agentic_id":"agentic-cli-local-agent","agentic_takeover_at":"2026-07-21T10:30:12Z","agentic_heartbeat_at":"2026-07-21T10:30:12Z","task_class":"technical_task","process_id":"development_change_v1","target_repo":"tapstate/example-repo","ok":true}
`)

	var stdout bytes.Buffer
	var stderr bytes.Buffer
	code := Run([]string{"write-pr-evidence", "--workspace", "tapstate", "--run-id", runID, "--pr-url", "https://github.com/tapstate/example-repo/pull/42"}, &stdout, &stderr)
	if code != 1 {
		t.Fatalf("code = %d stdout = %s stderr = %s", code, stdout.String(), stderr.String())
	}
	assertJSONField(t, stdout.String(), "code", "operation_stage_not_allowed")
}

func TestWriteEvidenceOutputsNextAction(t *testing.T) {
	root := t.TempDir()
	t.Setenv("AGENTIC_OPS_WORKSPACE_ROOT", root)
	runID := "TAP-123-takeover-20260721103012-a8f3"
	writeCLITestFile(t, filepath.Join(root, ".agentic-ops", "feedback", "events.ndjson"), `{"timestamp":"2026-07-21T10:30:12Z","workspace":"tapstate","agentic_run_id":"TAP-123-takeover-20260721103012-a8f3","issue_key":"TAP-123","operation":"takeover_task","task_type":"task_takeover","current_stage":"takeover_started","agentic_next_action":"proceed","agent_id":"agentic-cli-local-agent","agentic_id":"agentic-cli-local-agent","agentic_takeover_at":"2026-07-21T10:30:12Z","agentic_heartbeat_at":"2026-07-21T10:30:12Z","task_class":"technical_task","process_id":"development_change_v1","target_repo":"tapstate/example-repo","ok":true,"gate":"takeover_task","gate_status":"passed"}
`)
	var stdout bytes.Buffer
	var stderr bytes.Buffer
	code := Run([]string{"write-evidence", "--workspace", "tapstate", "--run-id", runID, "--content-file", writeCompletionBodyFile(t, root)}, &stdout, &stderr)
	if code != 0 {
		t.Fatalf("code = %d", code)
	}
	assertJSONField(t, stdout.String(), "operation", "write_evidence")
	assertJSONField(t, stdout.String(), "issue_key", "TAP-123")
	assertJSONField(t, stdout.String(), "task_class", "technical_task")
	assertJSONField(t, stdout.String(), "process_id", "development_change_v1")
	assertJSONField(t, stdout.String(), "target_repo", "tapstate/example-repo")
	assertJSONField(t, stdout.String(), "audit_submitted", true)
	assertJSONField(t, stdout.String(), "agentic_next_action", "request_owner_confirmation")
	evidencePath := filepath.Join(root, ".agentic-ops", "runs", runID, "evidence.md")
	evidenceData, err := os.ReadFile(evidencePath)
	if err != nil {
		t.Fatalf("ReadFile evidence error = %v", err)
	}
	for _, want := range []string{"Jira 卡片：TAP-123", "任务分类：technical_task", "标准流程：development_change_v1", "目标仓库：tapstate/example-repo", "## 事实来源"} {
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

func TestWriteEvidenceSkipsIncompleteHistoricalTakeoverEvent(t *testing.T) {
	root := t.TempDir()
	t.Setenv("AGENTIC_OPS_WORKSPACE_ROOT", root)
	runID := "TAP-123-takeover-20260721103012-a8f3"
	writeCLITestFile(t, filepath.Join(root, ".agentic-ops", "feedback", "events.ndjson"), `{"timestamp":"2026-07-21T10:30:12Z","workspace":"tapstate","agentic_run_id":"TAP-123-takeover-20260721103012-a8f3","issue_key":"TAP-123","operation":"takeover_task","task_type":"task_takeover","current_stage":"takeover_gate","agentic_next_action":"ask_owner","agent_id":"agentic-cli-local-agent","agentic_id":"agentic-cli-local-agent","ok":true,"gate":"real_jira_write","gate_status":"passed"}
{"timestamp":"2026-07-21T10:30:13Z","workspace":"tapstate","agentic_run_id":"TAP-123-takeover-20260721103012-a8f3","issue_key":"TAP-123","operation":"takeover_task","task_type":"task_takeover","current_stage":"takeover_started","agentic_next_action":"proceed","agent_id":"agentic-cli-local-agent","agentic_id":"agentic-cli-local-agent","agentic_takeover_at":"2026-07-21T10:30:12Z","agentic_heartbeat_at":"2026-07-21T10:30:12Z","task_class":"technical_task","process_id":"development_change_v1","target_repo":"tapstate/example-repo","ok":true,"gate":"takeover_task","gate_status":"passed"}
`)

	var stdout bytes.Buffer
	var stderr bytes.Buffer
	code := Run([]string{"write-evidence", "--workspace", "tapstate", "--run-id", runID, "--content-file", writeCompletionBodyFile(t, root)}, &stdout, &stderr)
	if code != 0 {
		t.Fatalf("code = %d stdout = %s stderr = %s", code, stdout.String(), stderr.String())
	}
	assertJSONField(t, stdout.String(), "issue_key", "TAP-123")
	assertJSONField(t, stdout.String(), "target_repo", "tapstate/example-repo")
}

func TestWriteEvidencePreservesTargetRepoAfterResume(t *testing.T) {
	root := t.TempDir()
	t.Setenv("AGENTIC_OPS_WORKSPACE_ROOT", root)
	runID := "TAP-123-takeover-20260721103012-a8f3"
	writeCLITestFile(t, filepath.Join(root, ".agentic-ops", "feedback", "events.ndjson"), `{"timestamp":"2026-07-21T10:30:12Z","workspace":"tapstate","agentic_run_id":"TAP-123-takeover-20260721103012-a8f3","issue_key":"TAP-123","operation":"takeover_task","task_type":"task_takeover","current_stage":"takeover_started","agentic_next_action":"proceed","agent_id":"agentic-cli-local-agent","agentic_id":"agentic-cli-local-agent","agentic_takeover_at":"2026-07-21T10:30:12Z","agentic_heartbeat_at":"2026-07-21T10:30:12Z","task_class":"technical_task","process_id":"development_change_v1","target_repo":"tapstate/example-repo","ok":true,"gate":"takeover_task","gate_status":"passed"}
{"timestamp":"2026-07-21T10:31:00Z","workspace":"tapstate","agentic_run_id":"TAP-123-takeover-20260721103012-a8f3","issue_key":"TAP-123","operation":"resume_takeover","task_type":"task_takeover","current_stage":"takeover_resumed","agentic_next_action":"continue_development","agent_id":"agentic-cli-local-agent","agentic_id":"agentic-cli-local-agent","task_class":"technical_task","process_id":"development_change_v1","ok":true,"gate":"resume_takeover","gate_status":"passed"}
`)
	var stdout bytes.Buffer
	var stderr bytes.Buffer
	code := Run([]string{"write-evidence", "--workspace", "tapstate", "--run-id", runID, "--content-file", writeCompletionBodyFile(t, root)}, &stdout, &stderr)
	if code != 0 {
		t.Fatalf("code = %d stdout = %s stderr = %s", code, stdout.String(), stderr.String())
	}
	assertJSONField(t, stdout.String(), "target_repo", "tapstate/example-repo")
	evidenceData, err := os.ReadFile(filepath.Join(root, ".agentic-ops", "runs", runID, "evidence.md"))
	if err != nil {
		t.Fatalf("ReadFile evidence error = %v", err)
	}
	if !strings.Contains(string(evidenceData), "目标仓库：tapstate/example-repo") {
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
	writeCLITestFile(t, filepath.Join(root, ".agentic-ops", "feedback", "events.ndjson"), `{"timestamp":"2026-07-21T10:30:12Z","workspace":"tapstate","agentic_run_id":"TAP-123-takeover-20260721103012-a8f3","issue_key":"TAP-123","operation":"takeover_task","task_type":"task_takeover","current_stage":"takeover_started","agentic_next_action":"proceed","agent_id":"agentic-cli-local-agent","agentic_id":"agentic-cli-local-agent","agentic_takeover_at":"2026-07-21T10:30:12Z","agentic_heartbeat_at":"2026-07-21T10:30:12Z","task_class":"technical_task","process_id":"development_change_v1","target_repo":"tapstate/example-repo","ok":true,"gate":"takeover_task","gate_status":"passed"}
`)
	var stdout bytes.Buffer
	var stderr bytes.Buffer
	code := Run([]string{"write-evidence", "--workspace", "tapstate", "--run-id", runID, "--content-file", writeCompletionBodyFile(t, root)}, &stdout, &stderr)
	if code != 1 {
		t.Fatalf("code = %d stdout = %s stderr = %s", code, stdout.String(), stderr.String())
	}
	assertJSONField(t, stdout.String(), "operation", "write_evidence")
	assertJSONField(t, stdout.String(), "code", "policy_gate_required")
	assertJSONField(t, stdout.String(), "current_stage", "evidence_write_gate")
	assertJSONField(t, stdout.String(), "agentic_next_action", "ask_owner")
	if _, err := os.Stat(filepath.Join(root, ".agentic-ops", "runs", runID, "evidence.md")); !os.IsNotExist(err) {
		t.Fatalf("evidence file should not be written when policy gate blocks, stat err = %v", err)
	}
}

func TestWriteEvidenceRejectsMissingRun(t *testing.T) {
	root := t.TempDir()
	t.Setenv("AGENTIC_OPS_WORKSPACE_ROOT", root)
	var stdout bytes.Buffer
	var stderr bytes.Buffer
	code := Run([]string{"write-evidence", "--workspace", "tapstate", "--run-id", "missing-run", "--content-file", writeCompletionBodyFile(t, root)}, &stdout, &stderr)
	if code != 1 {
		t.Fatalf("code = %d stdout = %s stderr = %s", code, stdout.String(), stderr.String())
	}
	assertJSONField(t, stdout.String(), "operation", "write_evidence")
	assertJSONField(t, stdout.String(), "code", "run_not_found")
	assertJSONField(t, stdout.String(), "current_stage", "evidence_write_gate")
	assertJSONField(t, stdout.String(), "agentic_next_action", "ask_owner")
}

func TestWriteEvidenceRejectsCompletedRun(t *testing.T) {
	root := t.TempDir()
	t.Setenv("AGENTIC_OPS_WORKSPACE_ROOT", root)
	runID := "TAP-123-takeover-20260721103012-a8f3"
	writeCLITestFile(t, filepath.Join(root, ".agentic-ops", "feedback", "events.ndjson"), `{"timestamp":"2026-07-21T10:30:12Z","workspace":"tapstate","agentic_run_id":"TAP-123-takeover-20260721103012-a8f3","issue_key":"TAP-123","operation":"takeover_task","task_type":"task_takeover","current_stage":"takeover_started","agentic_next_action":"proceed","agent_id":"agentic-cli-local-agent","agentic_id":"agentic-cli-local-agent","agentic_takeover_at":"2026-07-21T10:30:12Z","agentic_heartbeat_at":"2026-07-21T10:30:12Z","task_class":"technical_task","process_id":"development_change_v1","target_repo":"tapstate/example-repo","ok":true,"gate":"takeover_task","gate_status":"passed"}
{"timestamp":"2026-07-21T10:31:00Z","workspace":"tapstate","agentic_run_id":"TAP-123-takeover-20260721103012-a8f3","issue_key":"TAP-123","operation":"release_agent","task_type":"task_release","current_stage":"completed","agentic_next_action":"task_audit_submitted","agent_id":"agentic-cli-local-agent","agentic_id":"agentic-cli-local-agent","task_class":"technical_task","process_id":"development_change_v1","target_repo":"tapstate/example-repo","ok":true,"gate":"release_agent","gate_status":"passed"}
`)

	var stdout bytes.Buffer
	var stderr bytes.Buffer
	code := Run([]string{"write-evidence", "--workspace", "tapstate", "--run-id", runID, "--content-file", writeCompletionBodyFile(t, root)}, &stdout, &stderr)
	if code != 1 {
		t.Fatalf("code = %d stdout = %s stderr = %s", code, stdout.String(), stderr.String())
	}
	assertJSONField(t, stdout.String(), "operation", "write_evidence")
	assertJSONField(t, stdout.String(), "code", "local_state_mismatch")
	if _, err := os.Stat(filepath.Join(root, ".agentic-ops", "runs", runID, "evidence.md")); !os.IsNotExist(err) {
		t.Fatalf("evidence file should not be written for a completed run, stat err = %v", err)
	}
}

func TestWriteEvidenceRequiresConfirmationForRealJiraComment(t *testing.T) {
	root := t.TempDir()
	t.Setenv("AGENTIC_OPS_WORKSPACE_ROOT", root)
	runID := "TAP-123-takeover-20260721103012-a8f3"
	writeCLITestFile(t, filepath.Join(root, ".agentic-ops", "feedback", "events.ndjson"), `{"timestamp":"2026-07-21T10:30:12Z","workspace":"tapstate","agentic_run_id":"TAP-123-takeover-20260721103012-a8f3","issue_key":"TAP-123","operation":"takeover_task","task_type":"task_takeover","current_stage":"takeover_started","agentic_next_action":"proceed","agent_id":"agentic-cli-local-agent","agentic_id":"agentic-cli-local-agent","agentic_takeover_at":"2026-07-21T10:30:12Z","agentic_heartbeat_at":"2026-07-21T10:30:12Z","task_class":"technical_task","process_id":"development_change_v1","target_repo":"tapstate/example-repo","ok":true,"gate":"takeover_task","gate_status":"passed"}
`)
	withJiraClientForTest(t, clihandlers.JiraClientSelection{Client: &recordingJiraClient{issue: realModeBoundIssue()}, Mode: "real"})

	var stdout bytes.Buffer
	var stderr bytes.Buffer
	code := Run([]string{"write-evidence", "--workspace", "tapstate", "--run-id", runID, "--content-file", writeCompletionBodyFile(t, root)}, &stdout, &stderr)
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
	writeCLITestFile(t, filepath.Join(root, ".agentic-ops", "feedback", "events.ndjson"), `{"timestamp":"2026-07-21T10:30:12Z","workspace":"tapstate","agentic_run_id":"TAP-123-takeover-20260721103012-a8f3","issue_key":"TAP-123","operation":"takeover_task","task_type":"task_takeover","current_stage":"takeover_started","agentic_next_action":"proceed","agent_id":"agentic-cli-local-agent","agentic_id":"agentic-cli-local-agent","agentic_takeover_at":"2026-07-21T10:30:12Z","agentic_heartbeat_at":"2026-07-21T10:30:12Z","task_class":"technical_task","process_id":"development_change_v1","target_repo":"tapstate/example-repo","ok":true,"gate":"takeover_task","gate_status":"passed"}
`)
	client := &recordingJiraClient{issue: realModeBoundIssue()}
	withJiraClientForTest(t, clihandlers.JiraClientSelection{Client: client, Mode: "real"})

	var stdout bytes.Buffer
	var stderr bytes.Buffer
	code := Run([]string{"write-evidence", "--workspace", "tapstate", "--run-id", runID, "--content-file", writeCompletionBodyFile(t, root), "--confirm-real-jira-write"}, &stdout, &stderr)
	if code != 0 {
		t.Fatalf("code = %d stdout = %s stderr = %s", code, stdout.String(), stderr.String())
	}
	if client.commentKey != "TAP-123" {
		t.Fatalf("commentKey = %s", client.commentKey)
	}
	if !strings.Contains(client.commentBody, "证据状态：已写入") || !strings.Contains(client.commentBody, "## 事实来源") {
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
	assertJSONField(t, stdout.String(), "agentic_next_action", "ask_owner")
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
	if client.transitionKey != "TAP-123" || client.transitionRequest.ID != "31" {
		t.Fatalf("transition = %s %+v", client.transitionKey, client.transitionRequest)
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
	if client.transitionKey != "TAP-123" || client.transitionRequest.ID != "31" {
		t.Fatalf("transition = %s %+v", client.transitionKey, client.transitionRequest)
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
				"agentic_id":                  {JiraField: "customfield_agentic_id"},
				"agentic_run_id":              {JiraField: "customfield_agentic_run_id"},
				"agentic_takeover_at":         {JiraField: "customfield_agentic_takeover_at"},
				"agentic_next_action":         {JiraField: "customfield_agentic_next_action"},
				"agentic_completion_evidence": {JiraField: "customfield_agentic_completion_evidence"},
				"agentic_heartbeat_at":        {JiraField: "customfield_agentic_heartbeat_at"},
			},
		},
	}

	takeoverFields := clihandlers.JiraTakeoverFields(p, "run-1", "agent-1", "2026-07-21T10:30:12Z", "proceed")
	if takeoverFields["customfield_agentic_id"] != "agent-1" {
		t.Fatalf("takeoverFields = %#v", takeoverFields)
	}
	if takeoverFields["customfield_agentic_run_id"] != "run-1" ||
		takeoverFields["customfield_agentic_takeover_at"] != "2026-07-21T10:30:12Z" ||
		takeoverFields["customfield_agentic_next_action"] != "proceed" ||
		takeoverFields["customfield_agentic_heartbeat_at"] != "2026-07-21T10:30:12Z" {
		t.Fatalf("takeoverFields = %#v", takeoverFields)
	}
	if value, ok := takeoverFields["customfield_agentic_completion_evidence"]; !ok || value != nil {
		t.Fatalf("takeoverFields must clear completion evidence: %#v", takeoverFields)
	}
	releaseFields := clihandlers.JiraReleaseFields(p)
	if _, ok := releaseFields["customfield_agentic_id"]; !ok {
		t.Fatalf("releaseFields missing current agent field: %#v", releaseFields)
	}
	if releaseFields["customfield_agentic_id"] != nil {
		t.Fatalf("release current agent field = %#v", releaseFields["customfield_agentic_id"])
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
	assertJSONField(t, stdout.String(), "agentic_run_id", runID)
	assertJSONField(t, stdout.String(), "current_stage", "completed")
	assertJSONField(t, stdout.String(), "agentic_id_cleared", true)
	assertJSONField(t, stdout.String(), "audit_submitted", true)
	assertJSONField(t, stdout.String(), "audit_target", "local_file")
	assertJSONField(t, stdout.String(), "agentic_next_action", "task_audit_submitted")

	events, err := os.ReadFile(filepath.Join(root, ".agentic-ops", "feedback", "events.ndjson"))
	if err != nil {
		t.Fatalf("ReadFile events error = %v", err)
	}
	for _, want := range []string{
		`"operation":"release_agent"`,
		`"agentic_id":"agentic-cli-local-agent"`,
		`"agentic_id_cleared":true`,
		`"completed_at":"2026-07-21T10:30:12Z"`,
		`"agentic_completion_evidence":"evidence.md"`,
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
	assertJSONField(t, stdout.String(), "code", "agentic_completion_evidence_missing")
	assertJSONField(t, stdout.String(), "current_stage", "completion_cleanup")
	assertJSONField(t, stdout.String(), "agentic_next_action", "ask_owner")
}

func writeCompletionBodyFile(t *testing.T, root string) string {
	t.Helper()
	path := filepath.Join(root, "completion-body.md")
	writeCLITestFile(t, path, `## 变更内容

修复接管原子性和证据链。

## 验证命令与结果

go test ./...：通过。

## 风险

未发现额外风险。

## 恢复说明

无需恢复。

## 事实来源

Jira AO、Git 和 GitHub PR 回读。
`)
	return path
}
