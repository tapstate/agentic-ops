package assets

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
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

func TestInstallCompatibleValidatesExactPairManifest(t *testing.T) {
	source := t.TempDir()
	writeAssetTestManifest(t, source, "AST-v0.1.2-b794810", "RES-v0.1.2-b794810")
	writeTestFile(t, filepath.Join(source, "handbooks", "ai-employee-handbook.md"), "# handbook\n")

	result, err := InstallCompatible(source, t.TempDir(), "AST-v0.1.2-b794810", "RES-v0.1.2-b794810")
	if err != nil {
		t.Fatalf("InstallCompatible error = %v", err)
	}
	if result.CompatibilityPolicy != "exact_pair" {
		t.Fatalf("CompatibilityPolicy = %s", result.CompatibilityPolicy)
	}
}

func TestInstallCompatibleRejectsMissingManifest(t *testing.T) {
	_, err := InstallCompatible(t.TempDir(), t.TempDir(), "AST-v0.1.2-b794810", "RES-v0.1.2-b794810")
	if err == nil || !strings.Contains(err.Error(), "asset_manifest_missing") {
		t.Fatalf("err = %v", err)
	}
}

func TestInstallCompatibleRejectsAssetVersionMismatch(t *testing.T) {
	source := t.TempDir()
	writeAssetTestManifest(t, source, "AST-v0.1.2-b794810", "RES-v0.1.2-b794810")

	_, err := InstallCompatible(source, t.TempDir(), "AST-v0.1.3-deadbee", "RES-v0.1.2-b794810")
	if err == nil || !strings.Contains(err.Error(), "asset_version_mismatch") {
		t.Fatalf("err = %v", err)
	}
}

func TestInstallCompatibleRejectsCLIOutsideExactPair(t *testing.T) {
	source := t.TempDir()
	writeAssetTestManifest(t, source, "AST-v0.1.2-b794810", "RES-v0.1.2-b794810")

	_, err := InstallCompatible(source, t.TempDir(), "AST-v0.1.2-b794810", "RES-v0.1.1-a68372d")
	if err == nil || !strings.Contains(err.Error(), "incompatible_cli_version") {
		t.Fatalf("err = %v", err)
	}
}

func TestInstallCompatibleRejectsUnsafeAssetVersionBeforeWriting(t *testing.T) {
	source := t.TempDir()
	writeAssetTestManifest(t, source, "../escape", "RES-v0.1.2-b794810")
	installDir := t.TempDir()

	_, err := InstallCompatible(source, installDir, "../escape", "RES-v0.1.2-b794810")
	if err == nil || !strings.Contains(err.Error(), "unsafe asset version") {
		t.Fatalf("err = %v", err)
	}
	if _, statErr := os.Stat(filepath.Join(installDir, "current.json")); !os.IsNotExist(statErr) {
		t.Fatalf("current.json changed after validation failure: %v", statErr)
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

func writeAssetTestManifest(t *testing.T, source string, assetVersion string, cliVersion string) {
	t.Helper()
	writeTestFile(t, filepath.Join(source, "manifest.json"), `{
  "asset_version": "`+assetVersion+`",
  "min_cli_version": "`+cliVersion+`",
  "compatibility_policy": "exact_pair",
  "asset_source": {
    "kind": "local_directory",
    "path": "."
  }
}
`)
}
