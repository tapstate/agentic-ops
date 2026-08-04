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

func TestTaskAuthorizationDoesNotDisableHumanGate(t *testing.T) {
	p := Policy{
		Gates: map[string]Gate{
			"git_push": {Required: true},
		},
		AuthorizationScopes: map[string]AuthorizationScope{
			"task_execution": {CoveredOperations: []string{"git_push"}},
		},
	}
	if !RequiresHumanGate(p, "git_push") {
		t.Fatal("git_push must remain human gated")
	}
	if scope, ok := AuthorizationScopeForOperation(p, "git_push"); !ok || scope != "task_execution" {
		t.Fatalf("authorization scope = %q, %v", scope, ok)
	}
}
