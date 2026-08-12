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

func TestReadSkipsIncompleteTakeoverAndUsesLatestCompleteAnchor(t *testing.T) {
	incomplete := takeoverEvent()
	incomplete.TaskClass = ""
	incomplete.ProcessID = ""
	incomplete.TargetRepo = ""

	latest := takeoverEvent()
	latest.CurrentStage = "implementation"
	latest.AgenticNextAction = "continue_development"

	got, err := Read(
		[]feedback.Event{incomplete, latest},
		Query{AgenticRunID: "run-1", Workspace: "tapstate", AgentID: "agent-1"},
	)
	if err != nil {
		t.Fatalf("Read error = %v", err)
	}
	if got.CurrentStage != "implementation" || got.AgenticNextAction != "continue_development" {
		t.Fatalf("Context = %#v", got)
	}
}

func TestReadLatestCompleteAnchorIgnoresEarlierStateBearingEvents(t *testing.T) {
	older := takeoverEvent()
	beforeLatest := feedback.Event{
		Workspace:         "tapstate",
		AgenticRunID:      "run-1",
		IssueKey:          "TAP-123",
		Operation:         "prepare_pr",
		CurrentStage:      "pr_created",
		AgenticNextAction: "check_ci",
		OK:                true,
	}
	latest := takeoverEvent()
	latest.CurrentStage = "implementation"
	latest.AgenticNextAction = "continue_development"
	afterLatest := feedback.Event{
		Workspace:         "tapstate",
		AgenticRunID:      "run-1",
		IssueKey:          "TAP-123",
		Operation:         "write_evidence",
		CurrentStage:      "evidence_written",
		AgenticNextAction: "request_owner_confirmation",
		OK:                true,
	}

	got, err := Read(
		[]feedback.Event{older, beforeLatest, latest, afterLatest},
		Query{AgenticRunID: "run-1", Workspace: "tapstate", AgentID: "agent-1"},
	)
	if err != nil {
		t.Fatalf("Read error = %v", err)
	}
	if got.CurrentStage != "evidence_written" || got.AgenticNextAction != "request_owner_confirmation" {
		t.Fatalf("Context = %#v", got)
	}

	withoutTail, err := Read(
		[]feedback.Event{older, beforeLatest, latest},
		Query{AgenticRunID: "run-1", Workspace: "tapstate", AgentID: "agent-1"},
	)
	if err != nil {
		t.Fatalf("Read without tail error = %v", err)
	}
	if withoutTail.CurrentStage != "implementation" || withoutTail.AgenticNextAction != "continue_development" {
		t.Fatalf("Context before latest anchor leaked = %#v", withoutTail)
	}
}

func TestReadLatestTakeoverStillRejectsIdentityConflict(t *testing.T) {
	tests := []struct {
		name   string
		mutate func(*feedback.Event)
		want   error
	}{
		{name: "workspace conflict", mutate: func(event *feedback.Event) { event.Workspace = "other" }, want: ErrWorkspaceMismatch},
		{name: "agent conflict", mutate: func(event *feedback.Event) { event.AgentID = "agent-2" }, want: ErrLocalStateMismatch},
		{name: "agentic id conflict", mutate: func(event *feedback.Event) { event.AgenticID = "agent-2" }, want: ErrLocalStateMismatch},
	}

	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			latest := takeoverEvent()
			test.mutate(&latest)
			_, err := Read(
				[]feedback.Event{takeoverEvent(), latest},
				Query{AgenticRunID: "run-1", Workspace: "tapstate", AgentID: "agent-1"},
			)
			if !errors.Is(err, test.want) {
				t.Fatalf("Read error = %v, want %v", err, test.want)
			}
		})
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

func TestReadRejectsMissingTargetRepoEvenWhenLaterEventSuppliesIt(t *testing.T) {
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

	_, err := Read(
		[]feedback.Event{anchor, later},
		Query{AgenticRunID: "run-1", Workspace: "tapstate", AgentID: "agent-1"},
	)
	if !errors.Is(err, ErrLocalStateMismatch) {
		t.Fatalf("Read error = %v, want %v", err, ErrLocalStateMismatch)
	}
}

func TestReadRejectsTakeoverMissingRequiredAnchorField(t *testing.T) {
	tests := []struct {
		name   string
		mutate func(*feedback.Event)
	}{
		{name: "takeover time", mutate: func(event *feedback.Event) { event.AgenticTakeoverAt = "" }},
		{name: "heartbeat time", mutate: func(event *feedback.Event) { event.AgenticHeartbeatAt = "" }},
		{name: "target repo", mutate: func(event *feedback.Event) { event.TargetRepo = "" }},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			event := takeoverEvent()
			test.mutate(&event)
			_, err := Read([]feedback.Event{event}, Query{AgenticRunID: "run-1", Workspace: "tapstate", AgentID: "agent-1"})
			if !errors.Is(err, ErrLocalStateMismatch) {
				t.Fatalf("Read error = %v, want %v", err, ErrLocalStateMismatch)
			}
		})
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
		Workspace:          "tapstate",
		AgenticRunID:       "run-1",
		IssueKey:           "TAP-123",
		Operation:          "takeover_task",
		CurrentStage:       "takeover_started",
		AgenticNextAction:  "proceed",
		AgentID:            "agent-1",
		AgenticID:          "agent-1",
		AgenticTakeoverAt:  "2026-07-21T10:30:12Z",
		AgenticHeartbeatAt: "2026-07-21T10:30:12Z",
		TaskClass:          "technical_task",
		ProcessID:          "development_change_v1",
		TargetRepo:         "tapstate/example-repo",
		OK:                 true,
	}
}
