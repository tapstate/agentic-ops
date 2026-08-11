package cli

import (
	"bytes"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/tapstate/agentic-ops/packages/agentic-cli/internal/clihandlers"
	"github.com/tapstate/agentic-ops/packages/agentic-cli/internal/jira"
)

type countingClock struct {
	now   time.Time
	calls int
}

func (clock *countingClock) Now() time.Time {
	clock.calls++
	return clock.now
}

func TestListTasksRejectsFakeJiraByDefault(t *testing.T) {
	var stdout bytes.Buffer
	var stderr bytes.Buffer
	code := Run([]string{"list-tasks", "--workspace", "tapstate"}, &stdout, &stderr)
	if code != 1 {
		t.Fatalf("code = %d stdout = %s stderr = %s", code, stdout.String(), stderr.String())
	}
	assertJSONField(t, stdout.String(), "operation", "list_tasks")
	assertJSONField(t, stdout.String(), "code", "jira_adapter_config_failed")
	if strings.Contains(stdout.String(), "TAP-123") || strings.Contains(stdout.String(), "TAP-BUG-123") {
		t.Fatalf("list-tasks returned sample Jira tasks: %s", stdout.String())
	}
}

func TestListTasksReadsRealJiraTasks(t *testing.T) {
	var sawSearch bool
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPost || r.URL.Path != "/rest/api/3/search/jql" {
			t.Fatalf("unexpected Jira request %s %s", r.Method, r.URL.Path)
		}
		user, token, ok := r.BasicAuth()
		if !ok || user != "bot@example.com" || token != "token-123" {
			t.Fatalf("unexpected Jira auth user=%q token=%q ok=%v", user, token, ok)
		}
		data, err := io.ReadAll(r.Body)
		if err != nil {
			t.Fatalf("read request body error = %v", err)
		}
		if !strings.Contains(string(data), `assignee = currentUser()`) {
			t.Fatalf("request body missing profile task query: %s", string(data))
		}
		sawSearch = true
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{"issues":[{"key":"TAP-999","fields":{"summary":"真实 Jira 任务","assignee":{"accountId":"account-999"},"issuetype":{"name":"Task"},"status":{"name":"To Do"},"customfield_acceptance":"真实验收","customfield_target_repo":"tapstate/real-repo","customfield_risk":{"value":"low"},"description":{"type":"doc","content":[{"type":"paragraph","content":[{"type":"text","text":"验证方式"}]},{"type":"paragraph","content":[{"type":"text","text":"go test ./real"}]}]}}}]}`))
	}))
	defer server.Close()
	t.Setenv("AGENTIC_OPS_JIRA_ADAPTER", "real")
	t.Setenv("AGENTIC_OPS_JIRA_BASE_URL", server.URL)
	t.Setenv("AGENTIC_OPS_JIRA_EMAIL", "bot@example.com")
	t.Setenv("AGENTIC_OPS_JIRA_API_TOKEN", "token-123")

	var stdout bytes.Buffer
	var stderr bytes.Buffer
	code := Run([]string{"list-tasks", "--workspace", "tapstate"}, &stdout, &stderr)
	if code != 0 {
		t.Fatalf("code = %d stdout = %s stderr = %s", code, stdout.String(), stderr.String())
	}
	if !sawSearch {
		t.Fatal("Jira search endpoint was not called")
	}
	for _, want := range []string{`"operation":"list_tasks"`, `"workspace":"tapstate"`, `"key":"TAP-999"`, `"summary":"真实 Jira 任务"`, `"verification_method":"go test ./real"`} {
		if !strings.Contains(stdout.String(), want) {
			t.Fatalf("stdout missing %s: %s", want, stdout.String())
		}
	}
	if strings.Contains(stdout.String(), "TAP-123") || strings.Contains(stdout.String(), "TAP-BUG-123") {
		t.Fatalf("list-tasks returned sample Jira tasks: %s", stdout.String())
	}
}

func TestListTasksReadsWorkspaceJiraConfigFile(t *testing.T) {
	root := t.TempDir()
	installDir := t.TempDir()
	t.Setenv("AGENTIC_OPS_WORKSPACE_ROOT", root)
	t.Setenv("AGENTIC_OPS_HOME", installDir)
	var sawSearch bool
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPost || r.URL.Path != "/rest/api/3/search/jql" {
			t.Fatalf("unexpected Jira request %s %s", r.Method, r.URL.Path)
		}
		user, token, ok := r.BasicAuth()
		if !ok || user != "workspace@example.com" || token != "workspace-token" {
			t.Fatalf("unexpected Jira auth user=%q token=%q ok=%v", user, token, ok)
		}
		sawSearch = true
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{"issues":[{"key":"TAP-998","fields":{"summary":"工作空间 Jira 配置任务","assignee":{"accountId":"account-998"},"issuetype":{"name":"Task"},"status":{"name":"To Do"},"customfield_acceptance":"真实验收","customfield_target_repo":"tapstate/real-repo","customfield_risk":{"value":"low"},"description":{"type":"doc","content":[{"type":"paragraph","content":[{"type":"text","text":"go test ./config"}]}]}}}]}`))
	}))
	defer server.Close()
	writeCLITestFile(t, filepath.Join(root, ".agentic-ops", "config.local.yaml"), "projects:\n  tapstate:\n    jira:\n      adapter: real\n      base_url: "+server.URL+"\n      email: workspace@example.com\n")
	writeCLITestFile(t, filepath.Join(installDir, "user", ".env"), "AGENTIC_OPS_JIRA_API_TOKEN=workspace-token\n")

	var stdout bytes.Buffer
	var stderr bytes.Buffer
	code := Run([]string{"list-tasks", "--workspace", "tapstate"}, &stdout, &stderr)
	if code != 0 {
		t.Fatalf("code = %d stdout = %s stderr = %s", code, stdout.String(), stderr.String())
	}
	if !sawSearch {
		t.Fatal("Jira search endpoint was not called")
	}
	for _, want := range []string{`"operation":"list_tasks"`, `"workspace":"tapstate"`, `"key":"TAP-998"`, `"summary":"工作空间 Jira 配置任务"`} {
		if !strings.Contains(stdout.String(), want) {
			t.Fatalf("stdout missing %s: %s", want, stdout.String())
		}
	}
}

