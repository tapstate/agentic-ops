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
