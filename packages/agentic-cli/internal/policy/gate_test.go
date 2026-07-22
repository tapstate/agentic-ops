package policy

import "testing"

func TestRequiresHumanGateForWriteOperations(t *testing.T) {
	if !RequiresHumanGate("prepare_pr") {
		t.Fatal("prepare_pr should require a human gate")
	}
	if RequiresHumanGate("list_tasks") {
		t.Fatal("list_tasks should not require a human gate")
	}
}
