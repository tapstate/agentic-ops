package clihandlers

import (
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/tapstate/agentic-ops/packages/agentic-cli/internal/jira"
)

func TestProjectCodeSuggestionsUsesJiraSummaryAsAnalysisHint(t *testing.T) {
	root := t.TempDir()
	sourcePath := filepath.Join(root, "manager", "tm", "src", "main", "java", "TaskAlarmScheduler.java")
	if err := os.MkdirAll(filepath.Dir(sourcePath), 0o755); err != nil {
		t.Fatalf("MkdirAll error = %v", err)
	}
	if err := os.WriteFile(sourcePath, []byte("class TaskAlarmScheduler { void scheduleAlarm() {} }\n"), 0o644); err != nil {
		t.Fatalf("WriteFile error = %v", err)
	}
	hiddenPath := filepath.Join(root, ".github", "workflows", "ci.yml")
	if err := os.MkdirAll(filepath.Dir(hiddenPath), 0o755); err != nil {
		t.Fatalf("MkdirAll hidden error = %v", err)
	}
	if err := os.WriteFile(hiddenPath, []byte("TaskAlarmScheduler\n"), 0o644); err != nil {
		t.Fatalf("WriteFile hidden error = %v", err)
	}
	readmePath := filepath.Join(root, "README.md")
	if err := os.WriteFile(readmePath, []byte("TaskAlarmScheduler\n"), 0o644); err != nil {
		t.Fatalf("WriteFile README error = %v", err)
	}

	suggestions := projectCodeSuggestions(root, jira.Issue{
		Summary: "TaskAlarmScheduler alarm repeats after startup",
	})

	joined := strings.Join(suggestions, "\n")
	if !strings.Contains(joined, "manager/tm/src/main/java/TaskAlarmScheduler.java") {
		t.Fatalf("suggestions = %#v", suggestions)
	}
	if strings.Contains(joined, ".github") {
		t.Fatalf("suggestions should skip hidden directories: %#v", suggestions)
	}
	if strings.Contains(joined, "README.md") {
		t.Fatalf("suggestions should prefer source files over docs: %#v", suggestions)
	}
	if !strings.Contains(joined, "初步分析线索") {
		t.Fatalf("suggestions missing human confirmation wording: %#v", suggestions)
	}
}
