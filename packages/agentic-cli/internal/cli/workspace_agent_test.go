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
	installDir := t.TempDir()
	t.Setenv("AGENTIC_OPS_WORKSPACE_ROOT", root)
	t.Setenv("AGENTIC_OPS_HOME", installDir)
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
	assertJSONField(t, stdout.String(), "jira_config_status", "needs_configuration")
	assertJSONField(t, stdout.String(), "jira_config_path", filepath.Join(installDir, "user", "projects", "tapstate", "jira.local.yaml"))
	if !strings.Contains(stdout.String(), "--jira-base-url") {
		t.Fatalf("stdout missing jira config guidance: %s", stdout.String())
	}
	for _, dir := range []string{"runs", "run-logs", "feedback"} {
		if _, err := os.Stat(filepath.Join(root, ".agentic-ops", dir)); err != nil {
			t.Fatalf("workspace dir %s was not created: %v", dir, err)
		}
	}
	if _, err := os.Stat(filepath.Join(installDir, "user", "projects", "tapstate", "jira.local.yaml")); !os.IsNotExist(err) {
		t.Fatalf("jira config should not be written without jira base URL: %v", err)
	}
	if !strings.Contains(stdout.String(), `"run_logs_dir":"`) {
		t.Fatalf("stdout missing run_logs_dir: %s", stdout.String())
	}
}

func TestWorkspaceInitWritesPersonalProjectJiraConfigWhenBaseURLProvided(t *testing.T) {
	root := t.TempDir()
	installDir := t.TempDir()
	t.Setenv("AGENTIC_OPS_WORKSPACE_ROOT", root)
	t.Setenv("AGENTIC_OPS_HOME", installDir)
	var stdout bytes.Buffer
	var stderr bytes.Buffer
	code := Run([]string{"workspace", "init", "--project", "tapdata", "--jira-user", "lead@example.com", "--jira-base-url", "https://jira.example.test", "--jira-token-env", "TAPDATA_JIRA_TOKEN"}, &stdout, &stderr)
	if code != 0 {
		t.Fatalf("code = %d stdout = %s stderr = %s", code, stdout.String(), stderr.String())
	}
	configPath := filepath.Join(installDir, "user", "projects", "tapdata", "jira.local.yaml")
	assertJSONField(t, stdout.String(), "jira_config_status", "needs_token_env")
	assertJSONField(t, stdout.String(), "jira_config_path", configPath)
	assertJSONField(t, stdout.String(), "jira_token_env", "TAPDATA_JIRA_TOKEN")
	data, err := os.ReadFile(configPath)
	if err != nil {
		t.Fatalf("jira config was not written: %v", err)
	}
	for _, want := range []string{
		"adapter: real",
		"base_url: https://jira.example.test",
		"email: lead@example.com",
		"api_token_env: TAPDATA_JIRA_TOKEN",
	} {
		if !strings.Contains(string(data), want) {
			t.Fatalf("jira config missing %s: %s", want, string(data))
		}
	}
	if strings.Contains(string(data), "api_token:") {
		t.Fatalf("jira config must not write raw token: %s", string(data))
	}
}

func TestWorkspaceInitInteractivePromptsForMissingJiraConfig(t *testing.T) {
	root := t.TempDir()
	installDir := t.TempDir()
	t.Setenv("AGENTIC_OPS_WORKSPACE_ROOT", root)
	t.Setenv("AGENTIC_OPS_HOME", installDir)

	var stdout bytes.Buffer
	var stderr bytes.Buffer
	input := strings.NewReader("lead@example.com\nhttps://jira.example.test\nTAPDATA_JIRA_TOKEN\n")
	code := RunWithIO([]string{"workspace", "init", "--project", "tapdata", "--interactive"}, input, &stdout, &stderr, true)
	if code != 0 {
		t.Fatalf("code = %d stdout = %s stderr = %s", code, stdout.String(), stderr.String())
	}
	assertJSONField(t, stdout.String(), "jira_user", "lead@example.com")
	assertJSONField(t, stdout.String(), "jira_config_status", "needs_token_env")
	configPath := filepath.Join(installDir, "user", "projects", "tapdata", "jira.local.yaml")
	assertJSONField(t, stdout.String(), "jira_config_path", configPath)
	if !strings.Contains(stderr.String(), "Jira user") || !strings.Contains(stderr.String(), "Jira base URL") || !strings.Contains(stderr.String(), "Jira token env") {
		t.Fatalf("stderr missing prompts: %s", stderr.String())
	}
	data, err := os.ReadFile(configPath)
	if err != nil {
		t.Fatalf("jira config was not written: %v", err)
	}
	for _, want := range []string{
		"base_url: https://jira.example.test",
		"email: lead@example.com",
		"api_token_env: TAPDATA_JIRA_TOKEN",
	} {
		if !strings.Contains(string(data), want) {
			t.Fatalf("jira config missing %s: %s", want, string(data))
		}
	}
	if strings.TrimSpace(stdout.String()) == "" || strings.Contains(stdout.String(), "Jira user") {
		t.Fatalf("stdout should only contain JSON result: %s", stdout.String())
	}
}

