package cli

import (
	"bytes"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestContractValidateOutputsIssueCount(t *testing.T) {
	var stdout bytes.Buffer
	var stderr bytes.Buffer
	code := Run([]string{"contract", "validate"}, &stdout, &stderr)
	if code != 0 {
		t.Fatalf("code = %d stdout = %s stderr = %s", code, stdout.String(), stderr.String())
	}
	assertJSONField(t, stdout.String(), "operation", "contract_validate")
	assertJSONNumber(t, stdout.String(), "issues", 0)
	assertJSONField(t, stdout.String(), "next_action", "continue")
}

func TestProfileValidateOutputsIssueCount(t *testing.T) {
	var stdout bytes.Buffer
	var stderr bytes.Buffer
	code := Run([]string{"profile", "validate", "--workspace", "tapstate"}, &stdout, &stderr)
	if code != 0 {
		t.Fatalf("code = %d stdout = %s stderr = %s", code, stdout.String(), stderr.String())
	}
	assertJSONField(t, stdout.String(), "operation", "profile_validate")
	assertJSONField(t, stdout.String(), "workspace", "tapstate")
	assertJSONNumber(t, stdout.String(), "issues", 0)
	assertJSONField(t, stdout.String(), "next_action", "continue")
}

func TestProfileResolveMergesProjectPackageAndWorkspaceOverlay(t *testing.T) {
	root := t.TempDir()
	t.Setenv("AGENTIC_OPS_WORKSPACE_ROOT", root)
	Run([]string{"workspace", "init", "--project", "tapdata", "--jira-user", "lead@example.com"}, &bytes.Buffer{}, &bytes.Buffer{})

	var stdout bytes.Buffer
	var stderr bytes.Buffer
	code := Run([]string{"profile", "resolve", "--project", "tapdata"}, &stdout, &stderr)
	if code != 0 {
		t.Fatalf("code = %d stdout = %s stderr = %s", code, stdout.String(), stderr.String())
	}
	assertJSONField(t, stdout.String(), "operation", "profile_resolve")
	assertJSONField(t, stdout.String(), "workspace", "tapdata")
	assertJSONField(t, stdout.String(), "jira_user", "lead@example.com")
	assertJSONField(t, stdout.String(), "source_root", filepath.Join(root, "repos", "tapdata"))
	if !strings.Contains(stdout.String(), `"project_package"`) || !strings.Contains(stdout.String(), `"workspace_overlay"`) {
		t.Fatalf("stdout missing source layers: %s", stdout.String())
	}
	if !strings.Contains(stdout.String(), `projects/tapdata/profile.yaml`) {
		t.Fatalf("stdout missing project profile source: %s", stdout.String())
	}
}

func TestProfileUpdateAndRollbackUseLocalProfileBackup(t *testing.T) {
	repo := t.TempDir()
	t.Chdir(repo)
	writeCLITestFile(t, filepath.Join(repo, "go.mod"), "module example.local/test\n")
	writeCLITestFile(t, filepath.Join(repo, "install-resources", "basic", "contracts", "operations", ".keep"), "")
	target := filepath.Join(repo, "install-resources", "basic", "projects", "tapstate", "profile.yaml")
	source := filepath.Join(repo, "incoming", "tapstate.yaml")
	writeCLITestFile(t, target, validCLIProfileYAML("tapstate", "TAP"))
	writeCLITestFile(t, source, validCLIProfileYAML("tapstate", "OPS"))

	var updateStdout bytes.Buffer
	var updateStderr bytes.Buffer
	updateCode := Run([]string{"profile", "update", "--workspace", "tapstate", "--source", source}, &updateStdout, &updateStderr)
	if updateCode != 0 {
		t.Fatalf("updateCode = %d stdout = %s stderr = %s", updateCode, updateStdout.String(), updateStderr.String())
	}
	assertJSONField(t, updateStdout.String(), "operation", "profile_update")
	assertJSONField(t, updateStdout.String(), "workspace", "tapstate")
	assertJSONField(t, updateStdout.String(), "next_action", "profile_validate")
	updated, err := os.ReadFile(target)
	if err != nil {
		t.Fatalf("ReadFile updated error = %v", err)
	}
	if !strings.Contains(string(updated), "project: OPS") {
		t.Fatalf("updated profile = %s", string(updated))
	}

	var rollbackStdout bytes.Buffer
	var rollbackStderr bytes.Buffer
	rollbackCode := Run([]string{"profile", "rollback", "--workspace", "tapstate"}, &rollbackStdout, &rollbackStderr)
	if rollbackCode != 0 {
		t.Fatalf("rollbackCode = %d stdout = %s stderr = %s", rollbackCode, rollbackStdout.String(), rollbackStderr.String())
	}
	assertJSONField(t, rollbackStdout.String(), "operation", "profile_rollback")
	assertJSONField(t, rollbackStdout.String(), "workspace", "tapstate")
	assertJSONField(t, rollbackStdout.String(), "next_action", "profile_validate")
	restored, err := os.ReadFile(target)
	if err != nil {
		t.Fatalf("ReadFile restored error = %v", err)
	}
	if !strings.Contains(string(restored), "project: TAP") {
		t.Fatalf("restored profile = %s", string(restored))
	}
}

func TestPolicyValidateOutputsIssueCount(t *testing.T) {
	var stdout bytes.Buffer
	var stderr bytes.Buffer
	code := Run([]string{"policy", "validate", "--workspace", "tapstate"}, &stdout, &stderr)
	if code != 0 {
		t.Fatalf("code = %d stdout = %s stderr = %s", code, stdout.String(), stderr.String())
	}
	assertJSONField(t, stdout.String(), "operation", "policy_validate")
	assertJSONField(t, stdout.String(), "workspace", "tapstate")
	assertJSONField(t, stdout.String(), "policy", "default")
	assertJSONNumber(t, stdout.String(), "issues", 0)
	assertJSONField(t, stdout.String(), "next_action", "continue")
}

func TestPolicyUpdateAndRollbackUseLocalPolicyBackup(t *testing.T) {
	repo := t.TempDir()
	t.Chdir(repo)
	writeCLITestFile(t, filepath.Join(repo, "go.mod"), "module example.local/test\n")
	writeCLITestFile(t, filepath.Join(repo, "install-resources", "basic", "contracts", "operations", ".keep"), "")
	target := filepath.Join(repo, "install-resources", "basic", "policies", "default.yaml")
	source := filepath.Join(repo, "incoming", "default-policy.yaml")
	writeCLITestFile(t, target, validCLIPolicyYAML("default", false))
	writeCLITestFile(t, source, validCLIPolicyYAML("default", true))

	var updateStdout bytes.Buffer
	var updateStderr bytes.Buffer
	updateCode := Run([]string{"policy", "update", "--workspace", "tapstate", "--source", source}, &updateStdout, &updateStderr)
	if updateCode != 0 {
		t.Fatalf("updateCode = %d stdout = %s stderr = %s", updateCode, updateStdout.String(), updateStderr.String())
	}
	assertJSONField(t, updateStdout.String(), "operation", "policy_update")
	assertJSONField(t, updateStdout.String(), "workspace", "tapstate")
	assertJSONField(t, updateStdout.String(), "policy", "default")
	assertJSONField(t, updateStdout.String(), "next_action", "policy_validate")
	updated, err := os.ReadFile(target)
	if err != nil {
		t.Fatalf("ReadFile updated error = %v", err)
	}
	if !strings.Contains(string(updated), "write_jira_comment:\n    required: true") {
		t.Fatalf("updated policy = %s", string(updated))
	}

	var rollbackStdout bytes.Buffer
	var rollbackStderr bytes.Buffer
	rollbackCode := Run([]string{"policy", "rollback", "--workspace", "tapstate"}, &rollbackStdout, &rollbackStderr)
	if rollbackCode != 0 {
		t.Fatalf("rollbackCode = %d stdout = %s stderr = %s", rollbackCode, rollbackStdout.String(), rollbackStderr.String())
	}
	assertJSONField(t, rollbackStdout.String(), "operation", "policy_rollback")
	assertJSONField(t, rollbackStdout.String(), "workspace", "tapstate")
	assertJSONField(t, rollbackStdout.String(), "policy", "default")
	assertJSONField(t, rollbackStdout.String(), "next_action", "policy_validate")
	restored, err := os.ReadFile(target)
	if err != nil {
		t.Fatalf("ReadFile restored error = %v", err)
	}
	if !strings.Contains(string(restored), "write_jira_comment:\n    required: false") {
		t.Fatalf("restored policy = %s", string(restored))
	}
}

func TestProfileValidateReportsMissingProcessRegistryTarget(t *testing.T) {
	repo := t.TempDir()
	t.Chdir(repo)
	writeCLITestFile(t, filepath.Join(repo, "go.mod"), "module example.local/test\n")
	writeCLITestFile(t, filepath.Join(repo, "install-resources", "basic", "contracts", "operations", ".keep"), "")
	writeCLITestFile(t, filepath.Join(repo, "install-resources", "basic", "contracts", "processes", "development-change-v1.yaml"), validCLIProcessYAML("development_change_v1"))
	profileYAML := strings.Replace(validCLIProfileYAML("tapstate", "TAP"), "technical_task: development_change_v1", "technical_task: missing_process_v1", 1)
	writeCLITestFile(t, filepath.Join(repo, "install-resources", "basic", "projects", "tapstate", "profile.yaml"), profileYAML)

	var stdout bytes.Buffer
	var stderr bytes.Buffer
	code := Run([]string{"profile", "validate", "--workspace", "tapstate"}, &stdout, &stderr)
	if code != 1 {
		t.Fatalf("code = %d stdout = %s stderr = %s", code, stdout.String(), stderr.String())
	}
	assertJSONField(t, stdout.String(), "operation", "profile_validate")
	assertJSONField(t, stdout.String(), "code", "profile_validation_failed")
	assertJSONField(t, stdout.String(), "next_action", "fix_profile")
}
