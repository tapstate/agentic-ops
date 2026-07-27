package cli

import (
	"bytes"
	"encoding/json"
	"os"
	"path/filepath"
	"testing"

	"github.com/tapstate/agentic-ops/packages/agentic-cli/internal/contract"
)

func TestJiraRuntimeConfigContractCoversInitPreflightAndListTasks(t *testing.T) {
	root := t.TempDir()
	installDir := t.TempDir()
	t.Setenv("AGENTIC_OPS_WORKSPACE_ROOT", root)
	t.Setenv("AGENTIC_OPS_HOME", installDir)
	t.Setenv("AGENTIC_OPS_JIRA_ADAPTER", "")
	t.Setenv("AGENTIC_OPS_JIRA_API_TOKEN", "")
	sourceRoot, err := os.Getwd()
	if err != nil {
		t.Fatalf("Getwd error = %v", err)
	}

	var initStdout bytes.Buffer
	var initStderr bytes.Buffer
	initCode := Run([]string{"workspace", "init", "--project", "tapdata", "--jira-user", "lead@example.com", "--source-root", sourceRoot}, &initStdout, &initStderr)
	if initCode != 0 {
		t.Fatalf("initCode = %d stdout = %s stderr = %s", initCode, initStdout.String(), initStderr.String())
	}
	jiraConfigPath := filepath.Join(installDir, "user", "config.local.yaml")
	jiraEnvPath := filepath.Join(installDir, "user", ".env")
	initContract := loadOperationContractForCLITest(t, "workspace-init")
	assertContractOutputAndJSONFields(t, initContract, initStdout.String(), []string{
		"jira_config_status",
		"jira_config_path",
		"jira_token_env",
		"jira_token_env_has_value",
		"jira_env_file",
		"jira_token_help_url",
		"jira_token_setup",
		"jira_config_next_action",
	})
	assertJSONField(t, initStdout.String(), "jira_config_status", "needs_jira_api_token")
	assertJSONField(t, initStdout.String(), "jira_config_path", jiraConfigPath)
	assertJSONField(t, initStdout.String(), "jira_env_file", jiraEnvPath)
	assertJSONField(t, initStdout.String(), "jira_token_env_has_value", false)

	var preflightStdout bytes.Buffer
	var preflightStderr bytes.Buffer
	preflightCode := Run([]string{"preflight", "--workspace", "tapdata"}, &preflightStdout, &preflightStderr)
	if preflightCode != 0 {
		t.Fatalf("preflightCode = %d stdout = %s stderr = %s", preflightCode, preflightStdout.String(), preflightStderr.String())
	}
	preflightContract := loadOperationContractForCLITest(t, "preflight")
	assertContractOutputAndJSONFields(t, preflightContract, preflightStdout.String(), []string{
		"jira_token_env",
		"jira_token_env_has_value",
		"jira_env_file",
		"jira_token_help_url",
		"jira_token_setup",
		"jira_config_source",
	})
	assertNestedJSONField(t, preflightStdout.String(), []string{"checks", "jira_config", "status"}, "failed")
	assertNestedJSONField(t, preflightStdout.String(), []string{"checks", "jira_config", "code"}, "jira_api_token_missing")
	assertJSONField(t, preflightStdout.String(), "jira_config_source", jiraConfigPath)
	assertJSONField(t, preflightStdout.String(), "jira_env_file", jiraEnvPath)
	assertJSONField(t, preflightStdout.String(), "next_action", "set_jira_api_token")

	var listStdout bytes.Buffer
	var listStderr bytes.Buffer
	listCode := Run([]string{"list-tasks", "--workspace", "tapdata"}, &listStdout, &listStderr)
	if listCode != 1 {
		t.Fatalf("listCode = %d stdout = %s stderr = %s", listCode, listStdout.String(), listStderr.String())
	}
	listContract := loadOperationContractForCLITest(t, "list-tasks")
	assertContractFailureContextAndJSONFields(t, listContract, "jira_adapter_config_failed", listStdout.String(), []string{
		"jira_token_env",
		"jira_token_env_has_value",
		"jira_env_file",
		"jira_token_help_url",
		"jira_token_setup",
		"jira_config_source",
	})
	assertJSONField(t, listStdout.String(), "code", "jira_adapter_config_failed")
	assertJSONField(t, listStdout.String(), "jira_config_source", jiraConfigPath)
	assertJSONField(t, listStdout.String(), "jira_env_file", jiraEnvPath)
	assertJSONField(t, listStdout.String(), "jira_token_env_has_value", false)
}

func loadOperationContractForCLITest(t *testing.T, operation string) contract.Operation {
	t.Helper()
	path := filepath.Join("..", "..", "..", "..", "install-resources", "basic", "contracts", "operations", operation+".yaml")
	op, err := contract.LoadFile(path)
	if err != nil {
		t.Fatalf("LoadFile(%s) error = %v", path, err)
	}
	return op
}

func assertContractOutputAndJSONFields(t *testing.T, op contract.Operation, raw string, fields []string) {
	t.Helper()
	for _, field := range fields {
		if _, ok := op.Output[field]; !ok {
			t.Fatalf("%s contract output missing %s", op.Operation, field)
		}
		assertJSONHasField(t, raw, field)
	}
}

func assertContractFailureContextAndJSONFields(t *testing.T, op contract.Operation, failureCode string, raw string, fields []string) {
	t.Helper()
	context, ok := op.Failure.Context[failureCode]
	if !ok {
		t.Fatalf("%s contract failure context missing %s", op.Operation, failureCode)
	}
	for _, field := range fields {
		if _, ok := context.MayInclude[field]; !ok {
			t.Fatalf("%s contract failure context %s missing %s", op.Operation, failureCode, field)
		}
		assertJSONHasField(t, raw, field)
	}
}

func assertJSONHasField(t *testing.T, raw string, field string) {
	t.Helper()
	var got map[string]any
	if err := json.Unmarshal([]byte(raw), &got); err != nil {
		t.Fatalf("invalid JSON %q: %v", raw, err)
	}
	if _, ok := got[field]; !ok {
		t.Fatalf("JSON missing field %s: %s", field, raw)
	}
}
