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

func TestValidateReportsBrokenFailureContextSchemas(t *testing.T) {
	issues := Validate(Operation{
		Operation: "list_tasks",
		Input: map[string]FieldSpec{
			"workspace": {Type: "string"},
		},
		Output: map[string]FieldSpec{
			"workspace": {Type: "string"},
		},
		Failure: FailureSpec{
			Codes: []string{"jira_adapter_config_failed"},
			Context: map[string]FailureContextSpec{
				"unknown_failure": {
					MayInclude: map[string]FieldSpec{
						"jira_token_env": {Type: "string"},
					},
				},
				"jira_adapter_config_failed": {
					MayInclude: map[string]FieldSpec{
						"jira_token_env_has_value": {},
					},
				},
			},
		},
		SideEffects: []string{"must_not_write_jira"},
		HumanGate:   &HumanGate{},
	})

	for _, code := range []string{
		"unknown_failure_context_code",
		"missing_failure_context_field_type",
	} {
		if !hasIssue(issues, code) {
			t.Fatalf("missing validation issue %s in %#v", code, issues)
		}
	}
}

func TestValidateAcceptsRepositoryOperationContracts(t *testing.T) {
	paths, err := filepath.Glob(filepath.Join("..", "..", "..", "..", "install-resources", "basic", "contracts", "operations", "*.yaml"))
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
