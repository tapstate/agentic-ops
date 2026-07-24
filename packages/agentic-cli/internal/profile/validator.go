package profile

import "github.com/tapstate/agentic-ops/packages/agentic-cli/internal/process"

type ValidationIssue struct {
	Code    string
	Message string
}

func Validate(p Profile) []ValidationIssue {
	var issues []ValidationIssue
	if p.Workspace == "" {
		issues = append(issues, ValidationIssue{Code: "missing_workspace", Message: "workspace is required"})
	}
	if p.Jira.User == "" {
		issues = append(issues, ValidationIssue{Code: "missing_jira_user", Message: "jira.user is required"})
	}
	if p.Jira.Project == "" {
		issues = append(issues, ValidationIssue{Code: "missing_jira_project", Message: "jira.project is required"})
	}
	if p.Jira.TaskQuery == "" {
		issues = append(issues, ValidationIssue{Code: "missing_task_query", Message: "jira.task_query is required"})
	}
	if len(p.JiraFormMapping.Fields) == 0 {
		issues = append(issues, ValidationIssue{Code: "missing_form_mapping", Message: "jira_form_mapping.fields is required"})
	}
	if len(p.TaskClassMapping.IssueTypes) == 0 && len(p.TaskClassMapping.Labels) == 0 && len(p.TaskClassMapping.Components) == 0 {
		issues = append(issues, ValidationIssue{Code: "task_class_mapping_gap", Message: "task class mapping is required"})
	}
	if len(p.StandardProcessMapping) == 0 {
		issues = append(issues, ValidationIssue{Code: "standard_process_mapping_gap", Message: "standard process mapping is required"})
	}
	if len(p.StatusMapping) == 0 {
		issues = append(issues, ValidationIssue{Code: "lifecycle_mapping_gap", Message: "status mapping is required"})
	}
	if len(p.TransitionMapping) == 0 {
		issues = append(issues, ValidationIssue{Code: "transition_mapping_gap", Message: "transition mapping is required"})
	}
	if len(p.JiraTransitionMapping) == 0 {
		issues = append(issues, ValidationIssue{Code: "jira_transition_mapping_gap", Message: "jira transition mapping is required"})
	}
	for action := range p.TransitionMapping {
		transition, ok := p.JiraTransitionMapping[action]
		if !ok || (transition.ID == "" && transition.Name == "") {
			issues = append(issues, ValidationIssue{Code: "jira_transition_mapping_gap", Message: "jira transition mapping is required for " + action})
			break
		}
	}
	if p.Local.SourceRoot == "" {
		issues = append(issues, ValidationIssue{Code: "missing_local_source_root", Message: "local.source_root is required"})
	}
	if p.Local.RunLogsDir == "" {
		issues = append(issues, ValidationIssue{Code: "missing_local_run_logs_dir", Message: "local.run_logs_dir is required"})
	}
	if p.GitHub.Repositories.Default == "" && len(p.GitHub.Repositories.ByComponent) == 0 && len(p.GitHub.Repositories.ByLabel) == 0 && len(p.GitHub.Repositories.ByIssueType) == 0 {
		issues = append(issues, ValidationIssue{Code: "workspace_repo_mapping_gap", Message: "github.repositories mapping is required"})
	}
	return issues
}

func ValidateProcesses(p Profile, registry map[string]process.Process) []ValidationIssue {
	var issues []ValidationIssue
	stageIDs := map[string]bool{}
	for taskClass, processID := range p.StandardProcessMapping {
		registeredProcess, ok := registry[processID]
		if !ok {
			issues = append(issues, ValidationIssue{Code: "standard_process_missing", Message: processID + " process is required for " + taskClass})
			continue
		}
		if processIssues := process.Validate(registeredProcess); len(processIssues) > 0 {
			issues = append(issues, ValidationIssue{Code: "standard_process_invalid", Message: processID + " process validation failed: " + processIssues[0].Code})
			continue
		}
		for _, stage := range registeredProcess.Stages {
			stageIDs[stage.ID] = true
			if stage.ReviewGate != "" && !hasReviewGateRole(p, stage.ReviewGate) {
				issues = append(issues, ValidationIssue{Code: "review_gate_mapping_gap", Message: processID + " stage " + stage.ID + " requires review gate role " + stage.ReviewGate})
			}
		}
	}
	for name, retryRedo := range p.RetryRedo {
		if retryRedo.RedoFromStage != "" && !stageIDs[retryRedo.RedoFromStage] {
			issues = append(issues, ValidationIssue{Code: "retry_redo_stage_gap", Message: name + " redo_from_stage references unknown process stage " + retryRedo.RedoFromStage})
		}
		if retryRedo.NextAction != "" && !isKnownNextAction(retryRedo.NextAction) {
			issues = append(issues, ValidationIssue{Code: "next_action_mapping_gap", Message: name + " next_action is unknown: " + retryRedo.NextAction})
		}
	}
	for name, gate := range p.ReviewGates {
		if gate.ReturnedNextAction != "" && !isKnownNextAction(gate.ReturnedNextAction) {
			issues = append(issues, ValidationIssue{Code: "next_action_mapping_gap", Message: name + " returned_next_action is unknown: " + gate.ReturnedNextAction})
		}
	}
	return issues
}

func hasReviewGateRole(p Profile, role string) bool {
	for name, gate := range p.ReviewGates {
		if name == role || gate.Role == role {
			return true
		}
	}
	return false
}

func isKnownNextAction(nextAction string) bool {
	switch nextAction {
	case "proceed",
		"ask_owner",
		"blocked",
		"continue",
		"continue_development",
		"fix_and_verify",
		"redo_previous_stage",
		"request_owner_confirmation",
		"task_audit_submitted",
		"review_proposals",
		"share_bundle_with_maintainer":
		return true
	default:
		return false
	}
}