func TestListTasksReadsPersonalProjectJiraConfigFromCentralConfig(t *testing.T) {
	root := t.TempDir()
	installDir := t.TempDir()
	t.Setenv("AGENTIC_OPS_WORKSPACE_ROOT", root)
	t.Setenv("AGENTIC_OPS_HOME", installDir)
	t.Setenv("AGENTIC_OPS_JIRA_API_TOKEN", "personal-token")
	var sawSearch bool
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPost || r.URL.Path != "/rest/api/3/search/jql" {
			t.Fatalf("unexpected Jira request %s %s", r.Method, r.URL.Path)
		}
		user, token, ok := r.BasicAuth()
		if !ok || user != "personal@example.com" || token != "personal-token" {
			t.Fatalf("unexpected Jira auth user=%q token=%q ok=%v", user, token, ok)
		}
		sawSearch = true
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{"issues":[{"key":"TAP-997","fields":{"summary":"个人项目 Jira 配置任务","assignee":{"accountId":"account-997"},"issuetype":{"name":"Task"},"status":{"name":"To Do"},"customfield_acceptance":"真实验收","customfield_target_repo":"tapstate/real-repo","customfield_risk":{"value":"low"},"description":{"type":"doc","content":[{"type":"paragraph","content":[{"type":"text","text":"go test ./personal"}]}]}}}]}`))
	}))
	defer server.Close()
	writeCLITestFile(t, filepath.Join(root, ".agentic-ops", "config.local.yaml"), "\n")
	writeCLITestFile(t, filepath.Join(installDir, "user", "config.local.yaml"), "projects:\n  tapstate:\n    jira:\n      adapter: real\n      base_url: "+server.URL+"\n      email: personal@example.com\n")

	var stdout bytes.Buffer
	var stderr bytes.Buffer
	code := Run([]string{"list-tasks", "--workspace", "tapstate"}, &stdout, &stderr)
	if code != 0 {
		t.Fatalf("code = %d stdout = %s stderr = %s", code, stdout.String(), stderr.String())
	}
	if !sawSearch {
		t.Fatal("Jira search endpoint was not called")
	}
	for _, want := range []string{`"operation":"list_tasks"`, `"workspace":"tapstate"`, `"key":"TAP-997"`, `"summary":"个人项目 Jira 配置任务"`} {
		if !strings.Contains(stdout.String(), want) {
			t.Fatalf("stdout missing %s: %s", want, stdout.String())
		}
	}
}

func TestListTasksReadsPersonalConfigAndAgenticEnv(t *testing.T) {
	root := t.TempDir()
	installDir := t.TempDir()
	t.Setenv("AGENTIC_OPS_WORKSPACE_ROOT", root)
	t.Setenv("AGENTIC_OPS_HOME", installDir)
	var sawSearch bool
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPost || r.URL.Path != "/rest/api/3/search/jql" {
			t.Fatalf("unexpected Jira request %s %s", r.Method, r.URL.Path)
		}
		user, token, ok := r.BasicAuth()
		if !ok || user != "dotenv@example.com" || token != "dotenv-token" {
			t.Fatalf("unexpected Jira auth user=%q token=%q ok=%v", user, token, ok)
		}
		sawSearch = true
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{"issues":[{"key":"TAP-996","fields":{"summary":"dotenv Jira 配置任务","assignee":{"accountId":"account-996"},"issuetype":{"name":"Task"},"status":{"name":"To Do"},"customfield_acceptance":"真实验收","customfield_target_repo":"tapstate/real-repo","customfield_risk":{"value":"low"},"description":{"type":"doc","content":[{"type":"paragraph","content":[{"type":"text","text":"go test ./dotenv"}]}]}}}]}`))
	}))
	defer server.Close()
	userDir := filepath.Join(installDir, "user")
	writeCLITestFile(t, filepath.Join(userDir, "config.local.yaml"), "projects:\n  tapstate:\n    jira:\n      adapter: real\n      base_url: "+server.URL+"\n      email: dotenv@example.com\n")
	writeCLITestFile(t, filepath.Join(userDir, ".env"), "AGENTIC_OPS_JIRA_API_TOKEN=dotenv-token\n")

	var stdout bytes.Buffer
	var stderr bytes.Buffer
	code := Run([]string{"list-tasks", "--workspace", "tapstate"}, &stdout, &stderr)
	if code != 0 {
		t.Fatalf("code = %d stdout = %s stderr = %s", code, stdout.String(), stderr.String())
	}
	if !sawSearch {
		t.Fatal("Jira search endpoint was not called")
	}
	for _, want := range []string{`"operation":"list_tasks"`, `"workspace":"tapstate"`, `"key":"TAP-996"`, `"summary":"dotenv Jira 配置任务"`} {
		if !strings.Contains(stdout.String(), want) {
			t.Fatalf("stdout missing %s: %s", want, stdout.String())
		}
	}
}

