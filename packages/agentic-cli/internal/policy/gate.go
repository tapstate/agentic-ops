package policy

func RequiresHumanGate(operation string) bool {
	switch operation {
	case "prepare_pr", "fix_pr_comments", "request_owner_confirmation":
		return true
	default:
		return false
	}
}
