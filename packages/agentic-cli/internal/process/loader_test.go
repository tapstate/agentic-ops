package process

import (
	"path/filepath"
	"testing"
)

func TestLoadRegistryReadsProcessesByID(t *testing.T) {
	registry, err := LoadRegistry(filepath.Join("..", "..", "..", "..", "contracts", "processes"))
	if err != nil {
		t.Fatalf("LoadRegistry error = %v", err)
	}

	got, ok := registry["development_change_v1"]
	if !ok {
		t.Fatalf("development_change_v1 missing from registry: %#v", registry)
	}
	if got.EntryStage != "waiting_takeover" {
		t.Fatalf("EntryStage = %s", got.EntryStage)
	}
	if !got.HasStage("waiting_takeover") {
		t.Fatalf("waiting_takeover stage missing: %+v", got)
	}
}

func TestValidateReportsMissingEntryStage(t *testing.T) {
	issues := Validate(Process{ProcessID: "broken", EntryStage: "waiting_takeover"})
	if !hasProcessIssue(issues, "missing_entry_stage") {
		t.Fatalf("issues missing entry stage: %#v", issues)
	}
}

func hasProcessIssue(issues []ValidationIssue, code string) bool {
	for _, issue := range issues {
		if issue.Code == code {
			return true
		}
	}
	return false
}
