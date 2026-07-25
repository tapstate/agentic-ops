package cli

import (
	"bytes"
	"context"
	gitops "github.com/tapstate/agentic-ops/packages/agentic-cli/internal/git"
	"github.com/tapstate/agentic-ops/packages/agentic-cli/internal/github"
	"path/filepath"
	"strings"
	"testing"
)

func TestInspectWorkspaceOutputsSafeGitSummary(t *testing.T) {
	sourceRoot := t.TempDir()
	initGitRepoForCLITest(t, sourceRoot, "feature/tap-123")
	writeCLITestFile(t, filepath.Join(sourceRoot, "README.md"), "# Demo\n\nchanged\n")
	writeCLITestFile(t, filepath.Join(sourceRoot, "new.txt"), "new\n")

	var stdout bytes.Buffer
	var stderr bytes.Buffer
	code := Run([]string{"inspect-workspace", "--workspace", "tapstate", "--source-root", sourceRoot}, &stdout, &stderr)
	if code != 0 {
		t.Fatalf("code = %d stdout = %s stderr = %s", code, stdout.String(), stderr.String())
	}
	assertJSONField(t, stdout.String(), "operation", "inspect_workspace")
	assertJSONField(t, stdout.String(), "workspace", "tapstate")
	assertJSONField(t, stdout.String(), "source_root", sourceRoot)
	assertJSONField(t, stdout.String(), "branch", "feature/tap-123")
	assertJSONField(t, stdout.String(), "dirty", true)
	assertJSONField(t, stdout.String(), "current_stage", "workspace_inspected")
	assertJSONField(t, stdout.String(), "next_action", "prepare_pr")
	if !strings.Contains(stdout.String(), `"README.md"`) || !strings.Contains(stdout.String(), `"new.txt"`) {
		t.Fatalf("stdout missing changed files: %s", stdout.String())
	}
}

func TestInspectWorkspaceCanUseFakeGitInspector(t *testing.T) {
	withGitInspectorForTest(t, func(ctx context.Context, root string) (gitops.WorkspaceStatus, error) {
		return gitops.WorkspaceStatus{
			Root:         root,
			Branch:       "feature/fake",
			Commit:       "abc123",
			Dirty:        true,
			ChangedFiles: []string{"fake.go"},
		}, nil
	})

	var stdout bytes.Buffer
	var stderr bytes.Buffer
	code := Run([]string{"inspect-workspace", "--workspace", "tapstate", "--source-root", "/tmp/source"}, &stdout, &stderr)
	if code != 0 {
		t.Fatalf("code = %d stdout = %s stderr = %s", code, stdout.String(), stderr.String())
	}
	assertJSONField(t, stdout.String(), "operation", "inspect_workspace")
	assertJSONField(t, stdout.String(), "branch", "feature/fake")
	assertJSONField(t, stdout.String(), "commit", "abc123")
	assertJSONField(t, stdout.String(), "dirty", true)
	if !strings.Contains(stdout.String(), `"fake.go"`) {
		t.Fatalf("stdout missing fake changed file: %s", stdout.String())
	}
}

func TestPreparePROutputsPlanAndHumanGateForCreation(t *testing.T) {
	root := t.TempDir()
	t.Setenv("AGENTIC_OPS_WORKSPACE_ROOT", root)
	sourceRoot := t.TempDir()
	initGitRepoForCLITest(t, sourceRoot, "feature/tap-123")
	writeCLITestFile(t, filepath.Join(sourceRoot, "README.md"), "# Demo\n\nchanged\n")
	Run([]string{"takeover-task", "TAP-123", "--workspace", "tapstate"}, &bytes.Buffer{}, &bytes.Buffer{})
	runID := "TAP-123-takeover-20260721103012-a8f3"

	var stdout bytes.Buffer
	var stderr bytes.Buffer
	code := Run([]string{"prepare-pr", "--workspace", "tapstate", "--run-id", runID, "--source-root", sourceRoot, "--base", "main", "--title", "Fix TAP-123"}, &stdout, &stderr)
	if code != 0 {
		t.Fatalf("code = %d stdout = %s stderr = %s", code, stdout.String(), stderr.String())
	}
	assertJSONField(t, stdout.String(), "operation", "prepare_pr")
	assertJSONField(t, stdout.String(), "run_id", runID)
	assertJSONField(t, stdout.String(), "issue_key", "TAP-123")
	assertJSONField(t, stdout.String(), "branch", "feature/tap-123")
	assertJSONField(t, stdout.String(), "base", "main")
	assertJSONField(t, stdout.String(), "title", "Fix TAP-123")
	assertJSONField(t, stdout.String(), "policy_gate_code", "policy_gate_required")
	assertJSONField(t, stdout.String(), "create_pr_gate_required", true)
	assertJSONField(t, stdout.String(), "git_push_gate_required", true)
	assertJSONField(t, stdout.String(), "current_stage", "pr_plan_prepared")
	assertJSONField(t, stdout.String(), "next_action", "ask_owner_to_push_and_create_pr")
}

