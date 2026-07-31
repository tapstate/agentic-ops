package jira

import (
	"testing"

	"github.com/tapstate/agentic-ops/packages/agentic-cli/internal/contract"
	"github.com/tapstate/agentic-ops/packages/agentic-cli/internal/process"
	"github.com/tapstate/agentic-ops/packages/agentic-cli/internal/profile"
	"github.com/tapstate/agentic-ops/packages/agentic-cli/internal/runcontext"
)

func TestValidateResumeAllowsMatchingFacts(t *testing.T) {
	decision := ValidateResume(validResumeInput())

	if !decision.OK {
		t.Fatalf("Decision = %#v", decision)
	}
	if decision.StandardProcessStage != "waiting_takeover" {
		t.Fatalf("StandardProcessStage = %q", decision.StandardProcessStage)
	}
	if decision.TargetRepo != "tapstate/example-repo" {
		t.Fatalf("TargetRepo = %q", decision.TargetRepo)
	}
}

func TestValidateResumeChecksRealJiraOwnership(t *testing.T) {
	tests := []struct {
		name         string
		change       func(*ResumeInput)
		wantOK       bool
		wantCode     string
		wantRequired bool
		wantWritable bool
	}{
		{
			name: "assignee changed",
			change: func(input *ResumeInput) {
				input.Issue.Assignee = "other-user"
			},
			wantCode:     "assignee_changed",
			wantRequired: true,
		},
		{
			name: "agent binding lost",
			change: func(input *ResumeInput) {
				input.Issue.AgenticID = ""
			},
			wantCode:     "agent_binding_lost",
			wantRequired: true,
			wantWritable: true,
		},
		{
			name: "other agent",
			change: func(input *ResumeInput) {
				input.Issue.AgenticID = "agent-2"
			},
			wantCode:     "agent_ownership_conflict",
			wantRequired: true,
		},
		{
			name: "fake adapter skips remote binding",
			change: func(input *ResumeInput) {
				input.AdapterMode = "fake"
				input.Issue.AgenticID = ""
			},
			wantOK: true,
		},
	}

	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			input := validResumeInput()
			test.change(&input)

			decision := ValidateResume(input)

			if decision.OK != test.wantOK ||
				decision.Code != test.wantCode ||
				decision.JiraFeedbackRequired != test.wantRequired ||
				decision.JiraFeedbackWriteAllowed != test.wantWritable {
				t.Fatalf("Decision = %#v", decision)
			}
		})
	}
}

func TestValidateResumeRejectsInvalidRecoveryFacts(t *testing.T) {
	tests := []struct {
		name         string
		change       func(*ResumeInput)
		wantCode     string
		wantRequired bool
		wantWritable bool
	}{
		{
			name: "terminal run",
			change: func(input *ResumeInput) {
				input.Context.Terminal = true
			},
			wantCode: "terminal_run",
		},
		{
			name: "human gate pending",
			change: func(input *ResumeInput) {
				input.Context.HumanGatePending = true
			},
			wantCode:     "human_gate_pending",
			wantRequired: true,
			wantWritable: true,
		},
		{
			name: "operation stage not allowed",
			change: func(input *ResumeInput) {
				input.Context.CurrentStage = "evidence_written"
			},
			wantCode:     "resume_stage_not_allowed",
			wantRequired: true,
			wantWritable: true,
		},
		{
			name: "issue mismatch",
			change: func(input *ResumeInput) {
				input.Issue.Key = "TAP-456"
			},
			wantCode: "issue_mismatch",
		},
		{
			name: "target repo missing",
			change: func(input *ResumeInput) {
				input.Issue.TargetRepo = ""
			},
			wantCode:     "target_repo_missing",
			wantRequired: true,
			wantWritable: true,
		},
		{
			name: "target repo changed",
			change: func(input *ResumeInput) {
				input.Issue.TargetRepo = "tapstate/other-repo"
			},
			wantCode:     "target_repo_changed",
			wantRequired: true,
			wantWritable: true,
		},
		{
			name: "standard process missing",
			change: func(input *ResumeInput) {
				input.ProcessRegistry = map[string]process.Process{}
			},
			wantCode:     "standard_process_not_found",
			wantRequired: true,
			wantWritable: true,
		},
		{
			name: "task class process mismatch",
			change: func(input *ResumeInput) {
				registered := input.ProcessRegistry[input.Context.ProcessID]
				registered.TaskClasses = []string{"bug_fix"}
				input.ProcessRegistry[input.Context.ProcessID] = registered
			},
			wantCode:     "task_class_process_mismatch",
			wantRequired: true,
			wantWritable: true,
		},
		{
			name: "lifecycle mapping gap",
			change: func(input *ResumeInput) {
				input.Profile.StatusMapping = map[string]string{}
			},
			wantCode:     "lifecycle_mapping_gap",
			wantRequired: true,
			wantWritable: true,
		},
		{
			name: "invalid process stage",
			change: func(input *ResumeInput) {
				input.Profile.StatusMapping["To Do"] = "custom_review"
			},
			wantCode:     "invalid_process_stage",
			wantRequired: true,
			wantWritable: true,
		},
		{
			name: "terminal process stage",
			change: func(input *ResumeInput) {
				input.Issue.Status = "Done"
				input.Profile.StatusMapping["Done"] = "completed"
			},
			wantCode: "terminal_run",
		},
	}

	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			input := validResumeInput()
			test.change(&input)

			decision := ValidateResume(input)

			if decision.OK ||
				decision.Code != test.wantCode ||
				decision.JiraFeedbackRequired != test.wantRequired ||
				decision.JiraFeedbackWriteAllowed != test.wantWritable {
				t.Fatalf("Decision = %#v", decision)
			}
			if decision.Message == "" || decision.RequiredHumanAction == "" {
				t.Fatalf("Decision lacks Chinese guidance: %#v", decision)
			}
		})
	}
}