func TestListTasksDoesNotReadJiraTokenFromWorkspaceEnv(t *testing.T) {
	root := t.TempDir()
	installDir := t.TempDir()
	t.Setenv("AGENTIC_OPS_WORKSPACE_ROOT", root)
	t.Setenv("AGENTIC_OPS_HOME", installDir)
	writeCLITestFile(t, filepath.Join(root, ".agentic-ops", "config.local.yaml"), "projects:\n  tapstate:\n    jira:\n      adapter: real\n      base_url: https://jira.example.test\n      email: workspace@example.com\n")
	writeCLITestFile(t, filepath.Join(root, ".agentic-ops", ".env"), "AGENTIC_OPS_JIRA_API_TOKEN=workspace-token\n")

	var stdout bytes.Buffer
	var stderr bytes.Buffer
	code := Run([]string{"list-tasks", "--workspace", "tapstate"}, &stdout, &stderr)
	if code != 1 {
		t.Fatalf("code = %d stdout = %s stderr = %s", code, stdout.String(), stderr.String())
	}
	assertJSONField(t, stdout.String(), "operation", "list_tasks")
	assertJSONField(t, stdout.String(), "code", "jira_adapter_config_failed")
	assertJSONField(t, stdout.String(), "jira_token_env", "AGENTIC_OPS_JIRA_API_TOKEN")
	assertJSONField(t, stdout.String(), "jira_token_env_has_value", false)
	assertJSONField(t, stdout.String(), "jira_env_file", filepath.Join(installDir, "user", ".env"))
	if strings.Contains(stdout.String(), "workspace-token") {
		t.Fatalf("stdout must not reveal or use workspace env token: %s", stdout.String())
	}
}

func TestListTasksGuidesJiraTokenWhenTokenEnvMissing(t *testing.T) {
	root := t.TempDir()
	installDir := t.TempDir()
	t.Setenv("AGENTIC_OPS_WORKSPACE_ROOT", root)
	t.Setenv("AGENTIC_OPS_HOME", installDir)
	userDir := filepath.Join(installDir, "user")
	writeCLITestFile(t, filepath.Join(userDir, "config.local.yaml"), "projects:\n  tapstate:\n    jira:\n      adapter: real\n      base_url: https://jira.example.test\n      email: personal@example.com\n")

	var stdout bytes.Buffer
	var stderr bytes.Buffer
	code := Run([]string{"list-tasks", "--workspace", "tapstate"}, &stdout, &stderr)
	if code != 1 {
		t.Fatalf("code = %d stdout = %s stderr = %s", code, stdout.String(), stderr.String())
	}
	assertJSONField(t, stdout.String(), "operation", "list_tasks")
	assertJSONField(t, stdout.String(), "code", "jira_adapter_config_failed")
	assertJSONField(t, stdout.String(), "jira_token_env", "AGENTIC_OPS_JIRA_API_TOKEN")
	assertJSONField(t, stdout.String(), "jira_token_env_has_value", false)
	assertJSONField(t, stdout.String(), "jira_env_file", filepath.Join(userDir, ".env"))
	assertJSONField(t, stdout.String(), "jira_config_source", filepath.Join(userDir, "config.local.yaml"))
	assertJSONField(t, stdout.String(), "jira_token_help_url", "https://id.atlassian.com/manage-profile/security/api-tokens")
	if !strings.Contains(stdout.String(), ".env") || !strings.Contains(stdout.String(), "AGENTIC_OPS_JIRA_API_TOKEN=") {
		t.Fatalf("stdout missing token setup guidance: %s", stdout.String())
	}
}

