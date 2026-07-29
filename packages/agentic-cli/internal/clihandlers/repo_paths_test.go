package clihandlers

import (
	"errors"
	"os"
	"testing"
)

func TestDefaultProcessRegistryIncludesTaskClasses(t *testing.T) {
	registry := defaultProcessRegistry()

	tests := []struct {
		processID string
		taskClass string
	}{
		{"development_change_v1", "feature_change"},
		{"development_change_v1", "bug_fix"},
		{"development_change_v1", "technical_task"},
		{"investigation_v1", "investigation"},
		{"agenticops_improvement_v1", "process_improvement"},
	}
	for _, test := range tests {
		process := registry[test.processID]
		found := false
		for _, taskClass := range process.TaskClasses {
			if taskClass == test.taskClass {
				found = true
				break
			}
		}
		if !found {
			t.Fatalf("%s TaskClasses = %#v, missing %s", test.processID, process.TaskClasses, test.taskClass)
		}
	}
}

func TestOperationContractErrorCode(t *testing.T) {
	if got := operationContractErrorCode(os.ErrNotExist); got != "operation_contract_not_found" {
		t.Fatalf("operationContractErrorCode(os.ErrNotExist) = %s", got)
	}
	if got := operationContractErrorCode(errors.New("invalid yaml")); got != "operation_contract_load_failed" {
		t.Fatalf("operationContractErrorCode(invalid yaml) = %s", got)
	}
}
