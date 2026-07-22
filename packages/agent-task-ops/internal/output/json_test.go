package output

import "testing"

func TestSuccessIncludesOperationAndPayload(t *testing.T) {
	got := Success("agent_init", map[string]any{"workspace": "tapstate"})
	if got["ok"] != true {
		t.Fatalf("ok = %v, want true", got["ok"])
	}
	if got["operation"] != "agent_init" {
		t.Fatalf("operation = %v", got["operation"])
	}
	if got["workspace"] != "tapstate" {
		t.Fatalf("workspace = %v", got["workspace"])
	}
}

func TestFailureIncludesStableCode(t *testing.T) {
	got := Failure("takeover_task", "missing_target_repo", "缺少目标仓库", "请补充 target_repo")
	if got["ok"] != false {
		t.Fatalf("ok = %v, want false", got["ok"])
	}
	if got["code"] != "missing_target_repo" {
		t.Fatalf("code = %v", got["code"])
	}
	if got["required_human_action"] != "请补充 target_repo" {
		t.Fatalf("required_human_action = %v", got["required_human_action"])
	}
}
