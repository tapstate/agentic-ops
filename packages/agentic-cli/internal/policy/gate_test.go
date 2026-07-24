package policy

import "testing"

func TestRequiresHumanGateForWriteOperations(t *testing.T) {
	p := Policy{
		Gates: map[string]Gate{
			"prepare_pr": {Required: true},
			"list_tasks": {Required: false},
		},
	}
	if !RequiresHumanGate(p, "prepare_pr") {
		t.Fatal("prepare_pr should require a human gate from policy")
	}
	if RequiresHumanGate(p, "list_tasks") {
		t.Fatal("list_tasks should not require a human gate")
	}
	if RequiresHumanGate(p, "unknown") {
		t.Fatal("unknown gates should not require a human gate")
	}
}
