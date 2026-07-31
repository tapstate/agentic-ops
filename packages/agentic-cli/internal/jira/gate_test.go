package jira

import (
	"testing"

	"github.com/tapstate/agentic-ops/packages/agentic-cli/internal/process"
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
	if decision.TaskClassSource != "issue_type:Task" {
		t.Fatalf("TaskClassSource = %s", decision.TaskClassSource)
	}
	if decision.ProcessID != "development_change_v1" {
		t.Fatalf("ProcessID = %s", decision.ProcessID)
	}
	if decision.TargetRepo != "tapstate/example-repo" {
		t.Fatalf("TargetRepo = %s", decision.TargetRepo)
	}
}

func TestValidateTakeoverAllowsMissingTargetRepoForAgentInspection(t *testing.T) {
	issue := validIssue()
	issue.TargetRepo = ""

	p := validTakeoverProfile()
	p.GitHub.Repositories = profile.RepositoryMapping{}

	decision := ValidateTakeover(issue, p, "current-user", "agent-1")
	if !decision.OK {
		t.Fatalf("decision = %+v", decision)
	}
}

func TestValidateTakeoverUsesRepositoryFallbackByComponent(t *testing.T) {
	issue := validIssue()
	issue.TargetRepo = ""
	issue.Components = []string{"api"}

	decision := ValidateTakeover(issue, validTakeoverProfile(), "current-user", "agent-1")
	if !decision.OK {
		t.Fatalf("decision = %+v", decision)
	}
	if decision.TargetRepo != "tapstate/tap-api" {
		t.Fatalf("TargetRepo = %s", decision.TargetRepo)
	}
}

func TestValidateTakeoverUsesRepositoryFallbackByLabel(t *testing.T) {
	issue := validIssue()
	issue.TargetRepo = ""
	issue.Labels = []string{"cli"}

	decision := ValidateTakeover(issue, validTakeoverProfile(), "current-user", "agent-1")
	if !decision.OK {
		t.Fatalf("decision = %+v", decision)
	}
	if decision.TargetRepo != "tapstate/agentic-ops" {
		t.Fatalf("TargetRepo = %s", decision.TargetRepo)
	}
}

func TestValidateTakeoverUsesRepositoryFallbackByIssueType(t *testing.T) {
	issue := validIssue()
	issue.TargetRepo = ""

	p := validTakeoverProfile()
	p.GitHub.Repositories.ByComponent = nil
	p.GitHub.Repositories.ByLabel = nil

	decision := ValidateTakeover(issue, p, "current-user", "agent-1")
	if !decision.OK {
		t.Fatalf("decision = %+v", decision)
	}
	if decision.TargetRepo != "tapstate/task-repo" {
		t.Fatalf("TargetRepo = %s", decision.TargetRepo)
	}
}

func TestValidateTakeoverUsesDefaultRepositoryFallback(t *testing.T) {
	issue := validIssue()
	issue.TargetRepo = ""

	p := validTakeoverProfile()
	p.GitHub.Repositories.ByComponent = nil
	p.GitHub.Repositories.ByLabel = nil
	p.GitHub.Repositories.ByIssueType = nil

	decision := ValidateTakeover(issue, p, "current-user", "agent-1")
	if !decision.OK {
		t.Fatalf("decision = %+v", decision)
	}
	if decision.TargetRepo != "tapstate/default-repo" {
		t.Fatalf("TargetRepo = %s", decision.TargetRepo)
	}
}

func TestValidateTakeoverUsesLabelTaskClassFallback(t *testing.T) {
	issue := validIssue()
	issue.IssueType = "Support"
	issue.Labels = []string{"investigation"}

	decision := ValidateTakeover(issue, validTakeoverProfile(), "current-user", "agent-1")
	if !decision.OK {
		t.Fatalf("decision = %+v", decision)
	}
	if decision.TaskClass != "investigation" {
		t.Fatalf("TaskClass = %s", decision.TaskClass)
	}
	if decision.TaskClassSource != "label:investigation" {
		t.Fatalf("TaskClassSource = %s", decision.TaskClassSource)
	}
	if decision.ProcessID != "investigation_v1" {
		t.Fatalf("ProcessID = %s", decision.ProcessID)
	}
}

func TestValidateTakeoverUsesComponentTaskClassFallback(t *testing.T) {
	issue := validIssue()
	issue.IssueType = "Support"
	issue.Components = []string{"ops"}

	decision := ValidateTakeover(issue, validTakeoverProfile(), "current-user", "agent-1")
	if !decision.OK {
		t.Fatalf("decision = %+v", decision)
	}
	if decision.TaskClass != "investigation" {
		t.Fatalf("TaskClass = %s", decision.TaskClass)
	}
	if decision.TaskClassSource != "component:ops" {
		t.Fatalf("TaskClassSource = %s", decision.TaskClassSource)
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
	issue.AgenticID = "agent-2"

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

func TestValidateTakeoverBlocksStatusOutsideProcessEntryStage(t *testing.T) {
	issue := validIssue()
	issue.Status = "In Progress"

	p := validTakeoverProfile()
	p.StatusMapping["In Progress"] = "implementation"

	decision := ValidateTakeoverWithProcesses(issue, p, "current-user", "agent-1", validTakeoverProcessRegistry())
	if decision.OK {
		t.Fatalf("decision OK, want blocked")
	}
	if decision.Code != "invalid_takeover_stage" {
		t.Fatalf("Code = %s", decision.Code)
	}
}

func validIssue() Issue {
	return Issue{
		Key:        "TAP-123",
		Summary:    "修复示例任务",
		Owner:      "current-user",
		Assignee:   "current-user",
		IssueType:  "Task",
		Status:     "To Do",
		TargetRepo: "tapstate/example-repo",
		FormValues: map[string]string{
			"problem_branch":      "develop",
			"target_branch":       "develop",
			"problem_summary":     "修复示例任务",
			"acceptance_criteria": "单元测试通过",
			"verification_method": "go test ./...",
			"risk_level":          "low",
		},
	}
}

func validTakeoverProcessRegistry() map[string]process.Process {
	return map[string]process.Process{
		"development_change_v1": {
			ProcessID:  "development_change_v1",
			EntryStage: "waiting_takeover",
			Stages: []process.Stage{
				{ID: "waiting_takeover"},
				{ID: "implementation"},
				{ID: "completed"},
			},
		},
		"investigation_v1": {
			ProcessID:  "investigation_v1",
			EntryStage: "waiting_takeover",
			Stages:     []process.Stage{{ID: "waiting_takeover"}},
		},
	}
}

func validTakeoverProfile() profile.Profile {
	return profile.Profile{
		Workspace: "tapstate",
		TaskClassMapping: profile.TaskClassMapping{
			IssueTypes: map[string]string{
				"Task": "technical_task",
			},
			Labels: map[string]string{
				"investigation": "investigation",
			},
			Components: map[string]string{
				"ops": "investigation",
			},
		},
		StandardProcessMapping: map[string]string{
			"technical_task": "development_change_v1",
			"investigation":  "investigation_v1",
		},
		StatusMapping: map[string]string{
			"To Do": "waiting_takeover",
		},
		GitHub: profile.GitHubConfig{
			Repositories: profile.RepositoryMapping{
				Default: "tapstate/default-repo",
				ByComponent: map[string]string{
					"api": "tapstate/tap-api",
				},
				ByLabel: map[string]string{
					"cli": "tapstate/agentic-ops",
				},
				ByIssueType: map[string]string{
					"Task": "tapstate/task-repo",
				},
			},
		},
	}
}
