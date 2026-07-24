package policy

type ValidationIssue struct {
	Code    string
	Message string
}

var requiredGates = []string{
	"write_jira_comment",
	"write_local_evidence",
	"transition_jira_status",
	"git_commit",
	"git_push",
	"git_merge",
	"git_rebase",
	"git_clean",
	"create_pr",
	"update_pr",
	"fix_pr_comments",
	"scope_change",
}

func Validate(p Policy) []ValidationIssue {
	var issues []ValidationIssue
	if p.Policy == "" {
		issues = append(issues, ValidationIssue{Code: "missing_policy_name", Message: "policy is required"})
	}
	if p.Version <= 0 {
		issues = append(issues, ValidationIssue{Code: "missing_policy_version", Message: "version must be greater than zero"})
	}
	if len(p.Gates) == 0 {
		issues = append(issues, ValidationIssue{Code: "missing_gates", Message: "gates are required"})
	}
	for _, gate := range requiredGates {
		if _, ok := p.Gates[gate]; !ok {
			issues = append(issues, ValidationIssue{Code: "missing_required_gate", Message: gate + " gate is required"})
		}
	}
	return issues
}
