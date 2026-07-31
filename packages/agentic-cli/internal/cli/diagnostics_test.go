package cli

import (
	"bytes"
	"context"
	"github.com/tapstate/agentic-ops/packages/agentic-cli/internal/clihandlers"
	"os"
	"path/filepath"
	"runtime"
	"testing"
)

func TestPreflightOutputsEnvironmentChecks(t *testing.T) {
	withCommandAvailabilityForTest(t, func(name string) bool {
		return name == "git" || name == "gh"
	})
	root := t.TempDir()
	installDir := t.TempDir()
	sourceRoot, err := os.Getwd()
	if err != nil {
		t.Fatalf("Getwd error = %v", err)
	}
	writeCompleteWorkspaceState(t, root, "tapstate", sourceRoot)
	t.Setenv("AGENTIC_OPS_WORKSPACE_ROOT", root)
	t.Setenv("AGENTIC_OPS_HOME", installDir)
	t.Setenv("AGENTIC_OPS_JIRA_ADAPTER", "fake")
	var stdout bytes.Buffer
	var stderr bytes.Buffer
	code := Run([]string{"preflight", "--workspace", "tapstate"}, &stdout, &stderr)
	if code != 0 {
		t.Fatalf("code = %d", code)
	}
	assertJSONField(t, stdout.String(), "operation", "preflight")
	assertJSONField(t, stdout.String(), "workspace", "tapstate")
	assertJSONField(t, stdout.String(), "install_dir", installDir)
	assertJSONField(t, stdout.String(), "os", runtime.GOOS)
	assertJSONField(t, stdout.String(), "arch", runtime.GOARCH)
	assertJSONField(t, stdout.String(), "version", "SRC-source")
	assertJSONField(t, stdout.String(), "status", "ok")
	assertNestedJSONField(t, stdout.String(), []string{"checks", "git", "status"}, "ok")
	assertNestedJSONField(t, stdout.String(), []string{"checks", "github_cli", "status"}, "ok")
	assertNestedJSONField(t, stdout.String(), []string{"checks", "github_auth", "status"}, "skipped")
	assertNestedJSONField(t, stdout.String(), []string{"checks", "profile", "status"}, "ok")
	assertNestedJSONField(t, stdout.String(), []string{"checks", "workspace", "status"}, "ok")
	assertNestedJSONField(t, stdout.String(), []string{"checks", "current_directory", "status"}, "ok")
	assertJSONField(t, stdout.String(), "agentic_next_action", "list_tasks")
}

func TestPreflightReportsMissingGit(t *testing.T) {
	withCommandAvailabilityForTest(t, func(name string) bool {
		return name == "gh"
	})
	root := t.TempDir()
	sourceRoot, err := os.Getwd()
	if err != nil {
		t.Fatalf("Getwd error = %v", err)
	}
	writeCompleteWorkspaceState(t, root, "tapstate", sourceRoot)
	t.Setenv("AGENTIC_OPS_WORKSPACE_ROOT", root)
	t.Setenv("AGENTIC_OPS_JIRA_ADAPTER", "fake")
	var stdout bytes.Buffer
	var stderr bytes.Buffer
	code := Run([]string{"preflight", "--workspace", "tapstate"}, &stdout, &stderr)
	if code != 0 {
		t.Fatalf("code = %d stdout = %s stderr = %s", code, stdout.String(), stderr.String())
	}
	assertJSONField(t, stdout.String(), "status", "failed")
	assertNestedJSONField(t, stdout.String(), []string{"checks", "git", "status"}, "failed")
	assertJSONField(t, stdout.String(), "agentic_next_action", "fix_environment")
}

func TestPreflightReportsProfileFailure(t *testing.T) {
	withCommandAvailabilityForTest(t, func(name string) bool {
		return true
	})
	var stdout bytes.Buffer
	var stderr bytes.Buffer
	code := Run([]string{"preflight", "--workspace", "missing-workspace"}, &stdout, &stderr)
	if code != 0 {
		t.Fatalf("code = %d stdout = %s stderr = %s", code, stdout.String(), stderr.String())
	}
	assertJSONField(t, stdout.String(), "status", "failed")
	assertNestedJSONField(t, stdout.String(), []string{"checks", "profile", "status"}, "failed")
	assertJSONField(t, stdout.String(), "agentic_next_action", "workspace_init")
}

