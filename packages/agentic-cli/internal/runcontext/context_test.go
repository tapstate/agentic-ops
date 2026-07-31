package runcontext

import (
	"errors"
	"path/filepath"
	"testing"

	"github.com/tapstate/agentic-ops/packages/agentic-cli/internal/feedback"
)

func TestReadRestoresTakeoverContext(t *testing.T) {
	got, err := Read([]feedback.Event{takeoverEvent()}, Query{AgenticRunID: "run-1", Workspace: "tapstate", AgentID: "agent-1"})
	if err != nil {
		t.Fatalf("Read error = %v", err)
	}
	if got.Workspace != "tapstate" ||
		got.AgenticRunID != "run-1" ||
		got.IssueKey != "TAP-123" ||
		got.AgentID != "agent-1" ||
		got.AgenticID != "agent-1" ||
		got.TaskClass != "technical_task" ||
		got.ProcessID != "development_change_v1" ||
		got.TargetRepo != "tapstate/example-repo" ||
		got.CurrentStage != "takeover_started" ||
		got.AgenticNextAction != "proceed" {
		t.Fatalf("Context = %#v", got)
	}
}

func TestReadRejectsImmutableFieldConflict(t *testing.T) {
	events := []feedback.Event{
		takeoverEvent(),
		{
			Workspace:         "tapstate",
			AgenticRunID:      "run-1",
			IssueKey:          "TAP-123",
			Operation:         "prepare_pr",
			CurrentStage:      "pr_plan_prepared",
			AgenticNextAction: "ask_owner_to_push_and_create_pr",
			TargetRepo:        "tapstate/other-repo",
			OK:                true,
		},
	}

	_, err := Read(events, Query{AgenticRunID: "run-1", Workspace: "tapstate", AgentID: "agent-1"})
	if !errors.Is(err, ErrLocalStateMismatch) {
		t.Fatalf("Read error = %v, want %v", err, ErrLocalStateMismatch)
	}
}

func TestReadReturnsStableLocalStateErrors(t *testing.T) {
	incomplete := takeoverEvent()
	incomplete.ProcessID = ""

	tests := []struct {
		name   string
		events []feedback.Event
		query  Query
		want   error
	}{
		{
			name:   "run not found",
			events: []feedback.Event{takeoverEvent()},
			query:  Query{AgenticRunID: "other-run", Workspace: "tapstate", AgentID: "agent-1"},
			want:   ErrRunNotFound,
		},
		{
			name:   "workspace mismatch",
			events: []feedback.Event{takeoverEvent()},
			query:  Query{AgenticRunID: "run-1", Workspace: "other", AgentID: "agent-1"},
			want:   ErrWorkspaceMismatch,
		},
		{
			name:   "incomplete takeover",
			events: []feedback.Event{incomplete},
			query:  Query{AgenticRunID: "run-1", Workspace: "tapstate", AgentID: "agent-1"},
			want:   ErrLocalStateMismatch,
		},
	}

	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			_, err := Read(test.events, test.query)
			if !errors.Is(err, test.want) {
				t.Fatalf("Read error = %v, want %v", err, test.want)
			}
		})
	}
}

func TestReadBackfillsMissingTargetRepoFromLaterEvent(t *testing.T) {
	anchor := takeoverEvent()
	anchor.TargetRepo = ""
	later := feedback.Event{
		Workspace:         "tapstate",
		AgenticRunID:      "run-1",
		IssueKey:          "TAP-123",
		Operation:         "prepare_pr",
		CurrentStage:      "pr_plan_prepared",
		AgenticNextAction: "ask_owner_to_push_and_create_pr",
		TargetRepo:        "tapstate/example-repo",
		OK:                true,
	}

	got, err := Read(
		[]feedback.Event{anchor, later},
		Query{AgenticRunID: "run-1", Workspace: "tapstate", AgentID: "agent-1"},
	)
	if err != nil {
		t.Fatalf("Read error = %v", err)
	}
	if got.TargetRepo != "tapstate/example-repo" {
		t.Fatalf("TargetRepo = %q", got.TargetRepo)
	}
}

