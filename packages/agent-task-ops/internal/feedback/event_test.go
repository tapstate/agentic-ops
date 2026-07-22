package feedback

import (
	"os"
	"strings"
	"testing"
	"time"
)

func TestRunIDUsesIssueTaskAndTime(t *testing.T) {
	now := time.Date(2026, 7, 21, 10, 30, 12, 0, time.UTC)
	got := RunID("TAP-123", "task_takeover", now, "a8f3")
	if got != "TAP-123-takeover-20260721103012-a8f3" {
		t.Fatalf("RunID = %s", got)
	}
}

func TestAppendEventWritesNDJSON(t *testing.T) {
	path := t.TempDir() + "/events.ndjson"
	err := AppendEvent(path, Event{
		Workspace:    "tapstate",
		RunID:        "run-1",
		TaskType:     "task_takeover",
		Operation:    "takeover_task",
		CurrentStage: "takeover_gate",
		NextAction:   "ask_owner",
	})
	if err != nil {
		t.Fatalf("AppendEvent error = %v", err)
	}
	data, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("ReadFile error = %v", err)
	}
	if !strings.Contains(string(data), `"current_stage":"takeover_gate"`) {
		t.Fatalf("event = %s", string(data))
	}
}

func TestReadEventsReadsNDJSON(t *testing.T) {
	path := t.TempDir() + "/events.ndjson"
	err := AppendEvent(path, Event{Workspace: "tapstate", RunID: "run-1", Operation: "takeover_task", OK: true})
	if err != nil {
		t.Fatalf("AppendEvent error = %v", err)
	}
	events, err := ReadEvents(path)
	if err != nil {
		t.Fatalf("ReadEvents error = %v", err)
	}
	if len(events) != 1 {
		t.Fatalf("len = %d", len(events))
	}
	if events[0].Operation != "takeover_task" {
		t.Fatalf("Operation = %s", events[0].Operation)
	}
}
