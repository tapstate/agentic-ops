package cli

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"github.com/tapstate/agentic-ops/packages/agentic-cli/internal/clihandlers"
	gitops "github.com/tapstate/agentic-ops/packages/agentic-cli/internal/git"
	"github.com/tapstate/agentic-ops/packages/agentic-cli/internal/github"
	"github.com/tapstate/agentic-ops/packages/agentic-cli/internal/jira"
	"github.com/tapstate/agentic-ops/packages/agentic-cli/internal/profile"
	"io"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

type staticClock struct {
	now time.Time
}

func (clock staticClock) Now() time.Time {
	return clock.now
}

func TestMain(m *testing.M) {
	restoreClock := clihandlers.SetClockForTest(staticClock{now: time.Date(2026, 7, 21, 10, 30, 12, 0, time.UTC)})
	restore := clihandlers.SetRunGitCloneForTest(func(repoURL string, targetPath string, _ io.Writer) error {
		if strings.TrimSpace(repoURL) == "" {
			return fmt.Errorf("repo URL is required")
		}
		if strings.TrimSpace(targetPath) == "" {
			return fmt.Errorf("target path is required")
		}
		if err := os.MkdirAll(filepath.Join(targetPath, ".git"), 0o755); err != nil {
			return err
		}
		return os.WriteFile(filepath.Join(targetPath, ".git", "HEAD"), []byte("ref: refs/heads/main\n"), 0o644)
	})
	code := m.Run()
	restore()
	restoreClock()
	os.Exit(code)
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

func writeCompleteWorkspaceState(t *testing.T, root string, workspaceName string, sourceRoot string) {
	t.Helper()
	if _, err := os.Stat(sourceRoot); errors.Is(err, os.ErrNotExist) {
		writeCLITestFile(t, filepath.Join(sourceRoot, ".git", "HEAD"), "ref: refs/heads/main\n")
	} else if err != nil {
		t.Fatalf("Stat source root error = %v", err)
	}
	writeCLITestFile(t, filepath.Join(root, ".agentic-ops", "profile.local.yaml"),
		"workspace: "+workspaceName+"\n"+
			"local:\n"+
			"  workspace_root: "+root+"\n"+
			"  source_root: "+sourceRoot+"\n"+
			"  runs_dir: "+filepath.Join(root, ".agentic-ops", "runs")+"\n"+
			"  run_logs_dir: "+filepath.Join(root, ".agentic-ops", "run-logs")+"\n"+
			"  feedback_dir: "+filepath.Join(root, ".agentic-ops", "feedback")+"\n")
	writeCLITestFile(t, filepath.Join(root, ".agentic-ops", "agent.json"),
		`{"workspace":"`+workspaceName+`","project":"`+workspaceName+`"}`)
	writeCLITestFile(t, filepath.Join(root, "AGENTS.md"),
		"<!-- BEGIN AGENTICOPS MANAGED BLOCK -->\n"+
			"# AgenticOps workspace instructions\n"+
			"<!-- END AGENTICOPS MANAGED BLOCK -->\n")
	for _, dir := range []string{"runs", "run-logs", "feedback"} {
		if err := os.MkdirAll(filepath.Join(root, ".agentic-ops", dir), 0o755); err != nil {
			t.Fatalf("MkdirAll %s error = %v", dir, err)
		}
	}
}

func initGitRepoForCLITest(t *testing.T, dir string, branch string) {
	t.Helper()
	runGitForCLITest(t, dir, "init", "-b", branch)
	runGitForCLITest(t, dir, "config", "user.email", "agent@example.com")
	runGitForCLITest(t, dir, "config", "user.name", "Agentic Ops")
	writeCLITestFile(t, filepath.Join(dir, "README.md"), "# Demo\n")
	runGitForCLITest(t, dir, "add", "README.md")
	runGitForCLITest(t, dir, "commit", "-m", "initial")
}

func runGitForCLITest(t *testing.T, dir string, args ...string) {
	t.Helper()
	cmd := exec.Command("git", append([]string{"-C", dir}, args...)...)
	output, err := cmd.CombinedOutput()
	if err != nil {
		t.Fatalf("git %v failed: %v\n%s", args, err, string(output))
	}
}

func validCLIProfileYAML(workspace string, jiraProject string) string {
	return "workspace: " + workspace + "\n" +
		"jira:\n" +
		"  user: dev@example.com\n" +
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
		"jira_transition_mapping:\n" +
		"  start_progress:\n" +
		"    name: Start Progress\n" +
		"github:\n" +
		"  repositories:\n" +
		"    default: tapstate/example-repo\n" +
		"local:\n" +
		"  source_root: /tmp/source\n" +
		"  run_logs_dir: /tmp/.agentic-ops/run-logs\n"
}

func validCLIPolicyYAML(policyName string, requireJiraComment bool) string {
	return validCLIPolicyYAMLWithEvidenceGate(policyName, requireJiraComment, false)
}

func validCLIProcessYAML(processID string) string {
	return "process_id: " + processID + "\n" +
		"task_classes:\n" +
		"  - technical_task\n" +
		"entry_stage: waiting_takeover\n" +
		"stages:\n" +
		"  - id: waiting_takeover\n" +
		"  - id: implementation\n" +
		"  - id: completed\n"
}

func validCLIPolicyYAMLWithEvidenceGate(policyName string, requireJiraComment bool, requireLocalEvidence bool) string {
	jiraCommentRequired := "false"
	if requireJiraComment {
		jiraCommentRequired = "true"
	}
	localEvidenceRequired := "false"
	if requireLocalEvidence {
		localEvidenceRequired = "true"
	}
	return "policy: " + policyName + "\n" +
		"version: 1\n" +
		"gates:\n" +
		"  write_jira_comment:\n" +
		"    required: " + jiraCommentRequired + "\n" +
		"  write_local_evidence:\n" +
		"    required: " + localEvidenceRequired + "\n" +
		"  transition_jira_status:\n" +
		"    required: true\n" +
		"  git_commit:\n" +
		"    required: true\n" +
		"  git_push:\n" +
		"    required: true\n" +
		"  git_merge:\n" +
		"    required: true\n" +
		"  git_rebase:\n" +
		"    required: true\n" +
		"  git_clean:\n" +
		"    required: true\n" +
		"  create_pr:\n" +
		"    required: true\n" +
		"  update_pr:\n" +
		"    required: true\n" +
		"  fix_pr_comments:\n" +
		"    required: true\n" +
		"  scope_change:\n" +
		"    required: true\n" +
		"authorization_scopes:\n" +
		"  task_execution:\n" +
		"    confirmation_source: jira_decision\n" +
		"    required_bindings:\n" +
		"      - issue_key\n" +
		"    covered_operations:\n" +
		"      - git_push\n" +
		"    excluded_operations:\n" +
		"      - git_merge\n" +
		"    invalidated_by:\n" +
		"      - scope_changed\n"
}

func withJiraClientForTest(t *testing.T, selection clihandlers.JiraClientSelection) {
	t.Helper()
	restore := clihandlers.SetJiraClientSelectorForTest(func(workspaceName string, workspaceProfile profile.Profile) (clihandlers.JiraClientSelection, error) {
		return selection, nil
	})
	t.Cleanup(restore)
}

type recordingJiraClient struct {
	issue               jira.Issue
	issueReads          []jira.Issue
	issueReadErrors     []error
	getIssueCalls       int
	updatedKey          string
	updatedFields       map[string]any
	updateCalls         int
	updateErr           error
	commentKey          string
	commentBody         string
	commentCalls        int
	commentErr          error
	descriptionKey      string
	descriptionSections map[string]string
	descriptionErr      error
	transitionKey       string
	transitionRequest   jira.TransitionRequest
	transitionCalls     int
	transitionErr       error
	transitions         []jira.Transition
	transitionsErr      error
}

func (client *recordingJiraClient) CurrentUser(ctx context.Context) (string, error) {
	return "current-user", nil
}

func (client *recordingJiraClient) SearchIssues(ctx context.Context, workspace string, jql string) ([]jira.Issue, error) {
	return []jira.Issue{client.issue}, nil
}

func (client *recordingJiraClient) GetIssueByKey(ctx context.Context, workspace string, key string) (jira.Issue, bool, error) {
	callIndex := client.getIssueCalls
	client.getIssueCalls++
	if callIndex < len(client.issueReadErrors) && client.issueReadErrors[callIndex] != nil {
		return jira.Issue{}, false, client.issueReadErrors[callIndex]
	}
	if len(client.issueReads) > 0 {
		if callIndex >= len(client.issueReads) {
			callIndex = len(client.issueReads) - 1
		}
		issue := client.issueReads[callIndex]
		if issue.Key == key {
			return issue, true, nil
		}
		return jira.Issue{}, false, nil
	}
	if client.issue.Key == key {
		return client.issue, true, nil
	}
	return jira.Issue{}, false, nil
}

func (client *recordingJiraClient) AddComment(ctx context.Context, key string, body string) error {
	client.commentCalls++
	client.commentKey = key
	client.commentBody = body
	if client.commentErr != nil {
		return client.commentErr
	}
	return nil
}

func (client *recordingJiraClient) UpdateFields(ctx context.Context, key string, fields map[string]any) error {
	client.updateCalls++
	client.updatedKey = key
	client.updatedFields = fields
	if client.updateErr != nil {
		return client.updateErr
	}
	return nil
}

func (client *recordingJiraClient) UpdateDescriptionSections(ctx context.Context, key string, sections map[string]string) error {
	client.descriptionKey = key
	client.descriptionSections = sections
	if client.descriptionErr != nil {
		return client.descriptionErr
	}
	return nil
}

func (client *recordingJiraClient) Transitions(ctx context.Context, key string) ([]jira.Transition, error) {
	if client.transitionsErr != nil {
		return nil, client.transitionsErr
	}
	if len(client.transitions) > 0 {
		return client.transitions, nil
	}
	return []jira.Transition{{ID: "11", Name: "Start Progress"}, {ID: "31", Name: "Done"}}, nil
}

func (client *recordingJiraClient) TransitionIssue(ctx context.Context, key string, request jira.TransitionRequest) error {
	client.transitionCalls++
	client.transitionKey = key
	client.transitionRequest = request
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

func withGitHubClientForTest(t *testing.T, client github.Client) {
	t.Helper()
	restore := clihandlers.SetGitHubClientForTest(client)
	t.Cleanup(restore)
}

func withGitInspectorForTest(t *testing.T, inspector func(context.Context, string) (gitops.WorkspaceStatus, error)) {
	t.Helper()
	restore := clihandlers.SetInspectGitWorkspaceForTest(inspector)
	t.Cleanup(restore)
}

func withCommandAvailabilityForTest(t *testing.T, check func(string) bool) {
	t.Helper()
	restore := clihandlers.SetCommandAvailableForTest(check)
	t.Cleanup(restore)
}

type cliFakeGitHubRunner struct {
	outputs map[string]string
	errors  map[string]error
}

func (f *cliFakeGitHubRunner) Run(ctx context.Context, args ...string) ([]byte, error) {
	command := strings.Join(args, " ")
	if err := f.errors[command]; err != nil {
		return nil, err
	}
	output, ok := f.outputs[command]
	if !ok {
		return nil, errors.New("unexpected gh command: " + command)
	}
	return []byte(output), nil
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
		Key:        "TAP-123",
		Summary:    "修复示例任务",
		Owner:      "current-user",
		Assignee:   "current-user",
		IssueType:  "Task",
		Status:     "To Do",
		TargetRepo: "tapstate/example-repo",
		FormValues: map[string]string{
			"problem_branch":      "develop",
			"target_branch":       "develop",
			"problem_summary":     "修复示例任务",
			"acceptance_criteria": "单元测试通过",
			"verification_method": "go test ./...",
			"risk_level":          "low",
		},
	}
}

func realModeBoundIssue() jira.Issue {
	issue := realModeIssue()
	issue.AgenticID = "agentic-cli-local-agent"
	return issue
}

func takeoverReadbackIssue(issue jira.Issue, status string) jira.Issue {
	readback := issue
	readback.Status = status
	readback.AgenticID = "agentic-cli-local-agent"
	readback.FormValues = map[string]string{}
	for key, value := range issue.FormValues {
		readback.FormValues[key] = value
	}
	readback.FormValues["agentic_id"] = "agentic-cli-local-agent"
	readback.FormValues["agentic_run_id"] = issue.Key + "-takeover-20260721103012-a8f3"
	readback.FormValues["agentic_takeover_at"] = "2026-07-21T10:30:12Z"
	readback.FormValues["agentic_heartbeat_at"] = "2026-07-21T10:30:12Z"
	return readback
}

func realModeAOIssue() jira.Issue {
	return jira.Issue{
		Key:        "AO-6",
		Summary:    "实现 Jira 原子接管与事实回读",
		Owner:      "current-user",
		Assignee:   "current-user",
		IssueType:  "Agentic 缺陷",
		Status:     "待接管",
		TargetRepo: "tapstate/agentic-ops",
		FormValues: map[string]string{},
	}
}