func TestValidateResumeDoesNotAuthorizeEarlyFeedbackAfterOwnershipLoss(t *testing.T) {
	tests := []struct {
		name     string
		change   func(*ResumeInput)
		wantCode string
	}{
		{
			name: "human gate with changed assignee",
			change: func(input *ResumeInput) {
				input.Context.HumanGatePending = true
				input.Issue.Assignee = "other-user"
			},
			wantCode: "human_gate_pending",
		},
		{
			name: "disallowed stage with other agent",
			change: func(input *ResumeInput) {
				input.Context.CurrentStage = "evidence_written"
				input.Issue.AgenticID = "agent-2"
			},
			wantCode: "resume_stage_not_allowed",
		},
	}

	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			input := validResumeInput()
			test.change(&input)

			decision := ValidateResume(input)

			if decision.Code != test.wantCode ||
				!decision.JiraFeedbackRequired ||
				decision.JiraFeedbackWriteAllowed {
				t.Fatalf("Decision = %#v", decision)
			}
		})
	}
}

func TestValidateResumeBackfillsMissingHistoricalTargetRepo(t *testing.T) {
	input := validResumeInput()
	input.Context.TargetRepo = ""

	decision := ValidateResume(input)

	if !decision.OK || decision.TargetRepo != "tapstate/example-repo" {
		t.Fatalf("Decision = %#v", decision)
	}
}

func validResumeInput() ResumeInput {
	return ResumeInput{
		Context: runcontext.Context{
			Workspace:         "tapstate",
			AgenticRunID:      "run-1",
			IssueKey:          "TAP-123",
			AgentID:           "agent-1",
			AgenticID:         "agent-1",
			TaskClass:         "technical_task",
			ProcessID:         "development_change_v1",
			TargetRepo:        "tapstate/example-repo",
			CurrentStage:      "takeover_started",
			AgenticNextAction: "proceed",
		},
		Issue: Issue{
			Key:        "TAP-123",
			Assignee:   "user-1",
			IssueType:  "Task",
			Status:     "To Do",
			TargetRepo: "tapstate/example-repo",
			AgenticID:  "agent-1",
		},
		CurrentUser: "user-1",
		AgentID:     "agent-1",
		AdapterMode: "real",
		Profile: profile.Profile{
			StatusMapping: map[string]string{"To Do": "waiting_takeover"},
		},
		Contract: contract.Operation{
			AllowedStages: []string{"takeover_started", "blocked"},
		},
		ProcessRegistry: map[string]process.Process{
			"development_change_v1": {
				ProcessID:   "development_change_v1",
				TaskClasses: []string{"feature_change", "bug_fix", "technical_task"},
				EntryStage:  "waiting_takeover",
				Stages: []process.Stage{
					{ID: "waiting_takeover"},
					{ID: "implementation"},
					{ID: "completed"},
				},
			},
		},
	}
}