func TestPreflightReportsCurrentDirectoryMismatch(t *testing.T) {
	withCommandAvailabilityForTest(t, func(name string) bool {
		return true
	})
	root := t.TempDir()
	writeCompleteWorkspaceState(t, root, "tapstate", filepath.Join(root, "src"))
	t.Setenv("AGENTIC_OPS_WORKSPACE_ROOT", root)
	t.Setenv("AGENTIC_OPS_JIRA_ADAPTER", "fake")
	var stdout bytes.Buffer
	var stderr bytes.Buffer
	code := Run([]string{"preflight", "--workspace", "tapstate"}, &stdout, &stderr)
	if code != 0 {
		t.Fatalf("code = %d stdout = %s stderr = %s", code, stdout.String(), stderr.String())
	}
	assertJSONField(t, stdout.String(), "status", "failed")
	assertNestedJSONField(t, stdout.String(), []string{"checks", "current_directory", "status"}, "failed")
	assertJSONField(t, stdout.String(), "agentic_next_action", "fix_environment")
}

func TestPreflightReportsMissingJiraTokenEnv(t *testing.T) {
	withCommandAvailabilityForTest(t, func(name string) bool {
		return true
	})
	root := t.TempDir()
	installDir := t.TempDir()
	t.Setenv("AGENTIC_OPS_WORKSPACE_ROOT", root)
	t.Setenv("AGENTIC_OPS_HOME", installDir)
	sourceRoot, err := os.Getwd()
	if err != nil {
		t.Fatalf("Getwd error = %v", err)
	}
	writeCompleteWorkspaceState(t, root, "tapstate", sourceRoot)
	writeCLITestFile(t, filepath.Join(installDir, "user", "config.local.yaml"), "projects:\n  tapstate:\n    jira:\n      adapter: real\n      base_url: https://jira.example.test\n      email: lead@example.com\n")

	var stdout bytes.Buffer
	var stderr bytes.Buffer
	code := Run([]string{"preflight", "--workspace", "tapstate"}, &stdout, &stderr)
	if code != 0 {
		t.Fatalf("code = %d stdout = %s stderr = %s", code, stdout.String(), stderr.String())
	}
	assertJSONField(t, stdout.String(), "status", "failed")
	assertNestedJSONField(t, stdout.String(), []string{"checks", "jira_config", "status"}, "failed")
	assertNestedJSONField(t, stdout.String(), []string{"checks", "jira_config", "message"}, "Jira API token AGENTIC_OPS_JIRA_API_TOKEN is not configured in process env or user/.env")
	assertJSONField(t, stdout.String(), "jira_token_env", "AGENTIC_OPS_JIRA_API_TOKEN")
	assertJSONField(t, stdout.String(), "jira_token_env_has_value", false)
	assertJSONField(t, stdout.String(), "jira_env_file", filepath.Join(installDir, "user", ".env"))
	assertJSONField(t, stdout.String(), "jira_token_help_url", "https://id.atlassian.com/manage-profile/security/api-tokens")
	assertJSONField(t, stdout.String(), "agentic_next_action", "set_jira_api_token")
}

func TestPreflightRejectsIncompleteWorkspace(t *testing.T) {
	withCommandAvailabilityForTest(t, func(name string) bool {
		return true
	})
	root := t.TempDir()
	installDir := t.TempDir()
	t.Setenv("AGENTIC_OPS_WORKSPACE_ROOT", root)
	t.Setenv("AGENTIC_OPS_HOME", installDir)
	writeCLITestFile(t, filepath.Join(root, ".agentic-ops", "profile.local.yaml"), "workspace: tapdata\nlocal:\n  source_root: "+filepath.Join(root, "repos", "tapdata")+"\n")

	var stdout bytes.Buffer
	var stderr bytes.Buffer
	code := Run([]string{"preflight", "--workspace", "tapdata"}, &stdout, &stderr)
	if code != 0 {
		t.Fatalf("code = %d stdout = %s stderr = %s", code, stdout.String(), stderr.String())
	}
	assertJSONField(t, stdout.String(), "status", "failed")
	assertNestedJSONField(t, stdout.String(), []string{"checks", "workspace", "status"}, "failed")
	assertNestedJSONField(t, stdout.String(), []string{"checks", "workspace", "code"}, "workspace_initialization_incomplete")
	assertJSONField(t, stdout.String(), "agentic_next_action", "workspace_init")
}

