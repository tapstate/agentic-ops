package profile

import (
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/tapstate/agentic-ops/packages/agentic-cli/internal/process"
)

func TestValidateAcceptsTapstateProfile(t *testing.T) {
	p, err := LoadFile(filepath.Join("..", "..", "..", "..", "install-resources", "basic", "projects", "tapstate", "profile.yaml"))
	if err != nil {
		t.Fatalf("LoadFile error = %v", err)
	}
	if issues := Validate(p); len(issues) != 0 {
		t.Fatalf("Validate issues = %#v", issues)
	}
}

func TestValidateAcceptsTapdataProfile(t *testing.T) {
	p, err := LoadFile(filepath.Join("..", "..", "..", "..", "install-resources", "basic", "projects", "tapdata", "profile.yaml"))
	if err != nil {
		t.Fatalf("LoadFile error = %v", err)
	}
	if issues := Validate(p); len(issues) != 0 {
		t.Fatalf("Validate issues = %#v", issues)
	}
	if p.Workspace != "tapdata" {
		t.Fatalf("Workspace = %s", p.Workspace)
	}
	if p.Jira.User != "<jira-user>" {
		t.Fatalf("Jira.User = %s", p.Jira.User)
	}
	if p.Jira.Project != "TAP" {
		t.Fatalf("Jira.Project = %s", p.Jira.Project)
	}
	if p.Local.SourceRoot != "<project-ai-workspace>/repos/tapdata" {
		t.Fatalf("Local.SourceRoot = %s", p.Local.SourceRoot)
	}
	if !containsString(p.Standards, "projects/tapdata/standards/development-rules.md") {
		t.Fatalf("Standards missing tapdata development rules: %#v", p.Standards)
	}
	if field := p.JiraFormMapping.Fields["problem_summary"]; field.Source != "jira_description_section" || field.Section != "问题现象" {
		t.Fatalf("problem_summary must map to confirmed description section: %#v", field)
	}
	for _, name := range []string{"issue_analysis", "fix_details", "verification_method"} {
		field := p.JiraFormMapping.Fields[name]
		if field.Source != "jira_field" || field.JiraField == "" || !field.Writable {
			t.Fatalf("%s must be a writable Jira field mapping: %#v", name, field)
		}
	}
	if p.JiraFormMapping.Fields["owner"].Writable {
		t.Fatal("owner mapping must not be writable through update-task-form")
	}
	if gate := p.ReviewGates["development_engineer_review"]; gate.Role != "development_engineer" || gate.DecisionField != "development_engineer_decision" {
		t.Fatalf("development engineer review gate mismatch: %#v", gate)
	}
	standardPath := filepath.Join("..", "..", "..", "..", "install-resources", "basic", "projects", "tapdata", "standards", "development-rules.md")
	if info, err := os.Stat(standardPath); err != nil || info.IsDir() {
		t.Fatalf("tapdata standard file is not readable: info=%v err=%v", info, err)
	}
	for name, value := range map[string]string{
		"workspace_root": p.Local.WorkspaceRoot,
		"source_root":    p.Local.SourceRoot,
		"runs_dir":       p.Local.RunsDir,
		"run_logs_dir":   p.Local.RunLogsDir,
		"feedback_dir":   p.Local.FeedbackDir,
	} {
		if !strings.Contains(value, "<project-ai-workspace>") {
			t.Fatalf("%s should keep shared install placeholder: %s", name, value)
		}
	}
}

