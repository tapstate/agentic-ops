package profile

type ValidationIssue struct {
	Code    string
	Message string
}

func Validate(p Profile) []ValidationIssue {
	var issues []ValidationIssue
	if p.Workspace == "" {
		issues = append(issues, ValidationIssue{Code: "missing_workspace", Message: "workspace is required"})
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
	if len(p.TaskClassMapping.IssueTypes) == 0 && len(p.TaskClassMapping.Labels) == 0 {
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
	if p.Local.SourceRoot == "" {
		issues = append(issues, ValidationIssue{Code: "missing_local_source_root", Message: "local.source_root is required"})
	}
	return issues
}
