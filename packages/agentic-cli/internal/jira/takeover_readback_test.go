package jira

import (
	"errors"
	"testing"

	"github.com/tapstate/agentic-ops/packages/agentic-cli/internal/profile"
)

func TestValidateTakeoverReadback(t *testing.T) {
	workspaceProfile := profile.Profile{
		StatusMapping: map[string]string{
			"To Do":       "waiting_takeover",
			"In Progress": "implementation",
		},
	}
	expectation := TakeoverExpectation{
		TargetStage:        "implementation",
		AgenticID:          "agent-1",
		AgenticRunID:       "run-1",
		AgenticTakeoverAt:  "2026-08-11T10:00:00Z",
		AgenticHeartbeatAt: "2026-08-11T10:00:00Z",
	}
	matchingIssue := Issue{
		Key:       "AO-6",
		Status:    "In Progress",
		AgenticID: "agent-1",
		FormValues: map[string]string{
			"agentic_run_id":       "run-1",
			"agentic_takeover_at":  "2026-08-11T10:00:00Z",
			"agentic_heartbeat_at": "2026-08-11T10:00:00Z",
		},
	}

	if err := ValidateTakeoverReadback(matchingIssue, workspaceProfile, expectation); err != nil {
		t.Fatalf("matching readback error = %v", err)
	}

	tests := []struct {
		name   string
		mutate func(*Issue)
	}{
		{name: "status", mutate: func(issue *Issue) { issue.Status = "To Do" }},
		{name: "agentic id", mutate: func(issue *Issue) { issue.AgenticID = "other-agent" }},
		{name: "run id", mutate: func(issue *Issue) { issue.FormValues["agentic_run_id"] = "other-run" }},
		{name: "takeover at", mutate: func(issue *Issue) { issue.FormValues["agentic_takeover_at"] = "2026-08-11T10:00:01Z" }},
		{name: "heartbeat at", mutate: func(issue *Issue) { issue.FormValues["agentic_heartbeat_at"] = "2026-08-11T10:00:01Z" }},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			issue := matchingIssue
			issue.FormValues = map[string]string{}
			for key, value := range matchingIssue.FormValues {
				issue.FormValues[key] = value
			}
			test.mutate(&issue)
			err := ValidateTakeoverReadback(issue, workspaceProfile, expectation)
			if !errors.Is(err, ErrTakeoverReadbackMismatch) {
				t.Fatalf("error = %v, want %v", err, ErrTakeoverReadbackMismatch)
			}
		})
	}
}
