package process

type ValidationIssue struct {
	Code    string
	Message string
}

func Validate(process Process) []ValidationIssue {
	var issues []ValidationIssue
	if process.ProcessID == "" {
		issues = append(issues, ValidationIssue{Code: "missing_process_id", Message: "process_id is required"})
	}
	if process.EntryStage == "" {
		issues = append(issues, ValidationIssue{Code: "missing_entry_stage", Message: "entry_stage is required"})
	}
	if len(process.Stages) == 0 {
		issues = append(issues, ValidationIssue{Code: "missing_stages", Message: "stages are required"})
	}
	if process.EntryStage != "" && !process.HasStage(process.EntryStage) {
		issues = append(issues, ValidationIssue{Code: "missing_entry_stage", Message: "entry_stage must exist in stages"})
	}
	return issues
}
