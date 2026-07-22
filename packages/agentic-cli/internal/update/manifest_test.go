package update

import (
	"encoding/json"
	"os"
	"path/filepath"
	"testing"
)

func TestCheckReportsRequiredUpdateWithBlockedOperations(t *testing.T) {
	manifestPath := filepath.Join(t.TempDir(), "manifest.json")
	writeUpdateTestFile(t, manifestPath, `{
  "version": "RES-v0.1.20-deadbee",
  "asset_version": "RES-v0.1.20-deadbee",
  "severity": "required",
  "reason": "takeover_task may write invalid evidence",
  "blocked_operations": ["takeover_task", "write_evidence"]
}
`)

	result, err := Check(manifestPath, "RES-v0.1.11-a68372d")
	if err != nil {
		t.Fatalf("Check error = %v", err)
	}

	if !result.UpdateAvailable {
		t.Fatalf("UpdateAvailable = false, want true")
	}
	if result.Severity != "required" {
		t.Fatalf("Severity = %s", result.Severity)
	}
	if len(result.BlockedOperations) != 2 || result.BlockedOperations[0] != "takeover_task" {
		t.Fatalf("BlockedOperations = %#v", result.BlockedOperations)
	}
	if result.NextAction != "update_apply" {
		t.Fatalf("NextAction = %s", result.NextAction)
	}
}

func TestCheckReportsNoUpdateForSameVersion(t *testing.T) {
	manifestPath := filepath.Join(t.TempDir(), "manifest.json")
	writeUpdateTestFile(t, manifestPath, `{
  "version": "RES-v0.1.11-a68372d",
  "asset_version": "RES-v0.1.11-a68372d",
  "severity": "recommended"
}
`)

	result, err := Check(manifestPath, "RES-v0.1.11-a68372d")
	if err != nil {
		t.Fatalf("Check error = %v", err)
	}

	if result.UpdateAvailable {
		t.Fatalf("UpdateAvailable = true, want false")
	}
	if result.NextAction != "continue" {
		t.Fatalf("NextAction = %s", result.NextAction)
	}
}

func TestApplyWritesCurrentAndPreservesPreviousVersions(t *testing.T) {
	dir := t.TempDir()
	manifestPath := filepath.Join(dir, "manifest.json")
	installDir := filepath.Join(dir, "install")
	writeUpdateTestFile(t, manifestPath, `{
  "version": "RES-v0.1.20-deadbee",
  "asset_version": "RES-v0.1.20-deadbee",
  "severity": "recommended"
}
`)
	writeUpdateTestFile(t, filepath.Join(installDir, "current.json"), `{
  "agentic_cli_version": "RES-v0.1.11-a68372d",
  "asset_version": "RES-v0.1.11-a68372d"
}
`)

	result, err := Apply(manifestPath, installDir)
	if err != nil {
		t.Fatalf("Apply error = %v", err)
	}

	if result.AgenticCLIVersion != "RES-v0.1.20-deadbee" {
		t.Fatalf("AgenticCLIVersion = %s", result.AgenticCLIVersion)
	}
	data, err := os.ReadFile(filepath.Join(installDir, "current.json"))
	if err != nil {
		t.Fatalf("ReadFile current.json error = %v", err)
	}
	var got map[string]string
	if err := json.Unmarshal(data, &got); err != nil {
		t.Fatalf("Unmarshal current error = %v", err)
	}
	if got["previous_agentic_cli_version"] != "RES-v0.1.11-a68372d" {
		t.Fatalf("previous_agentic_cli_version = %s", got["previous_agentic_cli_version"])
	}
	if got["previous_asset_version"] != "RES-v0.1.11-a68372d" {
		t.Fatalf("previous_asset_version = %s", got["previous_asset_version"])
	}
}

func writeUpdateTestFile(t *testing.T, path string, content string) {
	t.Helper()
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		t.Fatalf("MkdirAll error = %v", err)
	}
	if err := os.WriteFile(path, []byte(content), 0o644); err != nil {
		t.Fatalf("WriteFile error = %v", err)
	}
}