func TestWorkspaceInitInteractiveAcceptsExistingJiraConfigDefaults(t *testing.T) {
	root := t.TempDir()
	installDir := t.TempDir()
	t.Setenv("AGENTIC_OPS_WORKSPACE_ROOT", root)
	t.Setenv("AGENTIC_OPS_HOME", installDir)
	t.Setenv("TAPDATA_JIRA_TOKEN", "token-123")
	configPath := filepath.Join(installDir, "user", "projects", "tapdata", "jira.local.yaml")
	writeCLITestFile(t, configPath, "adapter: real\nbase_url: https://jira.example.test\nemail: existing@example.com\napi_token_env: TAPDATA_JIRA_TOKEN\n")

	var stdout bytes.Buffer
	var stderr bytes.Buffer
	input := strings.NewReader("\n\n\n")
	code := RunWithIO([]string{"workspace", "init", "--project", "tapdata", "--interactive"}, input, &stdout, &stderr, true)
	if code != 0 {
		t.Fatalf("code = %d stdout = %s stderr = %s", code, stdout.String(), stderr.String())
	}
	assertJSONField(t, stdout.String(), "jira_user", "existing@example.com")
	assertJSONField(t, stdout.String(), "jira_config_status", "configured")
	assertJSONField(t, stdout.String(), "jira_config_path", configPath)
	if !strings.Contains(stderr.String(), "[existing@example.com]") || !strings.Contains(stderr.String(), "[https://jira.example.test]") {
		t.Fatalf("stderr missing default confirmations: %s", stderr.String())
	}
}

func TestWorkspaceInitInteractiveRequiresTerminal(t *testing.T) {
	root := t.TempDir()
	t.Setenv("AGENTIC_OPS_WORKSPACE_ROOT", root)
	var stdout bytes.Buffer
	var stderr bytes.Buffer
	code := RunWithIO([]string{"workspace", "init", "--project", "tapdata", "--interactive"}, strings.NewReader(""), &stdout, &stderr, false)
	if code != 1 {
		t.Fatalf("code = %d stdout = %s stderr = %s", code, stdout.String(), stderr.String())
	}
	assertJSONField(t, stdout.String(), "operation", "workspace_init")
	assertJSONField(t, stdout.String(), "code", "interactive_terminal_required")
	if strings.TrimSpace(stderr.String()) != "" {
		t.Fatalf("stderr should be empty for non-interactive failure: %s", stderr.String())
	}
}

func TestWorkspaceInitWritesLocalProfileOverlay(t *testing.T) {
	root := t.TempDir()
	t.Setenv("AGENTIC_OPS_WORKSPACE_ROOT", root)
	var stdout bytes.Buffer
	var stderr bytes.Buffer
	code := Run([]string{"workspace", "init", "--project", "tapdata", "--jira-user", "lead@example.com"}, &stdout, &stderr)
	if code != 0 {
		t.Fatalf("code = %d stdout = %s stderr = %s", code, stdout.String(), stderr.String())
	}
	legacyProfilePath := filepath.Join(root, ".agentic-ops", "profiles", "tapdata.yaml")
	if _, err := os.Stat(legacyProfilePath); !os.IsNotExist(err) {
		t.Fatalf("legacy copied profile exists = %v, want not exist", err)
	}
	overlayPath := filepath.Join(root, ".agentic-ops", "profile.local.yaml")
	data, err := os.ReadFile(overlayPath)
	if err != nil {
		t.Fatalf("profile overlay was not written: %v", err)
	}
	if strings.Contains(string(data), "standard_process_mapping:") || strings.Contains(string(data), "task_class_mapping:") {
		t.Fatalf("overlay should only contain local differences: %s", string(data))
	}
	for _, want := range []string{
		"workspace: tapdata",
		"user: lead@example.com",
		"workspace_root: " + root,
		"source_root: " + filepath.Join(root, "repos", "tapdata"),
		"runs_dir: " + filepath.Join(root, ".agentic-ops", "runs"),
		"run_logs_dir: " + filepath.Join(root, ".agentic-ops", "run-logs"),
		"feedback_dir: " + filepath.Join(root, ".agentic-ops", "feedback"),
	} {
		if !strings.Contains(string(data), want) {
			t.Fatalf("materialized profile missing %s: %s", want, string(data))
		}
	}
	assertJSONField(t, stdout.String(), "profile_overlay", overlayPath)
	assertJSONField(t, stdout.String(), "profile_ref", "$HOME/.agentic-ops/install-resources/basic/projects/tapdata/profile.yaml")
}

