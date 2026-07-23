package contract

import (
	"path/filepath"
	"testing"
)

func TestValidateReportsMissingRequiredContractSections(t *testing.T) {
	issues := Validate(Operation{})

	for _, code := range []string{
		"missing_operation",
		"missing_input",
		"missing_output",
		"missing_failure_codes",
		"missing_side_effects",
		"missing_human_gate",
	} {
		if !hasIssue(issues, code) {
			t.Fatalf("missing validation issue %s in %#v", code, issues)
		}
	}
}

func TestValidateAcceptsRepositoryOperationContracts(t *testing.T) {
	paths, err := filepath.Glob(filepath.Join("..", "..", "..", "..", "contracts", "operations", "*.yaml"))
	if err != nil {
		t.Fatalf("Glob error = %v", err)
	}
	if len(paths) == 0 {
		t.Fatal("no operation contracts found")
	}
	for _, path := range paths {
		op, err := LoadFile(path)
		if err != nil {
			t.Fatalf("LoadFile(%s) error = %v", path, err)
		}
		if issues := Validate(op); len(issues) != 0 {
			t.Fatalf("Validate(%s) issues = %#v", path, issues)
		}
	}
}

func hasIssue(issues []ValidationIssue, code string) bool {
	for _, issue := range issues {
		if issue.Code == code {
			return true
		}
	}
	return false
}
