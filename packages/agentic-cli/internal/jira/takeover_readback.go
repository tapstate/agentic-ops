package jira

import (
	"errors"
	"fmt"

	"github.com/tapstate/agentic-ops/packages/agentic-cli/internal/profile"
)

var ErrTakeoverReadbackMismatch = errors.New("jira_takeover_readback_mismatch")

type TakeoverExpectation struct {
	TargetStage        string
	AgenticID          string
	AgenticRunID       string
	AgenticTakeoverAt  string
	AgenticHeartbeatAt string
}

func ValidateTakeoverReadback(issue Issue, workspaceProfile profile.Profile, expectation TakeoverExpectation) error {
	actualStage, mapped := workspaceProfile.StatusMapping[issue.Status]
	if !mapped || actualStage != expectation.TargetStage {
		return takeoverReadbackMismatch("status stage", expectation.TargetStage, actualStage)
	}
	if issue.AgenticID != expectation.AgenticID {
		return takeoverReadbackMismatch("agentic_id", expectation.AgenticID, issue.AgenticID)
	}
	checks := []struct {
		name string
		want string
		got  string
	}{
		{name: "agentic_run_id", want: expectation.AgenticRunID, got: issue.FormValues["agentic_run_id"]},
		{name: "agentic_takeover_at", want: expectation.AgenticTakeoverAt, got: issue.FormValues["agentic_takeover_at"]},
		{name: "agentic_heartbeat_at", want: expectation.AgenticHeartbeatAt, got: issue.FormValues["agentic_heartbeat_at"]},
	}
	for _, check := range checks {
		if check.got != check.want {
			return takeoverReadbackMismatch(check.name, check.want, check.got)
		}
	}
	return nil
}

func takeoverReadbackMismatch(field string, want string, got string) error {
	return fmt.Errorf("%w: %s expected %q, got %q", ErrTakeoverReadbackMismatch, field, want, got)
}
