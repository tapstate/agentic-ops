package feedback

import (
	"fmt"
	"os"
	"path/filepath"
)

type Report struct {
	Runs          int            `json:"runs"`
	Succeeded     int            `json:"succeeded"`
	Blocked       int            `json:"blocked"`
	Failed        int            `json:"failed"`
	MissingFields map[string]int `json:"missing_fields,omitempty"`
}

func Summarize(events []Event) Report {
	report := Report{}
	for _, event := range events {
		report.Runs++
		if event.OK {
			report.Succeeded++
			continue
		}
		if event.MissingField != "" {
			if report.MissingFields == nil {
				report.MissingFields = map[string]int{}
			}
			report.MissingFields[event.MissingField]++
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
	if len(report.MissingFields) > 0 {
		content += "\n## Missing fields\n\n"
		for field, count := range report.MissingFields {
			content += fmt.Sprintf("- %s: %d\n", field, count)
		}
	}
	return os.WriteFile(path, []byte(content), 0o644)
}
