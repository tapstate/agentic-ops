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
	if len(p.AuthorizationScopes) == 0 {
		issues = append(issues, ValidationIssue{Code: "missing_authorization_scopes", Message: "authorization_scopes are required"})
	}
	for _, gate := range requiredGates {
		if _, ok := p.Gates[gate]; !ok {
			issues = append(issues, ValidationIssue{Code: "missing_required_gate", Message: gate + " gate is required"})
		}
	}
	coveredBy := make(map[string]string)
	for name, scope := range p.AuthorizationScopes {
		if invalidAuthorizationScope(scope) {
			issues = append(issues, ValidationIssue{Code: "invalid_authorization_scope", Message: name + " authorization scope is incomplete or contradictory"})
		}
		for _, operation := range scope.CoveredOperations {
			if previous, ok := coveredBy[operation]; ok && previous != name {
				issues = append(issues, ValidationIssue{Code: "ambiguous_authorization_operation", Message: operation + " is covered by both " + previous + " and " + name})
				continue
			}
			coveredBy[operation] = name
		}
	}
	return issues
}

func invalidAuthorizationScope(scope AuthorizationScope) bool {
	if scope.ConfirmationSource == "" ||
		len(scope.RequiredBindings) == 0 ||
		len(scope.CoveredOperations) == 0 ||
		len(scope.ExcludedOperations) == 0 ||
		len(scope.InvalidatedBy) == 0 {
		return true
	}
	for _, covered := range scope.CoveredOperations {
		for _, excluded := range scope.ExcludedOperations {
			if covered == excluded {
				return true
			}
		}
	}
	return false
}
