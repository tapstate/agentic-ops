package cli

import (
	"bytes"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestWorkspaceInitOutputsNextAction(t *testing.T) {
	root := t.TempDir()
	t.Setenv("AGENTIC_OPS_WORKSPACE_ROOT", root)
	var stdout bytes.Buffer
	var stderr bytes.Buffer
	code := Run([]string{"workspace", "init", "--project", "tapstate", "--jira-user", "dev@example.com"}, &stdout, &stderr)
	if code != 0 {
		t.Fatalf("code = %d", code)
	}
	assertJSONField(t, stdout.String(), "operation", "workspace_init")
	assertJSONField(t, stdout.String(), "workspace", "tapstate")
	assertJSONField(t, stdout.String(), "jira_user", "dev@example.com")
	assertJSONField(t, stdout.String(), "jira_project", "TAP")
	assertJSONField(t, stdout.String(), "next_action", "init_agent_capability")
	for _, dir := range []string{"runs", "run-logs", "feedback"} {
		if _, err := os.Stat(filepath.Join(root, ".agentic-ops", dir)); err != nil {
			t.Fatalf("workspace dir %s was not created: %v", dir, err)
		}
	}
	if !strings.Contains(stdout.String(), `"run_logs_dir":"`) {
		t.Fatalf("stdout missing run_logs_dir: %s", stdout.String())
	}
}

func TestWorkspaceInitMaterializesWorkspaceProfile(t *testing.T) {
	root := t.TempDir()
	t.Setenv("AGENTIC_OPS_WORKSPACE_ROOT", root)
	var stdout bytes.Buffer
	var stderr bytes.Buffer
	code := Run([]string{"workspace", "init", "--project", "tapdata", "--jira-user", "lead@example.com"}, &stdout, &stderr)
	if code != 0 {
		t.Fatalf("code = %d stdout = %s stderr = %s", code, stdout.String(), stderr.String())
	}
	profilePath := filepath.Join(root, ".agentic-ops", "profiles", "tapdata.yaml")
	data, err := os.ReadFile(profilePath)
	if err != nil {
		t.Fatalf("profile was not materialized: %v", err)
	}
	if !strings.Contains(string(data), "workspace: tapdata") || !strings.Contains(string(data), "user: lead@example.com") || !strings.Contains(string(data), "project: TAP") {
		t.Fatalf("materialized profile mismatch: %s", string(data))
	}
	assertJSONField(t, stdout.String(), "profile", profilePath)
}

func TestWorkspaceInitWritesAgentConfigForCodexActivation(t *testing.T) {
	root := t.TempDir()
	t.Setenv("AGENTIC_OPS_WORKSPACE_ROOT", root)
	var stdout bytes.Buffer
	var stderr bytes.Buffer
	code := Run([]string{"workspace", "init", "--project", "tapdata", "--jira-user", "lead@example.com"}, &stdout, &stderr)
	if code != 0 {
		t.Fatalf("code = %d stdout = %s stderr = %s", code, stdout.String(), stderr.String())
	}
	agentConfigPath := filepath.Join(root, ".agentic-ops", "agent.json")
	data, err := os.ReadFile(agentConfigPath)
	if err != nil {
		t.Fatalf("agent config was not written: %v", err)
	}
	for _, want := range []string{
		`"workspace":"tapdata"`,
		`"project":"tapdata"`,
		`"jira_user":"lead@example.com"`,
		`"jira_project":"TAP"`,
		`"agent_type":"codex"`,
		`"profile":"` + filepath.Join(root, ".agentic-ops", "profiles", "tapdata.yaml") + `"`,
	} {
		if !strings.Contains(string(data), want) {
			t.Fatalf("agent config missing %s: %s", want, string(data))
		}
	}
}

func TestWorkspaceInitWritesAgentInstructionsForCodex(t *testing.T) {
	root := t.TempDir()
	t.Setenv("AGENTIC_OPS_WORKSPACE_ROOT", root)
	var stdout bytes.Buffer
	var stderr bytes.Buffer
	code := Run([]string{"workspace", "init", "--project", "tapdata", "--jira-user", "lead@example.com"}, &stdout, &stderr)
	if code != 0 {
		t.Fatalf("code = %d stdout = %s stderr = %s", code, stdout.String(), stderr.String())
	}
	instructionsPath := filepath.Join(root, "AGENTS.md")
	data, err := os.ReadFile(instructionsPath)
	if err != nil {
		t.Fatalf("agent instructions were not written: %v", err)
	}
	for _, want := range []string{
		"AgenticOps",
		"project: tapdata",
		"jira_project: TAP",
		"按 ~/.agentic-ops/agent-guides.md 启用 AgenticOps。",
		"$HOME/.agentic-ops/agent-guides.md",
		"Do not rely on private wiki",
		"$HOME/.agentic-ops/install-resources/basic/ai-assets/README.md",
		"agentic-cli agent init",
		"agentic-cli preflight",
		"agentic-cli list-tasks",
		".agentic-ops/agent.json",
	} {
		if !strings.Contains(string(data), want) {
			t.Fatalf("agent instructions missing %s: %s", want, string(data))
		}
	}
	legacyActivationPhrase := "启用 AgenticOps " + "工作模式。"
	if strings.Contains(string(data), legacyActivationPhrase) {
		t.Fatalf("agent instructions should not include legacy activation phrase: %s", string(data))
	}
	assertJSONField(t, stdout.String(), "agent_instructions", instructionsPath)
}

func TestWorkspaceInitPreservesExistingAgentInstructions(t *testing.T) {
	root := t.TempDir()
	t.Setenv("AGENTIC_OPS_WORKSPACE_ROOT", root)
	instructionsPath := filepath.Join(root, "AGENTS.md")
	if err := os.WriteFile(instructionsPath, []byte("# Existing instructions\n\nKeep this line.\n"), 0o644); err != nil {
		t.Fatalf("write existing instructions error = %v", err)
	}
	Run([]string{"workspace", "init", "--project", "tapdata", "--jira-user", "lead@example.com"}, &bytes.Buffer{}, &bytes.Buffer{})
	Run([]string{"workspace", "init", "--project", "tapdata", "--jira-user", "lead@example.com"}, &bytes.Buffer{}, &bytes.Buffer{})
	data, err := os.ReadFile(instructionsPath)
	if err != nil {
		t.Fatalf("read instructions error = %v", err)
	}
	content := string(data)
	if !strings.Contains(content, "Keep this line.") {
		t.Fatalf("existing instructions were not preserved: %s", content)
	}
	if strings.Count(content, "BEGIN AGENTICOPS MANAGED BLOCK") != 1 || strings.Count(content, "END AGENTICOPS MANAGED BLOCK") != 1 {
		t.Fatalf("managed block was not idempotent: %s", content)
	}
}

func TestAgentInitInfersWorkspaceFromAgentConfig(t *testing.T) {
	root := t.TempDir()
	t.Setenv("AGENTIC_OPS_WORKSPACE_ROOT", root)
	Run([]string{"workspace", "init", "--project", "tapdata", "--jira-user", "lead@example.com"}, &bytes.Buffer{}, &bytes.Buffer{})
	var stdout bytes.Buffer
	var stderr bytes.Buffer
	code := Run([]string{"agent", "init"}, &stdout, &stderr)
	if code != 0 {
		t.Fatalf("code = %d stdout = %s stderr = %s", code, stdout.String(), stderr.String())
	}
	assertJSONField(t, stdout.String(), "operation", "agent_init")
	assertJSONField(t, stdout.String(), "workspace", "tapdata")
}

func TestTaskCommandsInferWorkspaceFromAgentConfig(t *testing.T) {
	root := t.TempDir()
	t.Setenv("AGENTIC_OPS_WORKSPACE_ROOT", root)
	Run([]string{"workspace", "init", "--project", "tapstate", "--jira-user", "dev@example.com"}, &bytes.Buffer{}, &bytes.Buffer{})

	var listStdout bytes.Buffer
	var listStderr bytes.Buffer
	listCode := Run([]string{"list-tasks"}, &listStdout, &listStderr)
	if listCode != 0 {
		t.Fatalf("list code = %d stdout = %s stderr = %s", listCode, listStdout.String(), listStderr.String())
	}
	assertJSONField(t, listStdout.String(), "workspace", "tapstate")

	var takeoverStdout bytes.Buffer
	var takeoverStderr bytes.Buffer
	takeoverCode := Run([]string{"takeover-task", "TAP-123"}, &takeoverStdout, &takeoverStderr)
	if takeoverCode != 0 {
		t.Fatalf("takeover code = %d stdout = %s stderr = %s", takeoverCode, takeoverStdout.String(), takeoverStderr.String())
	}
	assertJSONField(t, takeoverStdout.String(), "workspace", "tapstate")
	assertJSONField(t, takeoverStdout.String(), "issue_key", "TAP-123")
}

func TestWorkspaceInitRejectsMismatchedJiraProjectOverride(t *testing.T) {
	root := t.TempDir()
	t.Setenv("AGENTIC_OPS_WORKSPACE_ROOT", root)
	var stdout bytes.Buffer
	var stderr bytes.Buffer
	code := Run([]string{"workspace", "init", "--project", "tapdata", "--jira-user", "lead@example.com", "--jira-project", "OTHER"}, &stdout, &stderr)
	if code != 1 {
		t.Fatalf("code = %d stdout = %s stderr = %s", code, stdout.String(), stderr.String())
	}
	assertJSONField(t, stdout.String(), "code", "workspace_profile_failed")
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
	if !strings.Contains(stdout.String(), `"asset_entry":"$HOME/.agentic-ops/install-resources/basic/ai-assets/README.md"`) {
		t.Fatalf("stdout missing local asset entry: %s", stdout.String())
	}
	if !strings.Contains(stdout.String(), `"guide_entry":"$HOME/.agentic-ops/agent-guides.md"`) {
		t.Fatalf("stdout missing global guide entry: %s", stdout.String())
	}
	if !strings.Contains(stdout.String(), `"activation_phrase":"按 ~/.agentic-ops/agent-guides.md 启用 AgenticOps。"`) {
		t.Fatalf("stdout missing activation phrase: %s", stdout.String())
	}
	if !strings.Contains(stdout.String(), `"memory_dependency":false`) {
		t.Fatalf("stdout should declare no private wiki dependency: %s", stdout.String())
	}
	if !strings.Contains(stdout.String(), `"real_jira_write"`) || !strings.Contains(stdout.String(), `"create_pr"`) {
		t.Fatalf("stdout missing human gates: %s", stdout.String())
	}
}
