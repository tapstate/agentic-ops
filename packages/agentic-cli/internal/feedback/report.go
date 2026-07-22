package feedback

import (
	"fmt"
	"os"
	"path/filepath"
)

type Report struct {
	Runs      int `json:"runs"`
	Succeeded int `json:"succeeded"`
	Blocked   int `json:"blocked"`
	Failed    int `json:"failed"`
}

func Summarize(events []Event) Report {
	report := Report{}
	for _, event := range events {
		report.Runs++
		if event.OK {
			report.Succeeded++
			continue
		}
		if event.RequiresHumanAction || event.NextAction == "ask_owner" {
			report.Blocked++
			continue
		}
		report.Failed++
	}
	return report
}

func WriteMarkdown(path string, workspace string, date string, report Report) error {
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		return err
	}
	content := fmt.Sprintf(`# AgenticOps Daily Feedback

- workspace: %s
- date: %s
- runs: %d
- succeeded: %d
- blocked: %d
- failed: %d
`, workspace, date, report.Runs, report.Succeeded, report.Blocked, report.Failed)
	return os.WriteFile(path, []byte(content), 0o644)
}
