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

func Failure(operation string, code string, message string, requiredHumanAction string) map[string]any {
	result := map[string]any{
		"ok":        false,
		"operation": operation,
		"code":      code,
		"message":   message,
	}
	if requiredHumanAction != "" {
		result["required_human_action"] = requiredHumanAction
	}
	return result
}
