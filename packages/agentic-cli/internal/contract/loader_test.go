package contract

import (
	"path/filepath"
	"testing"
)

func TestLoadFileReadsOperationContract(t *testing.T) {
	path := filepath.Join("..", "..", "..", "..", "contracts", "operations", "takeover-task.yaml")
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
	path := filepath.Join("..", "..", "..", "..", "contracts", "operations", "takeover-task.yaml")
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

func contains(values []string, want string) bool {
	for _, value := range values {
		if value == want {
			return true
		}
	}
	return false
}