func TestTakeoverTaskReturnsRunIDAndStage(t *testing.T) {
	root := t.TempDir()
	t.Chdir(root)
	operationTime := time.Date(2026, 8, 11, 9, 8, 7, 0, time.UTC)
	clock := &countingClock{now: operationTime}
	restoreClock := clihandlers.SetClockForTest(clock)
	defer restoreClock()
	var stdout bytes.Buffer
	var stderr bytes.Buffer
	code := Run([]string{"takeover-task", "TAP-123", "--workspace", "tapstate"}, &stdout, &stderr)
	if code != 0 {
		t.Fatalf("code = %d", code)
	}
	for _, want := range []string{`"operation":"takeover_task"`, `"task_type":"task_takeover"`, `"current_stage":"takeover_started"`, `"agentic_next_action":"proceed"`} {
		if !strings.Contains(stdout.String(), want) {
			t.Fatalf("stdout missing %s: %s", want, stdout.String())
		}
	}
	assertJSONField(t, stdout.String(), "agent_id", "agentic-cli-local-agent")
	assertJSONField(t, stdout.String(), "agentic_id", "agentic-cli-local-agent")
	assertJSONField(t, stdout.String(), "agentic_run_id", "TAP-123-takeover-20260811090807-a8f3")
	assertJSONField(t, stdout.String(), "agentic_takeover_at", "2026-08-11T09:08:07Z")
	assertJSONField(t, stdout.String(), "agentic_heartbeat_at", "2026-08-11T09:08:07Z")
	assertJSONField(t, stdout.String(), "task_class", "technical_task")
	assertJSONField(t, stdout.String(), "task_class_source", "issue_type:Task")
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
	if !strings.Contains(string(events), `"timestamp":"2026-08-11T09:08:07Z"`) {
		t.Fatalf("events missing operation timestamp: %s", string(events))
	}
	if clock.calls != 1 {
		t.Fatalf("clock calls = %d, want 1", clock.calls)
	}
	for _, want := range []string{
		`"agent_id":"agentic-cli-local-agent"`,
		`"agentic_id":"agentic-cli-local-agent"`,
		`"agentic_takeover_at":"2026-08-11T09:08:07Z"`,
		`"task_class":"technical_task"`,
		`"process_id":"development_change_v1"`,
	} {
		if !strings.Contains(string(events), want) {
			t.Fatalf("events missing %s: %s", want, string(events))
		}
	}
}

func TestTakeoverTaskUsesTargetRepoFallback(t *testing.T) {
	root := t.TempDir()
	t.Setenv("AGENTIC_OPS_WORKSPACE_ROOT", root)
	var stdout bytes.Buffer
	var stderr bytes.Buffer
	code := Run([]string{"takeover-task", "TAP-MISSING-REPO", "--workspace", "tapstate"}, &stdout, &stderr)
	if code != 0 {
		t.Fatalf("code = %d stdout = %s", code, stdout.String())
	}
	assertJSONField(t, stdout.String(), "operation", "takeover_task")
	assertJSONField(t, stdout.String(), "issue_key", "TAP-MISSING-REPO")
	assertJSONField(t, stdout.String(), "target_repo", "tapstate/tap-api")
	assertJSONField(t, stdout.String(), "current_stage", "takeover_started")
	assertJSONField(t, stdout.String(), "agentic_next_action", "proceed")
	events, err := os.ReadFile(filepath.Join(root, ".agentic-ops", "feedback", "events.ndjson"))
	if err != nil {
		t.Fatalf("ReadFile events error = %v", err)
	}
	if !strings.Contains(string(events), `"target_repo":"tapstate/tap-api"`) {
		t.Fatalf("events = %s", string(events))
	}
	if !strings.Contains(string(events), `"gate_status":"passed"`) {
		t.Fatalf("events = %s", string(events))
	}
}

func TestTakeoverTaskBlocksStatusOutsideProcessEntryStage(t *testing.T) {
	root := t.TempDir()
	t.Setenv("AGENTIC_OPS_WORKSPACE_ROOT", root)
	var stdout bytes.Buffer
	var stderr bytes.Buffer
	code := Run([]string{"takeover-task", "TAP-IN-PROGRESS", "--workspace", "tapstate"}, &stdout, &stderr)
	if code != 1 {
		t.Fatalf("code = %d stdout = %s stderr = %s", code, stdout.String(), stderr.String())
	}
	assertJSONField(t, stdout.String(), "operation", "takeover_task")
	assertJSONField(t, stdout.String(), "code", "invalid_takeover_stage")
	assertJSONField(t, stdout.String(), "current_stage", "takeover_gate")
	assertJSONField(t, stdout.String(), "agentic_next_action", "ask_owner")
}