func TestPreflightRejectsWorkspaceWithMissingSourceRoot(t *testing.T) {
	withCommandAvailabilityForTest(t, func(name string) bool {
		return true
	})
	root := t.TempDir()
	installDir := t.TempDir()
	sourceRoot := filepath.Join(root, "repos", "tapdata")
	writeCompleteWorkspaceState(t, root, "tapdata", sourceRoot)
	if err := os.RemoveAll(sourceRoot); err != nil {
		t.Fatalf("RemoveAll source root error = %v", err)
	}
	t.Setenv("AGENTIC_OPS_WORKSPACE_ROOT", root)
	t.Setenv("AGENTIC_OPS_HOME", installDir)

	var stdout bytes.Buffer
	var stderr bytes.Buffer
	code := Run([]string{"preflight", "--workspace", "tapdata"}, &stdout, &stderr)
	if code != 0 {
		t.Fatalf("code = %d stdout = %s stderr = %s", code, stdout.String(), stderr.String())
	}
	assertJSONField(t, stdout.String(), "status", "failed")
	assertNestedJSONField(t, stdout.String(), []string{"checks", "workspace", "code"}, "workspace_initialization_incomplete")
	assertJSONField(t, stdout.String(), "agentic_next_action", "workspace_init")
}

func TestDoctorOutputsLocalDiagnosticChecks(t *testing.T) {
	root := t.TempDir()
	t.Setenv("AGENTIC_OPS_WORKSPACE_ROOT", root)
	if err := os.MkdirAll(filepath.Join(root, "src"), 0o755); err != nil {
		t.Fatalf("MkdirAll source root error = %v", err)
	}
	installDir := t.TempDir()
	var stdout bytes.Buffer
	var stderr bytes.Buffer
	code := Run([]string{"doctor", "--workspace", "tapstate", "--install-dir", installDir}, &stdout, &stderr)
	if code != 0 {
		t.Fatalf("code = %d stdout = %s stderr = %s", code, stdout.String(), stderr.String())
	}
	assertJSONField(t, stdout.String(), "operation", "doctor")
	assertJSONField(t, stdout.String(), "workspace", "tapstate")
	assertJSONField(t, stdout.String(), "version", "SRC-source")
	assertJSONField(t, stdout.String(), "agentic_next_action", "continue")
	assertJSONField(t, stdout.String(), "status", "ok")
	assertNestedJSONField(t, stdout.String(), []string{"checks", "profile", "status"}, "ok")
	assertNestedJSONField(t, stdout.String(), []string{"checks", "policy", "status"}, "ok")
	assertNestedJSONField(t, stdout.String(), []string{"checks", "jira_adapter", "status"}, "ok")
	assertNestedJSONField(t, stdout.String(), []string{"checks", "github", "status"}, "skipped")
	assertNestedJSONField(t, stdout.String(), []string{"checks", "workspace", "status"}, "ok")
	assertNestedJSONField(t, stdout.String(), []string{"checks", "contracts", "status"}, "ok")
	assertNestedJSONField(t, stdout.String(), []string{"checks", "current", "status"}, "skipped")
	assertNestedJSONField(t, stdout.String(), []string{"checks", "local_paths", "status"}, "ok")
}

func TestDoctorReportsCurrentVersionMismatch(t *testing.T) {
	root := t.TempDir()
	t.Setenv("AGENTIC_OPS_WORKSPACE_ROOT", root)
	if err := os.MkdirAll(filepath.Join(root, "src"), 0o755); err != nil {
		t.Fatalf("MkdirAll source root error = %v", err)
	}
	installDir := t.TempDir()
	writeCLITestFile(t, filepath.Join(installDir, "current.json"), `{
  "agentic_cli_version": "RES-v0.0.1-old",
  "asset_version": "RES-v0.0.1-old"
}
`)
	var stdout bytes.Buffer
	var stderr bytes.Buffer
	code := Run([]string{"doctor", "--workspace", "tapstate", "--install-dir", installDir}, &stdout, &stderr)
	if code != 0 {
		t.Fatalf("code = %d stdout = %s stderr = %s", code, stdout.String(), stderr.String())
	}
	assertJSONField(t, stdout.String(), "status", "failed")
	assertNestedJSONField(t, stdout.String(), []string{"checks", "current", "status"}, "failed")
	assertJSONField(t, stdout.String(), "agentic_next_action", "fix_environment")
}

func TestDoctorReportsMissingSourceRoot(t *testing.T) {
	root := t.TempDir()
	t.Setenv("AGENTIC_OPS_WORKSPACE_ROOT", root)
	var stdout bytes.Buffer
	var stderr bytes.Buffer
	code := Run([]string{"doctor", "--workspace", "tapstate"}, &stdout, &stderr)
	if code != 0 {
		t.Fatalf("code = %d stdout = %s stderr = %s", code, stdout.String(), stderr.String())
	}
	assertJSONField(t, stdout.String(), "status", "failed")
	assertNestedJSONField(t, stdout.String(), []string{"checks", "local_paths", "status"}, "failed")
	assertJSONField(t, stdout.String(), "agentic_next_action", "fix_environment")
}

