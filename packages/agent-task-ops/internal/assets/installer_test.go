package assets

import (
	"encoding/json"
	"os"
	"path/filepath"
	"testing"
)

func TestInstallCopiesAssetsAndWritesCurrent(t *testing.T) {
	source := t.TempDir()
	writeTestFile(t, filepath.Join(source, "handbooks", "ai-employee-handbook.md"), "# handbook\n")
	writeTestFile(t, filepath.Join(source, "contracts", "operations", "takeover-task.yaml"), "operation: takeover_task\n")
	writeTestFile(t, filepath.Join(source, "policies", "default.yaml"), "gates: {}\n")

	installDir := t.TempDir()
	result, err := Install(source, installDir, "2026.07.22.1")
	if err != nil {
		t.Fatalf("Install error = %v", err)
	}

	if result.AssetVersion != "2026.07.22.1" {
		t.Fatalf("AssetVersion = %s", result.AssetVersion)
	}
	if _, err := os.Stat(filepath.Join(installDir, "assets", "2026.07.22.1", "handbooks", "ai-employee-handbook.md")); err != nil {
		t.Fatalf("installed handbook missing: %v", err)
	}

	currentBytes, err := os.ReadFile(filepath.Join(installDir, "current.json"))
	if err != nil {
		t.Fatalf("ReadFile current.json error = %v", err)
	}
	var current Current
	if err := json.Unmarshal(currentBytes, &current); err != nil {
		t.Fatalf("Unmarshal current.json error = %v", err)
	}
	if current.AssetVersion != "2026.07.22.1" {
		t.Fatalf("current AssetVersion = %s", current.AssetVersion)
	}
}

func TestInstallPreservesPreviousCurrentVersion(t *testing.T) {
	source := t.TempDir()
	writeTestFile(t, filepath.Join(source, "manifest.json"), "{}\n")

	installDir := t.TempDir()
	writeTestFile(t, filepath.Join(installDir, "current.json"), `{
  "agent_task_ops_version": "0.1.4",
  "asset_version": "2026.07.22.1"
}
`)

	if _, err := Install(source, installDir, "2026.07.22.2"); err != nil {
		t.Fatalf("Install error = %v", err)
	}

	currentBytes, err := os.ReadFile(filepath.Join(installDir, "current.json"))
	if err != nil {
		t.Fatalf("ReadFile current.json error = %v", err)
	}
	var current Current
	if err := json.Unmarshal(currentBytes, &current); err != nil {
		t.Fatalf("Unmarshal current.json error = %v", err)
	}
	if current.AssetVersion != "2026.07.22.2" {
		t.Fatalf("current AssetVersion = %s", current.AssetVersion)
	}
	if current.PreviousAssetVersion != "2026.07.22.1" {
		t.Fatalf("PreviousAssetVersion = %s", current.PreviousAssetVersion)
	}
	if current.PreviousAgentTaskOpsVersion != "0.1.4" {
		t.Fatalf("PreviousAgentTaskOpsVersion = %s", current.PreviousAgentTaskOpsVersion)
	}
}

func writeTestFile(t *testing.T, path string, content string) {
	t.Helper()
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		t.Fatalf("MkdirAll error = %v", err)
	}
	if err := os.WriteFile(path, []byte(content), 0o644); err != nil {
		t.Fatalf("WriteFile error = %v", err)
	}
}