func TestValidateAcceptsAOProfileForAgenticOpsRepository(t *testing.T) {
	p, err := LoadFile(filepath.Join("..", "..", "..", "..", "install-resources", "basic", "projects", "ao", "profile.yaml"))
	if err != nil {
		t.Fatalf("LoadFile AO error = %v", err)
	}
	if issues := Validate(p); len(issues) != 0 {
		t.Fatalf("Validate AO issues = %#v", issues)
	}
	if p.Workspace != "ao" || p.Jira.Project != "AO" {
		t.Fatalf("AO binding = workspace:%s project:%s", p.Workspace, p.Jira.Project)
	}
	if p.TaskClassMapping.IssueTypes["Agentic 缺陷"] != "process_improvement" {
		t.Fatalf("Agentic 缺陷 mapping = %#v", p.TaskClassMapping.IssueTypes)
	}
	if p.GitHub.Repositories.Default != "tapstate/agentic-ops" {
		t.Fatalf("AO default repository = %s", p.GitHub.Repositories.Default)
	}
	for name, fieldID := range map[string]string{
		"agentic_id":                  "customfield_10364",
		"agentic_run_id":              "customfield_10365",
		"agentic_takeover_at":         "customfield_10366",
		"agentic_next_action":         "customfield_10367",
		"agentic_completion_evidence": "customfield_10368",
		"agentic_heartbeat_at":        "customfield_10369",
	} {
		if got := p.JiraFormMapping.Fields[name].JiraField; got != fieldID {
			t.Fatalf("%s field = %s, want %s", name, got, fieldID)
		}
	}
	if p.JiraTransitionMapping["start_progress"].ID != "2" || p.JiraTransitionMapping["complete"].ID != "5" {
		t.Fatalf("AO transitions = %#v", p.JiraTransitionMapping)
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
		"missing_local_run_logs_dir",
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

func TestValidateProcessesReportsMissingProcessMappingTarget(t *testing.T) {
	p := validDeepProfileForTest()
	p.StandardProcessMapping["technical_task"] = "missing_process_v1"
	registry := map[string]process.Process{
		"development_change_v1": validProcessForTest(),
	}

	issues := ValidateProcesses(p, registry)
	if !hasIssue(issues, "standard_process_missing") {
		t.Fatalf("issues missing standard_process_missing: %#v", issues)
	}
}

func TestValidateProcessesAcceptsExistingProcessMappingTarget(t *testing.T) {
	p := validDeepProfileForTest()
	registry := map[string]process.Process{
		"development_change_v1": validProcessForTest(),
	}

	issues := ValidateProcesses(p, registry)
	if len(issues) != 0 {
		t.Fatalf("ValidateProcesses issues = %#v", issues)
	}
}

func TestValidateProcessesReportsMissingReviewGateRole(t *testing.T) {
	p := validDeepProfileForTest()
	registry := map[string]process.Process{
		"development_change_v1": {
			ProcessID:  "development_change_v1",
			EntryStage: "waiting_takeover",
			Stages: []process.Stage{
				{ID: "waiting_takeover"},
				{ID: "implementation", ReviewGate: "qa"},
			},
		},
	}

	issues := ValidateProcesses(p, registry)
	if !hasIssue(issues, "review_gate_mapping_gap") {
		t.Fatalf("issues missing review_gate_mapping_gap: %#v", issues)
	}
}

func TestValidateProcessesReportsUnknownRetryRedoStage(t *testing.T) {
	p := validDeepProfileForTest()
	p.RetryRedo = map[string]RetryRedoPolicy{
		"scope_changed": {RedoFromStage: "missing_stage", AgenticNextAction: "redo_previous_stage"},
	}
	registry := map[string]process.Process{
		"development_change_v1": validProcessForTest(),
	}

	issues := ValidateProcesses(p, registry)
	if !hasIssue(issues, "retry_redo_stage_gap") {
		t.Fatalf("issues missing retry_redo_stage_gap: %#v", issues)
	}
}

func TestValidateProcessesReportsUnknownNextAction(t *testing.T) {
	p := validDeepProfileForTest()
	p.RetryRedo = map[string]RetryRedoPolicy{
		"verification_failed": {AgenticNextAction: "surprise_action"},
	}
	registry := map[string]process.Process{
		"development_change_v1": validProcessForTest(),
	}

	issues := ValidateProcesses(p, registry)
	if !hasIssue(issues, "next_action_mapping_gap") {
		t.Fatalf("issues missing next_action_mapping_gap: %#v", issues)
	}
}

func validDeepProfileForTest() Profile {
	return Profile{
		Workspace: "tapstate",
		TaskClassMapping: TaskClassMapping{
			IssueTypes: map[string]string{"Task": "technical_task"},
		},
		StandardProcessMapping: map[string]string{"technical_task": "development_change_v1"},
		ReviewGates: map[string]ReviewGate{
			"development_engineer_review": {
				Role:               "development_engineer",
				DecisionField:      "development_engineer_decision",
				ReturnedNextAction: "fix_and_verify",
			},
		},
	}
}

func validProcessForTest() process.Process {
	return process.Process{
		ProcessID:  "development_change_v1",
		EntryStage: "waiting_takeover",
		Stages: []process.Stage{
			{ID: "waiting_takeover"},
			{ID: "implementation"},
			{ID: "completed"},
		},
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

func containsString(values []string, expected string) bool {
	for _, value := range values {
		if value == expected {
			return true
		}
	}
	return false
}