func TestReadUsesLatestStateBearingEventAndIgnoresAuditEvents(t *testing.T) {
	events := []feedback.Event{
		takeoverEvent(),
		{
			Workspace:         "tapstate",
			AgenticRunID:      "run-1",
			IssueKey:          "TAP-123",
			Operation:         "write_evidence",
			CurrentStage:      "evidence_written",
			AgenticNextAction: "request_owner_confirmation",
			OK:                true,
		},
		{
			Workspace:         "tapstate",
			AgenticRunID:      "run-1",
			IssueKey:          "TAP-123",
			Operation:         "add_task_comment",
			CurrentStage:      "jira_write_completed",
			AgenticNextAction: "inspect_by_agent",
			OK:                true,
		},
		{
			Workspace:         "tapstate",
			AgenticRunID:      "run-1",
			IssueKey:          "TAP-123",
			Operation:         "resume_takeover",
			CurrentStage:      "resume_gate",
			AgenticNextAction: "ask_owner",
			OK:                false,
		},
		{
			Workspace:         "tapstate",
			AgenticRunID:      "run-1",
			IssueKey:          "TAP-123",
			Operation:         "resume_takeover",
			CurrentStage:      "takeover_resumed",
			AgenticNextAction: "continue_development",
			OK:                true,
		},
	}

	got, err := Read(events, Query{AgenticRunID: "run-1", Workspace: "tapstate", AgentID: "agent-1"})
	if err != nil {
		t.Fatalf("Read error = %v", err)
	}
	if got.CurrentStage != "evidence_written" || got.AgenticNextAction != "request_owner_confirmation" {
		t.Fatalf("Context = %#v", got)
	}
}

func TestReadMarksTerminalAndHumanGateStates(t *testing.T) {
	tests := []struct {
		name             string
		event            feedback.Event
		wantTerminal     bool
		wantHumanPending bool
	}{
		{
			name: "terminal",
			event: feedback.Event{
				Workspace:         "tapstate",
				AgenticRunID:      "run-1",
				IssueKey:          "TAP-123",
				Operation:         "release_agent",
				CurrentStage:      "completed",
				AgenticNextAction: "task_audit_submitted",
				OK:                true,
			},
			wantTerminal: true,
		},
		{
			name: "human gate",
			event: feedback.Event{
				Workspace:           "tapstate",
				AgenticRunID:        "run-1",
				IssueKey:            "TAP-123",
				Operation:           "write_evidence",
				CurrentStage:        "evidence_write_gate",
				AgenticNextAction:   "ask_owner",
				RequiresHumanAction: true,
			},
			wantHumanPending: true,
		},
	}

	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			got, err := Read(
				[]feedback.Event{takeoverEvent(), test.event},
				Query{AgenticRunID: "run-1", Workspace: "tapstate", AgentID: "agent-1"},
			)
			if err != nil {
				t.Fatalf("Read error = %v", err)
			}
			if got.Terminal != test.wantTerminal || got.HumanGatePending != test.wantHumanPending {
				t.Fatalf("Context = %#v", got)
			}
		})
	}
}

func TestReadFileAndErrorCode(t *testing.T) {
	path := filepath.Join(t.TempDir(), "events.ndjson")
	if err := feedback.AppendEvent(path, takeoverEvent()); err != nil {
		t.Fatalf("AppendEvent error = %v", err)
	}

	got, err := ReadFile(path, Query{AgenticRunID: "run-1", Workspace: "tapstate", AgentID: "agent-1"})
	if err != nil {
		t.Fatalf("ReadFile error = %v", err)
	}
	if got.IssueKey != "TAP-123" {
		t.Fatalf("Context = %#v", got)
	}

	tests := []struct {
		err  error
		want string
	}{
		{ErrRunNotFound, "run_not_found"},
		{ErrWorkspaceMismatch, "workspace_mismatch"},
		{ErrLocalStateMismatch, "local_state_mismatch"},
		{errors.New("read failed"), "event_read_failed"},
	}
	for _, test := range tests {
		if got := ErrorCode(test.err); got != test.want {
			t.Fatalf("ErrorCode(%v) = %s, want %s", test.err, got, test.want)
		}
	}
}

func takeoverEvent() feedback.Event {
	return feedback.Event{
		Workspace:         "tapstate",
		AgenticRunID:      "run-1",
		IssueKey:          "TAP-123",
		Operation:         "takeover_task",
		CurrentStage:      "takeover_started",
		AgenticNextAction: "proceed",
		AgentID:           "agent-1",
		AgenticID:         "agent-1",
		TaskClass:         "technical_task",
		ProcessID:         "development_change_v1",
		TargetRepo:        "tapstate/example-repo",
		OK:                true,
	}
}
