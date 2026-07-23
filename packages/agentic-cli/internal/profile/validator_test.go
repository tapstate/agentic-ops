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
		"missing_jira_user",
		"missing_jira_project",
		"missing_task_query",
		"missing_form_mapping",
		"task_class_mapping_gap",
		"standard_process_mapping_gap",
		"lifecycle_mapping_gap",
		"transition_mapping_gap",
		"jira_transition_mapping_gap",
		"missing_local_source_root",
		"workspace_repo_mapping_gap",
	} {
		if !hasIssue(issues, code) {
			t.Fatalf("missing validation issue %s in %#v", code, issues)
		}
	}
}

func TestValidateReportsMissingJiraTransitionMappingForStandardTransition(t *testing.T) {
	p := Profile{
		Workspace: "tapstate",
		Jira: JiraConfig{
			User:      "dev@example.com",
			Project:   "TAP",
			TaskQuery: "project = TAP",
		},
		JiraFormMapping: FormMapping{
			Fields: map[string]FormField{"owner": {JiraField: "assignee"}},
		},
		TaskClassMapping: TaskClassMapping{
			IssueTypes: map[string]string{"Task": "technical_task"},
		},
		StandardProcessMapping: map[string]string{"technical_task": "development_change_v1"},
		StatusMapping:          map[string]string{"Done": "completed"},
		TransitionMapping:      map[string]string{"complete": "completed"},
		GitHub:                 GitHubConfig{Repositories: RepositoryMapping{Default: "tapstate/example-repo"}},
		Local:                  LocalConfig{SourceRoot: "/tmp/src"},
	}

	issues := Validate(p)
	if !hasIssue(issues, "jira_transition_mapping_gap") {
		t.Fatalf("issues missing jira transition mapping gap: %#v", issues)
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
