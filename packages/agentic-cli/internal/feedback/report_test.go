package feedback

import (
	"os"
	"strings"
	"testing"
)

func TestFilterEventsMatchesWorkspaceDateAndAttributes(t *testing.T) {
	events := []Event{
		{Timestamp: "2026-07-21T10:00:00Z", Workspace: "tapstate", AgenticRunID: "run-1", IssueKey: "TAP-123", TaskType: "defect", Code: "missing_jira_field"},
		{Timestamp: "2026-07-21T11:00:00Z", Workspace: "tapstate", AgenticRunID: "run-2", IssueKey: "TAP-123", TaskType: "feature", Code: "policy_gate_required"},
		{Timestamp: "2026-07-22T10:00:00Z", Workspace: "other", AgenticRunID: "run-3", IssueKey: "TAP-999", TaskType: "defect", Code: "missing_jira_field"},
	}

	got, err := FilterEvents(events, EventFilter{
		Workspace:    "tapstate",
		Date:         "2026-07-21",
		AgenticRunID: "run-1",
		IssueKey:     "TAP-123",
		TaskType:     "defect",
		Code:         "missing_jira_field",
	})
	if err != nil {
		t.Fatalf("FilterEvents error = %v", err)
	}
	if len(got) != 1 || got[0].AgenticRunID != "run-1" {
		t.Fatalf("filtered events = %+v", got)
	}
}

func TestAnalyzeGroupsFailureHumanGateAndMissingFieldPatterns(t *testing.T) {
	analysis := Analyze([]Event{
		{OK: false, Code: "missing_jira_field", MissingField: "target_repo", RequiresHumanAction: true, Gate: "takeover_gate", Operation: "takeover_task"},
		{OK: false, Code: "missing_jira_field", MissingField: "target_repo", RequiresHumanAction: true, Gate: "takeover_gate", Operation: "takeover_task"},
		{OK: false, Code: "policy_gate_required", RequiresHumanAction: true, Gate: "write_evidence", Operation: "write_evidence"},
		{OK: true, Operation: "write_evidence"},
	})

	if analysis.Runs != 4 {
		t.Fatalf("Runs = %d", analysis.Runs)
	}
	if len(analysis.FailurePatterns) == 0 || analysis.FailurePatterns[0].Key != "missing_jira_field" || analysis.FailurePatterns[0].Count != 2 {
		t.Fatalf("failure patterns = %+v", analysis.FailurePatterns)
	}
	if len(analysis.HumanGateHotspots) == 0 || analysis.HumanGateHotspots[0].Key != "takeover_gate" || analysis.HumanGateHotspots[0].Count != 2 {
		t.Fatalf("human gate hotspots = %+v", analysis.HumanGateHotspots)
	}
	if len(analysis.MissingFieldTrends) != 1 || analysis.MissingFieldTrends[0].Key != "target_repo" || analysis.MissingFieldTrends[0].Count != 2 {
		t.Fatalf("missing field trends = %+v", analysis.MissingFieldTrends)
	}
}

func TestProposeReturnsStructuredSuggestionsWithoutChangingEvents(t *testing.T) {
	proposals := Propose([]Event{
		{OK: false, Code: "missing_jira_field", MissingField: "target_repo"},
		{OK: false, Code: "policy_gate_required", RequiresHumanAction: true},
	})

	if len(proposals) != 2 {
		t.Fatalf("proposals = %+v", proposals)
	}
	if proposals[0].RecommendedAsset == "" || proposals[0].EvidenceCount != 1 {
		t.Fatalf("first proposal = %+v", proposals[0])
	}
	if proposals[1].RecommendedAsset == "" || proposals[1].EvidenceCount != 1 {
		t.Fatalf("second proposal = %+v", proposals[1])
	}
}

func TestSummarizeCountsRuns(t *testing.T) {
	got := Summarize([]Event{
		{OK: true},
		{OK: false, RequiresHumanAction: true, MissingField: "target_repo"},
		{OK: false, AgenticNextAction: "retry"},
	})
	if got.Runs != 3 {
		t.Fatalf("Runs = %d", got.Runs)
	}
	if got.Succeeded != 1 || got.Blocked != 1 || got.Failed != 1 {
		t.Fatalf("report = %+v", got)
	}
	if got.MissingFields["target_repo"] != 1 {
		t.Fatalf("MissingFields = %+v", got.MissingFields)
	}
}

func TestWriteMarkdownCreatesFeedbackReport(t *testing.T) {
	path := t.TempDir() + "/reports/2026-07-21.md"
	report := Report{Runs: 2, Succeeded: 1, Blocked: 1, MissingFields: map[string]int{"target_repo": 1}}
	if err := WriteMarkdown(path, "tapstate", "2026-07-21", report); err != nil {
		t.Fatalf("WriteMarkdown error = %v", err)
	}
	data, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("ReadFile error = %v", err)
	}
	if !strings.Contains(string(data), "runs: 2") || !strings.Contains(string(data), "target_repo: 1") {
		t.Fatalf("content = %s", string(data))
	}
}