func TestInspectTaskOutputsFactsAndProjectAssetRefsWithoutSideEffects(t *testing.T) {
	root := t.TempDir()
	t.Setenv("AGENTIC_OPS_WORKSPACE_ROOT", root)
	issue := realModeIssue()
	issue.IssueType = "Bug"
	issue.FormValues["problem_branch"] = ""
	issue.FormValues["target_branch"] = ""
	issue.FormValues["problem_summary"] = "TM 启动时持续输出 Elasticsearch health check refused 告警"
	issue.Comments = []jira.Comment{{ID: "101", Author: "current-user", Body: "修复计划 v1 已确认"}}
	client := &recordingJiraClient{issue: issue}
	withJiraClientForTest(t, clihandlers.JiraClientSelection{Client: client, Mode: "real"})

	var stdout bytes.Buffer
	var stderr bytes.Buffer
	code := Run([]string{"inspect-task", "TAP-123", "--workspace", "tapdata"}, &stdout, &stderr)
	if code != 0 {
		t.Fatalf("code = %d stdout = %s stderr = %s", code, stdout.String(), stderr.String())
	}
	assertJSONField(t, stdout.String(), "operation", "inspect_task")
	assertJSONField(t, stdout.String(), "issue_key", "TAP-123")
	assertJSONField(t, stdout.String(), "current_jira_user", "current-user")
	assertNestedJSONField(t, stdout.String(), []string{"gate_facts", "task_class"}, "bug_fix")
	assertNestedJSONField(t, stdout.String(), []string{"gate_facts", "standard_process_id"}, "development_change_v1")
	assertNestedJSONField(t, stdout.String(), []string{"gate_facts", "mapped_stage"}, "waiting_takeover")
	assertNestedJSONField(t, stdout.String(), []string{"gate_facts", "assignee_matches_current_user"}, true)
	assertNestedJSONField(t, stdout.String(), []string{"form_values", "problem_branch"}, "")
	assertNestedJSONField(t, stdout.String(), []string{"form_values", "target_branch"}, "")
	assertNestedJSONField(t, stdout.String(), []string{"form_values", "problem_summary"}, "TM 启动时持续输出 Elasticsearch health check refused 告警")
	assertNestedJSONField(t, stdout.String(), []string{"asset_refs", "admission_dir"}, "install-resources/basic/projects/tapdata/admission")
	if !strings.Contains(stdout.String(), `"comments":[{"id":"101","author":"current-user","body":"修复计划 v1 已确认"}]`) {
		t.Fatalf("inspect-task comments missing: %s", stdout.String())
	}
	assertJSONField(t, stdout.String(), "recommended_next_action", "inspect_by_agent")
	for _, notWant := range []string{"admission_check_failed", "completion_template", "suggestions"} {
		if strings.Contains(stdout.String(), notWant) {
			t.Fatalf("inspect-task should not contain %s: %s", notWant, stdout.String())
		}
	}
	if client.updatedKey != "" || client.commentKey != "" {
		t.Fatalf("inspect-task wrote Jira: updated=%s comment=%s", client.updatedKey, client.commentKey)
	}
	if _, err := os.Stat(filepath.Join(root, ".agentic-ops", "feedback", "events.ndjson")); !os.IsNotExist(err) {
		t.Fatalf("inspect-task should not write takeover event, stat err = %v", err)
	}
}

func TestTakeoverTaskDoesNotEnforceProjectAdmissionFields(t *testing.T) {
	root := t.TempDir()
	t.Setenv("AGENTIC_OPS_WORKSPACE_ROOT", root)
	issue := realModeIssue()
	issue.IssueType = "Bug"
	issue.FormValues["problem_branch"] = ""
	issue.FormValues["target_branch"] = ""
	issue.FormValues["problem_summary"] = "TM 启动时持续输出 Elasticsearch health check refused 告警"
	issue.FormValues["acceptance_criteria"] = ""
	issue.FormValues["verification_method"] = ""
	issue.FormValues["risk_level"] = ""
	client := &recordingJiraClient{issue: issue}
	withJiraClientForTest(t, clihandlers.JiraClientSelection{Client: client, Mode: "real"})

	var stdout bytes.Buffer
	var stderr bytes.Buffer
	code := Run([]string{"takeover-task", "TAP-123", "--workspace", "tapstate", "--confirm-real-jira-write"}, &stdout, &stderr)
	if code != 0 {
		t.Fatalf("code = %d stdout = %s stderr = %s", code, stdout.String(), stderr.String())
	}
	assertJSONField(t, stdout.String(), "operation", "takeover_task")
	assertJSONField(t, stdout.String(), "current_stage", "takeover_started")
	assertJSONField(t, stdout.String(), "agentic_next_action", "proceed")
	if client.updatedKey != "TAP-123" {
		t.Fatalf("updatedKey = %s", client.updatedKey)
	}
	if client.commentKey != "" {
		t.Fatalf("takeover-task should not write admission comment, commentKey = %s body = %s", client.commentKey, client.commentBody)
	}
	for _, notWant := range []string{"admission_check_failed", "admission_standard_path", "admission_template_path", "missing_field_guidance", "suggestions", "completion_template"} {
		if strings.Contains(stdout.String(), notWant) {
			t.Fatalf("takeover-task should not contain %s: %s", notWant, stdout.String())
		}
	}
}

func TestTakeoverTaskWritesAgentOwnershipCommentWhenProfileUsesJiraComment(t *testing.T) {
	root := t.TempDir()
	t.Setenv("AGENTIC_OPS_WORKSPACE_ROOT", root)
	issue := realModeIssue()
	issue.AgenticID = ""
	issue.FormValues["agentic_id"] = ""
	client := &recordingJiraClient{issue: issue}
	withJiraClientForTest(t, clihandlers.JiraClientSelection{Client: client, Mode: "real"})

	var stdout bytes.Buffer
	var stderr bytes.Buffer
	code := Run([]string{"takeover-task", "TAP-123", "--workspace", "tapdata", "--confirm-real-jira-write"}, &stdout, &stderr)
	if code != 0 {
		t.Fatalf("code = %d stdout = %s stderr = %s", code, stdout.String(), stderr.String())
	}
	assertJSONField(t, stdout.String(), "operation", "takeover_task")
	assertJSONField(t, stdout.String(), "current_stage", "takeover_started")
	if client.updatedKey != "" {
		t.Fatalf("takeover-task should not update Jira fields for jira_comment mapping: %s %#v", client.updatedKey, client.updatedFields)
	}
	if client.commentKey != "TAP-123" {
		t.Fatalf("commentKey = %s body = %s", client.commentKey, client.commentBody)
	}
	for _, want := range []string{"AgenticOps ownership", "agentic_id: agentic-cli-local-agent", "agentic_takeover_at: 2026-07-21T10:30:12Z"} {
		if !strings.Contains(client.commentBody, want) {
			t.Fatalf("ownership comment missing %q: %s", want, client.commentBody)
		}
	}
}

