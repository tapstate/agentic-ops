package policy

import (
	"path/filepath"
	"testing"
)

func TestValidateAcceptsDefaultPolicy(t *testing.T) {
	p, err := LoadFile(filepath.Join("..", "..", "..", "..", "install-resources", "basic", "policies", "default.yaml"))
	if err != nil {
		t.Fatalf("LoadFile error = %v", err)
	}
	if issues := Validate(p); len(issues) != 0 {
		t.Fatalf("Validate issues = %#v", issues)
	}
}

func TestValidateAcceptsTaskExecutionAuthorizationScope(t *testing.T) {
	p, err := LoadFile(filepath.Join("..", "..", "..", "..", "install-resources", "basic", "policies", "default.yaml"))
	if err != nil {
		t.Fatalf("LoadFile error = %v", err)
	}
	scope, ok := p.AuthorizationScopes["task_execution"]
	if !ok {
		t.Fatal("task_execution authorization scope is missing")
	}
	if scope.ConfirmationSource != "jira_decision" {
		t.Fatalf("confirmation source = %q", scope.ConfirmationSource)
	}
	for _, operation := range []string{"git_commit", "git_push", "write_jira_comment", "create_pr", "update_pr"} {
		if got, ok := AuthorizationScopeForOperation(p, operation); !ok || got != "task_execution" {
			t.Fatalf("operation %s authorization scope = %q, %v", operation, got, ok)
		}
	}
	if issues := Validate(p); len(issues) != 0 {
		t.Fatalf("Validate issues = %#v", issues)
	}
}

func TestValidateRejectsOverlappingAuthorizationOperations(t *testing.T) {
	p := Policy{
		Policy:  "default",
		Version: 1,
		Gates:   completeRequiredGates(),
		AuthorizationScopes: map[string]AuthorizationScope{
			"task_execution": {
				ConfirmationSource: "jira_decision",
				RequiredBindings:   []string{"issue_key"},
				CoveredOperations:  []string{"git_push"},
				ExcludedOperations: []string{"git_push"},
				InvalidatedBy:      []string{"scope_changed"},
			},
		},
	}
	issues := Validate(p)
	if !hasPolicyIssue(issues, "invalid_authorization_scope") {
		t.Fatalf("issues missing invalid_authorization_scope: %#v", issues)
	}
}

func TestValidateRejectsOperationCoveredByMultipleScopes(t *testing.T) {
	p := Policy{
		Policy:  "default",
		Version: 1,
		Gates:   completeRequiredGates(),
		AuthorizationScopes: map[string]AuthorizationScope{
			"task_execution":  validAuthorizationScopeForTest("git_push"),
			"other_execution": validAuthorizationScopeForTest("git_push"),
		},
	}
	issues := Validate(p)
	if !hasPolicyIssue(issues, "ambiguous_authorization_operation") {
		t.Fatalf("issues missing ambiguous_authorization_operation: %#v", issues)
	}
}

func TestValidateReportsMissingRequiredGates(t *testing.T) {
	issues := Validate(Policy{})
	for _, code := range []string{
		"missing_policy_name",
		"missing_policy_version",
		"missing_gates",
		"missing_required_gate",
	} {
		if !hasPolicyIssue(issues, code) {
			t.Fatalf("missing validation issue %s in %#v", code, issues)
		}
	}
}

func hasPolicyIssue(issues []ValidationIssue, code string) bool {
	for _, issue := range issues {
		if issue.Code == code {
			return true
		}
	}
	return false
}

func completeRequiredGates() map[string]Gate {
	gates := make(map[string]Gate, len(requiredGates))
	for _, name := range requiredGates {
		gates[name] = Gate{Required: true}
	}
	return gates
}

func validAuthorizationScopeForTest(operation string) AuthorizationScope {
	return AuthorizationScope{
		ConfirmationSource: "jira_decision",
		RequiredBindings:   []string{"issue_key"},
		CoveredOperations:  []string{operation},
		ExcludedOperations: []string{"git_merge"},
		InvalidatedBy:      []string{"scope_changed"},
	}
}
