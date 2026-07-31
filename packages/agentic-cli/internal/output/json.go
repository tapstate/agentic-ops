package output

func Success(operation string, payload map[string]any) map[string]any {
	result := map[string]any{
		"ok":        true,
		"operation": operation,
	}
	for key, value := range payload {
		result[key] = value
	}
	return result
}

type FailureContext struct {
	Code                string
	Message             string
	RequiredHumanAction string
	TaskType            string
	CurrentStage        string
	AgenticNextAction   string
}

func Failure(operation string, code string, message string, requiredHumanAction string) map[string]any {
	return FailureWithContext(operation, FailureContext{
		Code:                code,
		Message:             message,
		RequiredHumanAction: requiredHumanAction,
		TaskType:            "unknown",
		CurrentStage:        "failed",
		AgenticNextAction:   "ask_owner",
	})
}

func FailureWithContext(operation string, context FailureContext) map[string]any {
	if context.TaskType == "" {
		context.TaskType = "unknown"
	}
	if context.CurrentStage == "" {
		context.CurrentStage = "failed"
	}
	if context.AgenticNextAction == "" {
		context.AgenticNextAction = "ask_owner"
	}
	if context.RequiredHumanAction == "" {
		context.RequiredHumanAction = "请联系 AgenticOps 维护者处理"
	}
	result := map[string]any{
		"ok":                    false,
		"operation":             operation,
		"code":                  context.Code,
		"message":               context.Message,
		"task_type":             context.TaskType,
		"current_stage":         context.CurrentStage,
		"agentic_next_action":   context.AgenticNextAction,
		"required_human_action": context.RequiredHumanAction,
	}
	return result
}
