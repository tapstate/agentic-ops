package contract

import (
	"path/filepath"
	"testing"
)

func TestLoadFileReadsOperationContract(t *testing.T) {
	path := filepath.Join("..", "..", "..", "..", "install-resources", "basic", "contracts", "operations", "takeover-task.yaml")
	op, err := LoadFile(path)
	if err != nil {
		t.Fatalf("LoadFile error = %v", err)
	}
	if op.Operation != "takeover_task" {
		t.Fatalf("Operation = %s", op.Operation)
	}
	if op.TaskType != "task_takeover" {
		t.Fatalf("TaskType = %s", op.TaskType)
	}
	if len(op.AllowedStages) == 0 {
		t.Fatal("AllowedStages is empty")
	}
}

func TestLoadFileReadsTakeoverGateSchema(t *testing.T) {
	path := filepath.Join("..", "..", "..", "..", "install-resources", "basic", "contracts", "operations", "takeover-task.yaml")
	op, err := LoadFile(path)
	if err != nil {
		t.Fatalf("LoadFile error = %v", err)
	}
	if op.Input["issue_key"].Required != true {
		t.Fatalf("issue_key required = %v, want true", op.Input["issue_key"].Required)
	}
	if !contains(op.Preconditions, "current_user_must_match_owner") {
		t.Fatalf("missing owner precondition: %#v", op.Preconditions)
	}
	if !contains(op.Failure.Codes, "assignee_mismatch") {
		t.Fatalf("missing assignee_mismatch failure code: %#v", op.Failure.Codes)
	}
	if op.RetryPolicy.Retryable != false {
		t.Fatalf("retryable = %v, want false for takeover gate failures", op.RetryPolicy.Retryable)
	}
}

func TestLoadFileReadsFailureContextMayIncludeSchema(t *testing.T) {
	path := filepath.Join("..", "..", "..", "..", "install-resources", "basic", "contracts", "operations", "list-tasks.yaml")
	op, err := LoadFile(path)
	if err != nil {
		t.Fatalf("LoadFile error = %v", err)
	}
	context, ok := op.Failure.Context["jira_adapter_config_failed"]
	if !ok {
		t.Fatalf("missing jira_adapter_config_failed failure context: %#v", op.Failure.Context)
	}
	if context.MayInclude["jira_token_env"].Type != "string" {
		t.Fatalf("jira_token_env type = %q, want string", context.MayInclude["jira_token_env"].Type)
	}
	if context.MayInclude["jira_token_env_has_value"].Type != "boolean" {
		t.Fatalf("jira_token_env_has_value type = %q, want boolean", context.MayInclude["jira_token_env_has_value"].Type)
	}
}

func TestLoadFileReadsWriteEvidenceContentLimit(t *testing.T) {
	path := filepath.Join("..", "..", "..", "..", "install-resources", "basic", "contracts", "operations", "write-evidence.yaml")
	op, err := LoadFile(path)
	if err != nil {
		t.Fatalf("LoadFile error = %v", err)
	}
	contentFile := op.Input["content_file"]
	if !contentFile.Required || contentFile.Type != "file" || contentFile.MaxBytes != 65536 {
		t.Fatalf("content_file = %#v", contentFile)
	}
	if !contains(op.RequiredInputs, "content_file") {
		t.Fatalf("required inputs = %#v", op.RequiredInputs)
	}
}

func contains(values []string, want string) bool {
	for _, value := range values {
		if value == want {
			return true
		}
	}
	return false
}
