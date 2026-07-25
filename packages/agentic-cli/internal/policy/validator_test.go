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