func TestResumeTakeoverReturnsRunIDAndNextAction(t *testing.T) {
	root := t.TempDir()
	t.Setenv("AGENTIC_OPS_WORKSPACE_ROOT", root)
	writeCLITestFile(t, filepath.Join(root, ".agentic-ops", "feedback", "events.ndjson"), `{"timestamp":"2026-07-21T10:30:12Z","workspace":"tapstate","agentic_run_id":"run-1","issue_key":"TAP-123","operation":"takeover_task","task_type":"task_takeover","current_stage":"takeover_started","agentic_next_action":"proceed","agent_id":"agentic-cli-local-agent","agentic_id":"agentic-cli-local-agent","task_class":"technical_task","process_id":"development_change_v1","target_repo":"tapstate/example-repo","ok":true,"gate":"takeover_task","gate_status":"passed"}
`)
	var stdout bytes.Buffer
	var stderr bytes.Buffer
	code := Run([]string{"resume-takeover", "--workspace", "tapstate", "--run-id", "run-1"}, &stdout, &stderr)
	if code != 0 {
		t.Fatalf("code = %d", code)
	}
	assertJSONField(t, stdout.String(), "operation", "resume_takeover")
	assertJSONField(t, stdout.String(), "agentic_run_id", "run-1")
	assertJSONField(t, stdout.String(), "issue_key", "TAP-123")
	assertJSONField(t, stdout.String(), "previous_stage", "takeover_started")
	assertJSONField(t, stdout.String(), "current_stage", "takeover_started")
	assertJSONField(t, stdout.String(), "agentic_next_action", "proceed")
	assertJSONField(t, stdout.String(), "target_repo", "tapstate/example-repo")
	assertJSONField(t, stdout.String(), "standard_process_stage", "waiting_takeover")
}

func TestResumeTakeoverRechecksRealJiraWithoutWriting(t *testing.T) {
	root := t.TempDir()
	t.Setenv("AGENTIC_OPS_WORKSPACE_ROOT", root)
	writeCLITestFile(t, filepath.Join(root, ".agentic-ops", "feedback", "events.ndjson"), `{"timestamp":"2026-07-21T10:30:12Z","workspace":"tapstate","agentic_run_id":"run-1","issue_key":"TAP-123","operation":"takeover_task","task_type":"task_takeover","current_stage":"takeover_started","agentic_next_action":"proceed","agent_id":"agentic-cli-local-agent","agentic_id":"agentic-cli-local-agent","task_class":"technical_task","process_id":"development_change_v1","target_repo":"tapstate/example-repo","ok":true,"gate":"takeover_task","gate_status":"passed"}
`)
	client := &recordingJiraClient{issue: realModeBoundIssue()}
	withJiraClientForTest(t, clihandlers.JiraClientSelection{Client: client, Mode: "real"})

	var stdout bytes.Buffer
	var stderr bytes.Buffer
	code := Run([]string{"resume-takeover", "--workspace", "tapstate", "--run-id", "run-1"}, &stdout, &stderr)

	if code != 0 {
		t.Fatalf("code = %d stdout = %s stderr = %s", code, stdout.String(), stderr.String())
	}
	assertJSONField(t, stdout.String(), "standard_process_stage", "waiting_takeover")
	if client.commentKey != "" ||
		client.updatedKey != "" ||
		client.descriptionKey != "" ||
		client.transitionKey != "" {
		t.Fatalf("resume-takeover performed Jira write: %#v", client)
	}
}

func TestResumeTakeoverCreatesWritableJiraFeedbackForLostBinding(t *testing.T) {
	root := t.TempDir()
	t.Setenv("AGENTIC_OPS_WORKSPACE_ROOT", root)
	writeCLITestFile(t, filepath.Join(root, ".agentic-ops", "feedback", "events.ndjson"), `{"timestamp":"2026-07-21T10:30:12Z","workspace":"tapstate","agentic_run_id":"run-1","issue_key":"TAP-123","operation":"takeover_task","task_type":"task_takeover","current_stage":"takeover_started","agentic_next_action":"proceed","agent_id":"agentic-cli-local-agent","agentic_id":"agentic-cli-local-agent","task_class":"technical_task","process_id":"development_change_v1","target_repo":"tapstate/example-repo","ok":true,"gate":"takeover_task","gate_status":"passed"}
`)
	client := &recordingJiraClient{issue: realModeIssue()}
	withJiraClientForTest(t, clihandlers.JiraClientSelection{Client: client, Mode: "real"})

	var stdout bytes.Buffer
	var stderr bytes.Buffer
	code := Run([]string{"resume-takeover", "--workspace", "tapstate", "--run-id", "run-1"}, &stdout, &stderr)

	if code != 1 {
		t.Fatalf("code = %d stdout = %s stderr = %s", code, stdout.String(), stderr.String())
	}
	assertJSONField(t, stdout.String(), "code", "agent_binding_lost")
	assertJSONField(t, stdout.String(), "jira_feedback_required", true)
	assertJSONField(t, stdout.String(), "jira_feedback_write_allowed", true)
	assertJSONField(t, stdout.String(), "jira_feedback_category", "blocked")
	assertJSONField(t, stdout.String(), "agentic_next_action", "add_task_comment")
	feedbackFile := filepath.Join(root, ".agentic-ops", "runs", "run-1", "resume-blocked-agent_binding_lost.md")
	if _, err := os.Stat(feedbackFile); err != nil {
		t.Fatalf("feedback file error = %v", err)
	}
	if client.commentKey != "" || client.updatedKey != "" || client.transitionKey != "" {
		t.Fatalf("resume-takeover performed Jira write: %#v", client)
	}
}