func TestDoctorChecksRealJiraAdapterWhenRequested(t *testing.T) {
	root := t.TempDir()
	t.Setenv("AGENTIC_OPS_WORKSPACE_ROOT", root)
	if err := os.MkdirAll(filepath.Join(root, "src"), 0o755); err != nil {
		t.Fatalf("MkdirAll source root error = %v", err)
	}
	withJiraClientForTest(t, clihandlers.JiraClientSelection{Client: &recordingJiraClient{issue: realModeIssue()}, Mode: "real"})

	var stdout bytes.Buffer
	var stderr bytes.Buffer
	code := Run([]string{"doctor", "--workspace", "tapstate", "--check-real-jira"}, &stdout, &stderr)
	if code != 0 {
		t.Fatalf("code = %d stdout = %s stderr = %s", code, stdout.String(), stderr.String())
	}
	assertJSONField(t, stdout.String(), "operation", "doctor")
	assertJSONField(t, stdout.String(), "status", "ok")
	assertNestedJSONField(t, stdout.String(), []string{"checks", "jira_adapter", "status"}, "ok")
	assertNestedJSONField(t, stdout.String(), []string{"checks", "jira_adapter", "message"}, "real adapter authenticated as current-user")
}

func TestDoctorFailsRealJiraCheckWhenAdapterIsNotReal(t *testing.T) {
	withJiraClientForTest(t, clihandlers.JiraClientSelection{Client: &recordingJiraClient{issue: realModeIssue()}, Mode: "fake"})

	var stdout bytes.Buffer
	var stderr bytes.Buffer
	code := Run([]string{"doctor", "--workspace", "tapstate", "--check-real-jira"}, &stdout, &stderr)
	if code != 0 {
		t.Fatalf("code = %d stdout = %s stderr = %s", code, stdout.String(), stderr.String())
	}
	assertJSONField(t, stdout.String(), "operation", "doctor")
	assertJSONField(t, stdout.String(), "status", "failed")
	assertJSONField(t, stdout.String(), "agentic_next_action", "fix_environment")
	assertNestedJSONField(t, stdout.String(), []string{"checks", "jira_adapter", "status"}, "failed")
}

func TestDoctorChecksGitHubAuthWhenRequested(t *testing.T) {
	root := t.TempDir()
	t.Setenv("AGENTIC_OPS_WORKSPACE_ROOT", root)
	if err := os.MkdirAll(filepath.Join(root, "src"), 0o755); err != nil {
		t.Fatalf("MkdirAll source root error = %v", err)
	}
	called := false
	restore := clihandlers.SetRunGitHubAuthStatusForTest(func(ctx context.Context) error {
		called = true
		return nil
	})
	t.Cleanup(restore)

	var stdout bytes.Buffer
	var stderr bytes.Buffer
	code := Run([]string{"doctor", "--workspace", "tapstate", "--check-github"}, &stdout, &stderr)
	if code != 0 {
		t.Fatalf("code = %d stdout = %s stderr = %s", code, stdout.String(), stderr.String())
	}
	if !called {
		t.Fatalf("runGitHubAuthStatus was not called")
	}
	assertJSONField(t, stdout.String(), "operation", "doctor")
	assertNestedJSONField(t, stdout.String(), []string{"checks", "github", "status"}, "ok")
}

func TestPreflightInfersWorkspaceFromAgentConfig(t *testing.T) {
	withCommandAvailabilityForTest(t, func(name string) bool {
		return true
	})
	root := t.TempDir()
	t.Setenv("AGENTIC_OPS_WORKSPACE_ROOT", root)
	t.Setenv("AGENTIC_OPS_HOME", t.TempDir())
	sourceRoot, err := os.Getwd()
	if err != nil {
		t.Fatalf("Getwd error = %v", err)
	}
	Run([]string{"workspace", "init", "--project", "tapdata", "--jira-user", "lead@example.com", "--source-root", sourceRoot}, &bytes.Buffer{}, &bytes.Buffer{})
	var stdout bytes.Buffer
	var stderr bytes.Buffer
	code := Run([]string{"preflight"}, &stdout, &stderr)
	if code != 0 {
		t.Fatalf("code = %d stdout = %s stderr = %s", code, stdout.String(), stderr.String())
	}
	assertJSONField(t, stdout.String(), "operation", "preflight")
	assertJSONField(t, stdout.String(), "workspace", "tapdata")
}
