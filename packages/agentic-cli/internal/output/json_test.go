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
	if got["task_type"] != "unknown" {
		t.Fatalf("task_type = %v", got["task_type"])
	}
	if got["current_stage"] != "failed" {
		t.Fatalf("current_stage = %v", got["current_stage"])
	}
	if got["agentic_next_action"] != "ask_owner" {
		t.Fatalf("agentic_next_action = %v", got["agentic_next_action"])
	}
	if _, exists := got["next_action"]; exists {
		t.Fatalf("legacy next_action must not be present: %#v", got)
	}
}

func TestFailureWithContextIncludesTaskProgress(t *testing.T) {
	got := FailureWithContext("takeover_task", FailureContext{
		Code:                "missing_jira_field",
		Message:             "Jira 卡片缺少目标仓库信息",
		RequiredHumanAction: "请补充目标仓库",
		TaskType:            "task_takeover",
		CurrentStage:        "takeover_gate",
		AgenticNextAction:   "ask_owner",
	})
	if got["code"] != "missing_jira_field" {
		t.Fatalf("code = %v", got["code"])
	}
	if got["task_type"] != "task_takeover" {
		t.Fatalf("task_type = %v", got["task_type"])
	}
	if got["current_stage"] != "takeover_gate" {
		t.Fatalf("current_stage = %v", got["current_stage"])
	}
	if got["agentic_next_action"] != "ask_owner" {
		t.Fatalf("agentic_next_action = %v", got["agentic_next_action"])
	}
	if _, exists := got["next_action"]; exists {
		t.Fatalf("legacy next_action must not be present: %#v", got)
	}
}