func TestResumeTakeoverCreatesOwnerOnlyFeedbackForOwnershipConflict(t *testing.T) {
	root := t.TempDir()
	t.Setenv("AGENTIC_OPS_WORKSPACE_ROOT", root)
	writeCLITestFile(t, filepath.Join(root, ".agentic-ops", "feedback", "events.ndjson"), `{"timestamp":"2026-07-21T10:30:12Z","workspace":"tapstate","agentic_run_id":"run-1","issue_key":"TAP-123","operation":"takeover_task","task_type":"task_takeover","current_stage":"takeover_started","agentic_next_action":"proceed","agent_id":"agentic-cli-local-agent","agentic_id":"agentic-cli-local-agent","task_class":"technical_task","process_id":"development_change_v1","target_repo":"tapstate/example-repo","ok":true,"gate":"takeover_task","gate_status":"passed"}
`)
	issue := realModeBoundIssue()
	issue.AgenticID = "other-agent"
	withJiraClientForTest(t, clihandlers.JiraClientSelection{Client: &recordingJiraClient{issue: issue}, Mode: "real"})

	var stdout bytes.Buffer
	var stderr bytes.Buffer
	code := Run([]string{"resume-takeover", "--workspace", "tapstate", "--run-id", "run-1"}, &stdout, &stderr)

	if code != 1 {
		t.Fatalf("code = %d stdout = %s stderr = %s", code, stdout.String(), stderr.String())
	}
	assertJSONField(t, stdout.String(), "code", "agent_ownership_conflict")
	assertJSONField(t, stdout.String(), "jira_feedback_required", true)
	assertJSONField(t, stdout.String(), "jira_feedback_write_allowed", false)
	assertJSONField(t, stdout.String(), "agentic_next_action", "ask_owner_to_add_task_comment")
}

func TestGeneratedResumeFeedbackCanBePassedToAddTaskComment(t *testing.T) {
	root := t.TempDir()
	t.Setenv("AGENTIC_OPS_WORKSPACE_ROOT", root)
	writeCLITestFile(t, filepath.Join(root, ".agentic-ops", "feedback", "events.ndjson"), `{"timestamp":"2026-07-21T10:30:12Z","workspace":"tapstate","agentic_run_id":"run-1","issue_key":"TAP-123","operation":"takeover_task","task_type":"task_takeover","current_stage":"takeover_started","agentic_next_action":"proceed","agent_id":"agentic-cli-local-agent","agentic_id":"agentic-cli-local-agent","task_class":"technical_task","process_id":"development_change_v1","target_repo":"tapstate/example-repo","ok":true,"gate":"takeover_task","gate_status":"passed"}
`)
	client := &recordingJiraClient{issue: realModeIssue()}
	withJiraClientForTest(t, clihandlers.JiraClientSelection{Client: client, Mode: "real"})

	var resumeStdout bytes.Buffer
	var resumeStderr bytes.Buffer
	resumeCode := Run([]string{"resume-takeover", "--workspace", "tapstate", "--run-id", "run-1"}, &resumeStdout, &resumeStderr)
	if resumeCode != 1 {
		t.Fatalf("resume code = %d stdout = %s stderr = %s", resumeCode, resumeStdout.String(), resumeStderr.String())
	}

	feedbackFile := filepath.Join(root, ".agentic-ops", "runs", "run-1", "resume-blocked-agent_binding_lost.md")
	var commentStdout bytes.Buffer
	var commentStderr bytes.Buffer
	commentCode := Run([]string{
		"add-task-comment",
		"TAP-123",
		"--workspace", "tapstate",
		"--category", "blocked",
		"--content-file", feedbackFile,
		"--run-id", "run-1",
		"--confirm-real-jira-write",
	}, &commentStdout, &commentStderr)

	if commentCode != 0 {
		t.Fatalf("comment code = %d stdout = %s stderr = %s", commentCode, commentStdout.String(), commentStderr.String())
	}
	assertJSONField(t, commentStdout.String(), "category", "blocked")
	if client.commentKey != "TAP-123" || !strings.Contains(client.commentBody, "resume-blocked:run-1:agent_binding_lost") {
		t.Fatalf("comment = %s %s", client.commentKey, client.commentBody)
	}
}

