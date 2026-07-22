package profile

import (
	"path/filepath"
	"testing"
)

func TestValidateAcceptsTapstateProfile(t *testing.T) {
	p, err := LoadFile(filepath.Join("..", "..", "..", "..", "profiles", "tapstate.yaml"))
	if err != nil {
		t.Fatalf("LoadFile error = %v", err)
	}
	if issues := Validate(p); len(issues) != 0 {
		t.Fatalf("Validate issues = %#v", issues)
	}
}

func TestValidateReportsMissingRequiredMappings(t *testing.T) {
	issues := Validate(Profile{})
	for _, code := range []string{
		"missing_workspace",
		"missing_jira_project",
		"missing_task_query",
		"missing_form_mapping",
		"task_class_mapping_gap",
		"standard_process_mapping_gap",
		"lifecycle_mapping_gap",
		"transition_mapping_gap",
		"missing_local_source_root",
	} {
		if !hasIssue(issues, code) {
			t.Fatalf("missing validation issue %s in %#v", code, issues)
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
