package feedback

import (
	"os"
	"strings"
	"testing"
)

func TestSummarizeCountsRuns(t *testing.T) {
	got := Summarize([]Event{
		{OK: true},
		{OK: false, RequiresHumanAction: true},
		{OK: false, NextAction: "retry"},
	})
	if got.Runs != 3 {
		t.Fatalf("Runs = %d", got.Runs)
	}
	if got.Succeeded != 1 || got.Blocked != 1 || got.Failed != 1 {
		t.Fatalf("report = %+v", got)
	}
}

func TestWriteMarkdownCreatesDailyReport(t *testing.T) {
	path := t.TempDir() + "/daily/2026-07-21.md"
	report := Report{Runs: 2, Succeeded: 1, Blocked: 1}
	if err := WriteMarkdown(path, "tapstate", "2026-07-21", report); err != nil {
		t.Fatalf("WriteMarkdown error = %v", err)
	}
	data, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("ReadFile error = %v", err)
	}
	if !strings.Contains(string(data), "runs: 2") {
		t.Fatalf("content = %s", string(data))
	}
}