func TestResumeTakeoverRejectsMissingRun(t *testing.T) {
	root := t.TempDir()
	t.Setenv("AGENTIC_OPS_WORKSPACE_ROOT", root)
	writeCLITestFile(t, filepath.Join(root, ".agentic-ops", "feedback", "events.ndjson"), `{"timestamp":"2026-07-21T10:30:12Z","workspace":"tapstate","agentic_run_id":"other-run","issue_key":"TAP-123","operation":"takeover_task","task_type":"task_takeover","current_stage":"takeover_started","agentic_next_action":"proceed","agent_id":"agentic-cli-local-agent","agentic_id":"agentic-cli-local-agent","task_class":"technical_task","process_id":"development_change_v1","ok":true,"gate":"takeover_task","gate_status":"passed"}
`)
	var stdout bytes.Buffer
	var stderr bytes.Buffer
	code := Run([]string{"resume-takeover", "--workspace", "tapstate", "--run-id", "run-1"}, &stdout, &stderr)
	if code != 1 {
		t.Fatalf("code = %d stdout = %s stderr = %s", code, stdout.String(), stderr.String())
	}
	assertJSONField(t, stdout.String(), "operation", "resume_takeover")
	assertJSONField(t, stdout.String(), "code", "run_not_found")
	assertJSONField(t, stdout.String(), "agentic_next_action", "ask_owner")
	if strings.Contains(stdout.String(), "jira_feedback") {
		t.Fatalf("untrusted local failure should not produce Jira feedback: %s", stdout.String())
	}
}

func TestResumeTakeoverRejectsWorkspaceMismatch(t *testing.T) {
	root := t.TempDir()
	t.Setenv("AGENTIC_OPS_WORKSPACE_ROOT", root)
	writeCLITestFile(t, filepath.Join(root, ".agentic-ops", "feedback", "events.ndjson"), `{"timestamp":"2026-07-21T10:30:12Z","workspace":"other","agentic_run_id":"run-1","issue_key":"TAP-123","operation":"takeover_task","task_type":"task_takeover","current_stage":"takeover_started","agentic_next_action":"proceed","agent_id":"agentic-cli-local-agent","agentic_id":"agentic-cli-local-agent","task_class":"technical_task","process_id":"development_change_v1","ok":true,"gate":"takeover_task","gate_status":"passed"}
`)
	var stdout bytes.Buffer
	var stderr bytes.Buffer
	code := Run([]string{"resume-takeover", "--workspace", "tapstate", "--run-id", "run-1"}, &stdout, &stderr)
	if code != 1 {
		t.Fatalf("code = %d stdout = %s stderr = %s", code, stdout.String(), stderr.String())
	}
	assertJSONField(t, stdout.String(), "operation", "resume_takeover")
	assertJSONField(t, stdout.String(), "code", "workspace_mismatch")
	assertJSONField(t, stdout.String(), "agentic_next_action", "ask_owner")
}

func TestResumeTakeoverRejectsIncompleteLocalState(t *testing.T) {
	root := t.TempDir()
	t.Setenv("AGENTIC_OPS_WORKSPACE_ROOT", root)
	writeCLITestFile(t, filepath.Join(root, ".agentic-ops", "feedback", "events.ndjson"), `{"timestamp":"2026-07-21T10:30:12Z","workspace":"tapstate","agentic_run_id":"run-1","issue_key":"TAP-123","operation":"takeover_task","task_type":"task_takeover","current_stage":"takeover_started","agentic_next_action":"proceed","agent_id":"agentic-cli-local-agent","agentic_id":"agentic-cli-local-agent","ok":true,"gate":"takeover_task","gate_status":"passed"}
`)
	var stdout bytes.Buffer
	var stderr bytes.Buffer
	code := Run([]string{"resume-takeover", "--workspace", "tapstate", "--run-id", "run-1"}, &stdout, &stderr)
	if code != 1 {
		t.Fatalf("code = %d stdout = %s stderr = %s", code, stdout.String(), stderr.String())
	}
	assertJSONField(t, stdout.String(), "operation", "resume_takeover")
	assertJSONField(t, stdout.String(), "code", "local_state_mismatch")
	assertJSONField(t, stdout.String(), "agentic_next_action", "ask_owner")
}

func TestTakeoverTaskRequiresConfirmationForRealJiraWrite(t *testing.T) {
	root := t.TempDir()
	t.Setenv("AGENTIC_OPS_WORKSPACE_ROOT", root)
	withJiraClientForTest(t, clihandlers.JiraClientSelection{Client: &recordingJiraClient{issue: realModeIssue()}, Mode: "real"})

	var stdout bytes.Buffer
	var stderr bytes.Buffer
	code := Run([]string{"takeover-task", "TAP-123", "--workspace", "tapstate"}, &stdout, &stderr)
	if code != 1 {
		t.Fatalf("code = %d stdout = %s stderr = %s", code, stdout.String(), stderr.String())
	}
	assertJSONField(t, stdout.String(), "operation", "takeover_task")
	assertJSONField(t, stdout.String(), "code", "real_jira_confirmation_required")
	assertJSONField(t, stdout.String(), "current_stage", "takeover_gate")
	assertJSONField(t, stdout.String(), "agentic_next_action", "ask_owner")
	assertEventLogContains(t, root, `"gate":"real_jira_write"`)
	assertEventLogContains(t, root, `"gate_status":"blocked"`)
	assertEventLogContains(t, root, `"code":"real_jira_confirmation_required"`)
}

func TestTakeoverTaskRecordsPassedRealJiraWriteGate(t *testing.T) {
	root := t.TempDir()
	t.Setenv("AGENTIC_OPS_WORKSPACE_ROOT", root)
	client := &recordingJiraClient{issue: realModeIssue()}
	withJiraClientForTest(t, clihandlers.JiraClientSelection{Client: client, Mode: "real"})

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