func TestReadPRCommentsUsesGitHubReader(t *testing.T) {
	fake := &cliFakeGitHubRunner{outputs: map[string]string{
		"pr view 42 --repo tapdata/tapdata --json comments,reviews": `{"comments":[{"author":{"login":"reviewer"},"body":"请补测试","url":"https://github.example/comment/1"}],"reviews":[]}`,
	}}
	withGitHubClientForTest(t, github.Client{Runner: fake})

	var stdout bytes.Buffer
	var stderr bytes.Buffer
	code := Run([]string{"read-pr-comments", "--workspace", "tapstate", "--repo", "tapdata/tapdata", "--pr", "42"}, &stdout, &stderr)
	if code != 0 {
		t.Fatalf("code = %d stdout = %s stderr = %s", code, stdout.String(), stderr.String())
	}
	assertJSONField(t, stdout.String(), "operation", "read_pr_comments")
	assertJSONField(t, stdout.String(), "repo", "tapdata/tapdata")
	assertJSONField(t, stdout.String(), "pr", "42")
	assertJSONNumber(t, stdout.String(), "comments_count", 1)
	assertJSONField(t, stdout.String(), "next_action", "classify_or_fix_pr_comments")
	if !strings.Contains(stdout.String(), "请补测试") {
		t.Fatalf("stdout missing comment body: %s", stdout.String())
	}
}

func TestCheckCIStatusUsesGitHubReader(t *testing.T) {
	fake := &cliFakeGitHubRunner{outputs: map[string]string{
		"pr checks 42 --repo tapdata/tapdata --json name,state,conclusion,detailsUrl": `[{"name":"unit","state":"COMPLETED","conclusion":"SUCCESS","detailsUrl":"https://github.example/checks/1"},{"name":"e2e","state":"COMPLETED","conclusion":"FAILURE","detailsUrl":"https://github.example/checks/2"}]`,
	}}
	withGitHubClientForTest(t, github.Client{Runner: fake})

	var stdout bytes.Buffer
	var stderr bytes.Buffer
	code := Run([]string{"check-ci-status", "--workspace", "tapstate", "--repo", "tapdata/tapdata", "--pr", "42"}, &stdout, &stderr)
	if code != 0 {
		t.Fatalf("code = %d stdout = %s stderr = %s", code, stdout.String(), stderr.String())
	}
	assertJSONField(t, stdout.String(), "operation", "check_ci_status")
	assertJSONField(t, stdout.String(), "status", "failed")
	assertJSONNumber(t, stdout.String(), "failing_checks_count", 1)
	assertJSONField(t, stdout.String(), "next_action", "fix_ci_failures")
	if !strings.Contains(stdout.String(), `"e2e"`) {
		t.Fatalf("stdout missing failing check: %s", stdout.String())
	}
}

func TestFixPRCommentsOutputsHumanGatedFixPlan(t *testing.T) {
	fake := &cliFakeGitHubRunner{outputs: map[string]string{
		"pr view 42 --repo tapdata/tapdata --json comments,reviews": `{"comments":[{"author":{"login":"reviewer"},"body":"请补测试","url":"https://github.example/comment/1"},{"author":{"login":"reviewer"},"body":"文档也要更新","url":"https://github.example/comment/2"}],"reviews":[]}`,
	}}
	withGitHubClientForTest(t, github.Client{Runner: fake})

	var stdout bytes.Buffer
	var stderr bytes.Buffer
	code := Run([]string{"fix-pr-comments", "--workspace", "tapstate", "--repo", "tapdata/tapdata", "--pr", "42"}, &stdout, &stderr)
	if code != 1 {
		t.Fatalf("code = %d stdout = %s stderr = %s", code, stdout.String(), stderr.String())
	}
	assertJSONField(t, stdout.String(), "operation", "fix_pr_comments")
	assertJSONField(t, stdout.String(), "code", "policy_gate_required")
	assertJSONField(t, stdout.String(), "comments_count", float64(2))
	assertJSONField(t, stdout.String(), "current_stage", "pr_comment_fix_gate")
	assertJSONField(t, stdout.String(), "next_action", "ask_owner")
	if !strings.Contains(stdout.String(), `"test"`) || !strings.Contains(stdout.String(), `"docs"`) {
		t.Fatalf("stdout missing fix categories: %s", stdout.String())
	}
}
