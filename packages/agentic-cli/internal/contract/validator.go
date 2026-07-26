package contract

type ValidationIssue struct {
	Code    string
	Message string
}

func Validate(op Operation) []ValidationIssue {
	var issues []ValidationIssue
	if op.Operation == "" {
		issues = append(issues, ValidationIssue{Code: "missing_operation", Message: "operation is required"})
	}
	if len(op.Input) == 0 {
		issues = append(issues, ValidationIssue{Code: "missing_input", Message: "input is required"})
	}
	if len(op.Output) == 0 {
		issues = append(issues, ValidationIssue{Code: "missing_output", Message: "output is required"})
	}
	if len(op.Failure.Codes) == 0 {
		issues = append(issues, ValidationIssue{Code: "missing_failure_codes", Message: "failure.codes is required"})
	}
	for code, context := range op.Failure.Context {
		if !containsString(op.Failure.Codes, code) {
			issues = append(issues, ValidationIssue{Code: "unknown_failure_context_code", Message: "failure.context key must be declared in failure.codes: " + code})
		}
		for fieldName, field := range context.MayInclude {
			if field.Type == "" {
				issues = append(issues, ValidationIssue{Code: "missing_failure_context_field_type", Message: "failure.context." + code + ".may_include." + fieldName + ".type is required"})
			}
		}
	}
	if len(op.SideEffects) == 0 {
		issues = append(issues, ValidationIssue{Code: "missing_side_effects", Message: "side_effects is required"})
	}
	if op.HumanGate == nil {
		issues = append(issues, ValidationIssue{Code: "missing_human_gate", Message: "human_gate is required"})
	}
	return issues
}

func containsString(values []string, want string) bool {
	for _, value := range values {
		if value == want {
			return true
		}
	}
	return false
}
