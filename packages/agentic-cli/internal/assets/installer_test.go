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
	result, err := Install(source, installDir, "RES-v0.1.1-a68372d")
	if err != nil {
		t.Fatalf("Install error = %v", err)
	}

	if result.AssetVersion != "RES-v0.1.1-a68372d" {
		t.Fatalf("AssetVersion = %s", result.AssetVersion)
	}
	if _, err := os.Stat(filepath.Join(installDir, "assets", "RES-v0.1.1-a68372d", "handbooks", "ai-employee-handbook.md")); err != nil {
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
	if current.AssetVersion != "RES-v0.1.1-a68372d" {
		t.Fatalf("current AssetVersion = %s", current.AssetVersion)
	}
}

func TestInstallPreservesPreviousCurrentVersion(t *testing.T) {
	source := t.TempDir()
	writeTestFile(t, filepath.Join(source, "manifest.json"), "{}\n")

	installDir := t.TempDir()
	writeTestFile(t, filepath.Join(installDir, "current.json"), `{
  "agentic_cli_version": "RES-v0.1.1-a68372d",
  "asset_version": "RES-v0.1.1-a68372d"
}
`)

	if _, err := Install(source, installDir, "RES-v0.1.2-b794810"); err != nil {
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
	if current.AssetVersion != "RES-v0.1.2-b794810" {
		t.Fatalf("current AssetVersion = %s", current.AssetVersion)
	}
	if current.PreviousAssetVersion != "RES-v0.1.1-a68372d" {
		t.Fatalf("PreviousAssetVersion = %s", current.PreviousAssetVersion)
	}
	if current.PreviousAgentTaskOpsVersion != "RES-v0.1.1-a68372d" {
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
