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

	"github.com/tapstate/agentic-ops/packages/agentic-cli/internal/clihandlers"
)

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
	t.Setenv("AGENTIC_OPS_WORKSPACE_ROOT", root)
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
	writeCLITestFile(t, filepath.Join(root, ".agentic-ops", "config.local.yaml"), "projects:\n  tapstate:\n    jira:\n      adapter: real\n      base_url: "+server.URL+"\n      email: workspace@example.com\n      api_token_env: WORKSPACE_JIRA_TOKEN\n")
	writeCLITestFile(t, filepath.Join(root, ".agentic-ops", ".env"), "WORKSPACE_JIRA_TOKEN=workspace-token\n")

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
	t.Setenv("PERSONAL_JIRA_TOKEN", "personal-token")
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
	writeCLITestFile(t, filepath.Join(installDir, "user", "config.local.yaml"), "projects:\n  tapstate:\n    jira:\n      adapter: real\n      base_url: "+server.URL+"\n      email: personal@example.com\n      api_token_env: PERSONAL_JIRA_TOKEN\n")

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
	writeCLITestFile(t, filepath.Join(userDir, "config.local.yaml"), "projects:\n  tapstate:\n    jira:\n      adapter: real\n      base_url: "+server.URL+"\n      email: dotenv@example.com\n      api_token_env: PERSONAL_JIRA_TOKEN\n")
	writeCLITestFile(t, filepath.Join(userDir, ".env"), "PERSONAL_JIRA_TOKEN=dotenv-token\n")

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

