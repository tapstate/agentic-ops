package jira

import (
	"testing"

	"github.com/tapstate/agentic-ops/packages/agentic-cli/internal/profile"
)

func TestValidateTakeoverAcceptsMappedIssue(t *testing.T) {
	decision := ValidateTakeover(validIssue(), validTakeoverProfile(), "current-user", "agent-1")
	if !decision.OK {
		t.Fatalf("decision = %+v", decision)
	}
	if decision.TaskClass != "technical_task" {
		t.Fatalf("TaskClass = %s", decision.TaskClass)
	}
	if decision.ProcessID != "development_change_v1" {
		t.Fatalf("ProcessID = %s", decision.ProcessID)
	}
}

func TestValidateTakeoverBlocksMissingTargetRepo(t *testing.T) {
	issue := validIssue()
	issue.TargetRepo = ""

	decision := ValidateTakeover(issue, validTakeoverProfile(), "current-user", "agent-1")
	if decision.OK {
		t.Fatalf("decision OK, want blocked")
	}
	if decision.Code != "missing_target_repo" {
		t.Fatalf("Code = %s", decision.Code)
	}
}

func TestValidateTakeoverBlocksOwnerMismatch(t *testing.T) {
	issue := validIssue()
	issue.Owner = "other-user"

	decision := ValidateTakeover(issue, validTakeoverProfile(), "current-user", "agent-1")
	if decision.Code != "owner_mismatch" {
		t.Fatalf("Code = %s", decision.Code)
	}
}

func TestValidateTakeoverBlocksAgentConflict(t *testing.T) {
	issue := validIssue()
	issue.CurrentAgentID = "agent-2"

	decision := ValidateTakeover(issue, validTakeoverProfile(), "current-user", "agent-1")
	if decision.Code != "agent_ownership_conflict" {
		t.Fatalf("Code = %s", decision.Code)
	}
}

func TestValidateTakeoverBlocksUnknownStatus(t *testing.T) {
	issue := validIssue()
	issue.Status = "Custom Review"

	decision := ValidateTakeover(issue, validTakeoverProfile(), "current-user", "agent-1")
	if decision.Code != "unknown_jira_status" {
		t.Fatalf("Code = %s", decision.Code)
	}
}

func validIssue() Issue {
	return Issue{
		Key:                "TAP-123",
		Summary:            "修复示例任务",
		Owner:              "current-user",
		Assignee:           "current-user",
		IssueType:          "Task",
		Status:             "To Do",
		TargetRepo:         "tapstate/example-repo",
		AcceptanceCriteria: "单元测试通过",
		VerificationMethod: "go test ./...",
		RiskLevel:          "low",
	}
}

func validTakeoverProfile() profile.Profile {
	return profile.Profile{
		Workspace: "tapstate",
		TaskClassMapping: profile.TaskClassMapping{
			IssueTypes: map[string]string{
				"Task": "technical_task",
			},
		},
		StandardProcessMapping: map[string]string{
			"technical_task": "development_change_v1",
		},
		StatusMapping: map[string]string{
			"To Do": "waiting_takeover",
		},
	}
}