func TestWorkspaceInitRequiresConfirmationBeforeReplacingExistingConfig(t *testing.T) {
	root := t.TempDir()
	t.Setenv("AGENTIC_OPS_WORKSPACE_ROOT", root)
	Run([]string{"workspace", "init", "--project", "tapdata", "--jira-user", "lead@example.com"}, &bytes.Buffer{}, &bytes.Buffer{})
	var stdout bytes.Buffer
	var stderr bytes.Buffer
	code := Run([]string{"workspace", "init", "--project", "tapdata", "--jira-user", "other@example.com"}, &stdout, &stderr)
	if code != 1 {
		t.Fatalf("code = %d stdout = %s stderr = %s", code, stdout.String(), stderr.String())
	}
	assertJSONField(t, stdout.String(), "code", "existing_config_confirmation_required")
	if !strings.Contains(stdout.String(), "--confirm-existing-config") {
		t.Fatalf("stdout missing confirmation guidance: %s", stdout.String())
	}
}

func TestWorkspaceInitCanReplaceExistingConfigAfterConfirmation(t *testing.T) {
	root := t.TempDir()
	t.Setenv("AGENTIC_OPS_WORKSPACE_ROOT", root)
	Run([]string{"workspace", "init", "--project", "tapdata", "--jira-user", "lead@example.com"}, &bytes.Buffer{}, &bytes.Buffer{})
	var stdout bytes.Buffer
	var stderr bytes.Buffer
	code := Run([]string{"workspace", "init", "--project", "tapdata", "--jira-user", "other@example.com", "--confirm-existing-config"}, &stdout, &stderr)
	if code != 0 {
		t.Fatalf("code = %d stdout = %s stderr = %s", code, stdout.String(), stderr.String())
	}
	assertJSONField(t, stdout.String(), "jira_user", "other@example.com")
	data, err := os.ReadFile(filepath.Join(root, ".agentic-ops", "profile.local.yaml"))
	if err != nil {
		t.Fatalf("read profile overlay error = %v", err)
	}
	if !strings.Contains(string(data), "user: other@example.com") {
		t.Fatalf("profile overlay was not replaced after confirmation: %s", string(data))
	}
}

func TestWorkspaceInitAcceptsConfirmedSourceRoot(t *testing.T) {
	root := t.TempDir()
	sourceRoot := filepath.Join(root, "repos", "custom-source")
	t.Setenv("AGENTIC_OPS_WORKSPACE_ROOT", root)
	var stdout bytes.Buffer
	var stderr bytes.Buffer
	code := Run([]string{"workspace", "init", "--project", "tapdata", "--jira-user", "lead@example.com", "--source-root", sourceRoot}, &stdout, &stderr)
	if code != 0 {
		t.Fatalf("code = %d stdout = %s stderr = %s", code, stdout.String(), stderr.String())
	}
	assertJSONField(t, stdout.String(), "source_root", sourceRoot)
	data, err := os.ReadFile(filepath.Join(root, ".agentic-ops", "profile.local.yaml"))
	if err != nil {
		t.Fatalf("read profile overlay error = %v", err)
	}
	if !strings.Contains(string(data), "source_root: "+sourceRoot) {
		t.Fatalf("profile overlay missing confirmed source root: %s", string(data))
	}
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
		`"profile_overlay":"` + filepath.Join(root, ".agentic-ops", "profile.local.yaml") + `"`,
		`"profile_ref":"$HOME/.agentic-ops/install-resources/basic/projects/tapdata/profile.yaml"`,
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
	if !strings.Contains(stdout.String(), `"tapdata branch-align"`) {
		t.Fatalf("agent init missing tapdata branch-align project tool: %s", stdout.String())
	}
	if !strings.Contains(stdout.String(), `"asset_resolution"`) || !strings.Contains(stdout.String(), `"workspace_overlay"`) {
		t.Fatalf("agent init missing asset resolution: %s", stdout.String())
	}
}

func TestTaskCommandsInferWorkspaceFromAgentConfig(t *testing.T) {
	root := t.TempDir()
	t.Setenv("AGENTIC_OPS_WORKSPACE_ROOT", root)
	t.Setenv("AGENTIC_OPS_JIRA_ADAPTER", "fake")
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