func TestListTasksGuidesJiraTokenWhenTokenEnvMissing(t *testing.T) {
	root := t.TempDir()
	installDir := t.TempDir()
	t.Setenv("AGENTIC_OPS_WORKSPACE_ROOT", root)
	t.Setenv("AGENTIC_OPS_HOME", installDir)
	userDir := filepath.Join(installDir, "user")
	writeCLITestFile(t, filepath.Join(userDir, "config.local.yaml"), "projects:\n  tapstate:\n    jira:\n      adapter: real\n      base_url: https://jira.example.test\n      email: personal@example.com\n      api_token_env: PERSONAL_JIRA_TOKEN\n")

	var stdout bytes.Buffer
	var stderr bytes.Buffer
	code := Run([]string{"list-tasks", "--workspace", "tapstate"}, &stdout, &stderr)
	if code != 1 {
		t.Fatalf("code = %d stdout = %s stderr = %s", code, stdout.String(), stderr.String())
	}
	assertJSONField(t, stdout.String(), "operation", "list_tasks")
	assertJSONField(t, stdout.String(), "code", "jira_adapter_config_failed")
	assertJSONField(t, stdout.String(), "jira_token_env", "PERSONAL_JIRA_TOKEN")
	assertJSONField(t, stdout.String(), "jira_token_env_has_value", false)
	assertJSONField(t, stdout.String(), "jira_env_file", filepath.Join(userDir, ".env"))
	assertJSONField(t, stdout.String(), "jira_config_source", filepath.Join(userDir, "config.local.yaml"))
	assertJSONField(t, stdout.String(), "jira_token_help_url", "https://id.atlassian.com/manage-profile/security/api-tokens")
	if !strings.Contains(stdout.String(), ".env") || !strings.Contains(stdout.String(), "PERSONAL_JIRA_TOKEN=") {
		t.Fatalf("stdout missing token setup guidance: %s", stdout.String())
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
	assertJSONField(t, stdout.String(), "next_action", "proceed")
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
	assertJSONField(t, stdout.String(), "next_action", "ask_owner")
}

func TestResumeTakeoverReturnsRunIDAndNextAction(t *testing.T) {
	root := t.TempDir()
	t.Setenv("AGENTIC_OPS_WORKSPACE_ROOT", root)
	writeCLITestFile(t, filepath.Join(root, ".agentic-ops", "feedback", "events.ndjson"), `{"timestamp":"2026-07-21T10:30:12Z","workspace":"tapstate","run_id":"run-1","issue_key":"TAP-123","operation":"takeover_task","task_type":"task_takeover","current_stage":"takeover_started","next_action":"proceed","agent_id":"agentic-cli-local-agent","current_agent_id":"agentic-cli-local-agent","task_class":"technical_task","process_id":"development_change_v1","ok":true,"gate":"takeover_task","gate_status":"passed"}
`)
	var stdout bytes.Buffer
	var stderr bytes.Buffer
	code := Run([]string{"resume-takeover", "--workspace", "tapstate", "--run-id", "run-1"}, &stdout, &stderr)
	if code != 0 {
		t.Fatalf("code = %d", code)
	}
	assertJSONField(t, stdout.String(), "operation", "resume_takeover")
	assertJSONField(t, stdout.String(), "run_id", "run-1")
	assertJSONField(t, stdout.String(), "issue_key", "TAP-123")
	assertJSONField(t, stdout.String(), "previous_stage", "takeover_started")
	assertJSONField(t, stdout.String(), "current_stage", "takeover_resumed")
	assertJSONField(t, stdout.String(), "next_action", "continue_development")
}

func TestResumeTakeoverRejectsMissingRun(t *testing.T) {
	root := t.TempDir()
	t.Setenv("AGENTIC_OPS_WORKSPACE_ROOT", root)
	writeCLITestFile(t, filepath.Join(root, ".agentic-ops", "feedback", "events.ndjson"), `{"timestamp":"2026-07-21T10:30:12Z","workspace":"tapstate","run_id":"other-run","issue_key":"TAP-123","operation":"takeover_task","task_type":"task_takeover","current_stage":"takeover_started","next_action":"proceed","agent_id":"agentic-cli-local-agent","current_agent_id":"agentic-cli-local-agent","task_class":"technical_task","process_id":"development_change_v1","ok":true,"gate":"takeover_task","gate_status":"passed"}
`)
	var stdout bytes.Buffer
	var stderr bytes.Buffer
	code := Run([]string{"resume-takeover", "--workspace", "tapstate", "--run-id", "run-1"}, &stdout, &stderr)
	if code != 1 {
		t.Fatalf("code = %d stdout = %s stderr = %s", code, stdout.String(), stderr.String())
	}
	assertJSONField(t, stdout.String(), "operation", "resume_takeover")
	assertJSONField(t, stdout.String(), "code", "run_not_found")
	assertJSONField(t, stdout.String(), "next_action", "ask_owner")
}

func TestResumeTakeoverRejectsWorkspaceMismatch(t *testing.T) {
	root := t.TempDir()
	t.Setenv("AGENTIC_OPS_WORKSPACE_ROOT", root)
	writeCLITestFile(t, filepath.Join(root, ".agentic-ops", "feedback", "events.ndjson"), `{"timestamp":"2026-07-21T10:30:12Z","workspace":"other","run_id":"run-1","issue_key":"TAP-123","operation":"takeover_task","task_type":"task_takeover","current_stage":"takeover_started","next_action":"proceed","agent_id":"agentic-cli-local-agent","current_agent_id":"agentic-cli-local-agent","task_class":"technical_task","process_id":"development_change_v1","ok":true,"gate":"takeover_task","gate_status":"passed"}
`)
	var stdout bytes.Buffer
	var stderr bytes.Buffer
	code := Run([]string{"resume-takeover", "--workspace", "tapstate", "--run-id", "run-1"}, &stdout, &stderr)
	if code != 1 {
		t.Fatalf("code = %d stdout = %s stderr = %s", code, stdout.String(), stderr.String())
	}
	assertJSONField(t, stdout.String(), "operation", "resume_takeover")
	assertJSONField(t, stdout.String(), "code", "workspace_mismatch")
	assertJSONField(t, stdout.String(), "next_action", "ask_owner")
}

func TestResumeTakeoverRejectsIncompleteLocalState(t *testing.T) {
	root := t.TempDir()
	t.Setenv("AGENTIC_OPS_WORKSPACE_ROOT", root)
	writeCLITestFile(t, filepath.Join(root, ".agentic-ops", "feedback", "events.ndjson"), `{"timestamp":"2026-07-21T10:30:12Z","workspace":"tapstate","run_id":"run-1","issue_key":"TAP-123","operation":"takeover_task","task_type":"task_takeover","current_stage":"takeover_started","next_action":"proceed","agent_id":"agentic-cli-local-agent","current_agent_id":"agentic-cli-local-agent","ok":true,"gate":"takeover_task","gate_status":"passed"}
`)
	var stdout bytes.Buffer
	var stderr bytes.Buffer
	code := Run([]string{"resume-takeover", "--workspace", "tapstate", "--run-id", "run-1"}, &stdout, &stderr)
	if code != 1 {
		t.Fatalf("code = %d stdout = %s stderr = %s", code, stdout.String(), stderr.String())
	}
	assertJSONField(t, stdout.String(), "operation", "resume_takeover")
	assertJSONField(t, stdout.String(), "code", "local_state_mismatch")
	assertJSONField(t, stdout.String(), "next_action", "ask_owner")
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
	assertJSONField(t, stdout.String(), "next_action", "ask_owner")
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
