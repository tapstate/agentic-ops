package update

import (
	"archive/tar"
	"bytes"
	"compress/gzip"
	"crypto/sha256"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestCheckReportsRequiredUpdateWithBlockedOperations(t *testing.T) {
	manifestPath := filepath.Join(t.TempDir(), "manifest.json")
	writeUpdateTestFile(t, manifestPath, `{
  "version": "RES-v0.1.20-deadbee",
  "asset_version": "RES-v0.1.20-deadbee",
  "min_cli_version": "RES-v0.1.20-deadbee",
  "min_asset_version": "RES-v0.1.20-deadbee",
  "compatibility_policy": "exact_pair",
  "asset_source": {"kind": "local_directory", "path": "."},
  "severity": "required",
  "reason": "takeover_task 可能写入无效证据",
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
	if result.AgenticNextAction != "update_apply" {
		t.Fatalf("AgenticNextAction = %s", result.AgenticNextAction)
	}
}

func TestCheckReportsNoUpdateForSameVersion(t *testing.T) {
	manifestPath := filepath.Join(t.TempDir(), "manifest.json")
	writeUpdateTestFile(t, manifestPath, `{
  "version": "RES-v0.1.11-a68372d",
  "asset_version": "RES-v0.1.11-a68372d",
  "min_cli_version": "RES-v0.1.11-a68372d",
  "min_asset_version": "RES-v0.1.11-a68372d",
  "compatibility_policy": "exact_pair",
  "asset_source": {"kind": "local_directory", "path": "."},
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
	if result.AgenticNextAction != "continue" {
		t.Fatalf("AgenticNextAction = %s", result.AgenticNextAction)
	}
}

func TestCheckWithCurrentReportsCompatibleExactPair(t *testing.T) {
	manifestPath := filepath.Join(t.TempDir(), "manifest.json")
	writeUpdateTestFile(t, manifestPath, `{
  "version": "RES-v0.1.20-deadbee",
  "asset_version": "AST-v0.1.20-deadbee",
  "min_cli_version": "RES-v0.1.20-deadbee",
  "min_asset_version": "AST-v0.1.20-deadbee",
  "compatibility_policy": "exact_pair",
  "migration_required": false,
  "asset_source": {
    "kind": "github_release",
    "repository": "tapstate/agentic-ops",
    "ref": "v0.1.20",
    "path": "agentic-ops-assets_RES-v0.1.20-deadbee.tar.gz"
  }
}
`)

	result, err := CheckWithCurrent(manifestPath, "RES-v0.1.20-deadbee", "AST-v0.1.20-deadbee")
	if err != nil {
		t.Fatalf("CheckWithCurrent error = %v", err)
	}

	if result.CompatibilityState != "compatible" {
		t.Fatalf("CompatibilityState = %s", result.CompatibilityState)
	}
	if result.CompatibilityPolicy != "exact_pair" {
		t.Fatalf("CompatibilityPolicy = %s", result.CompatibilityPolicy)
	}
	if result.UpdateAvailable {
		t.Fatalf("UpdateAvailable = true, want false")
	}
}

func TestCheckWithCurrentReportsRequiredExactPairMigration(t *testing.T) {
	manifestPath := filepath.Join(t.TempDir(), "manifest.json")
	writeUpdateTestFile(t, manifestPath, `{
  "version": "RES-v0.1.20-deadbee",
  "asset_version": "AST-v0.1.20-deadbee",
  "min_cli_version": "RES-v0.1.20-deadbee",
  "min_asset_version": "AST-v0.1.20-deadbee",
  "compatibility_policy": "exact_pair",
  "migration_required": true,
  "severity": "required",
  "blocked_operations": ["takeover_task"],
  "asset_source": {"kind": "github_release", "repository": "tapstate/agentic-ops", "ref": "v0.1.20", "path": "assets.tar.gz"}
}
`)

	result, err := CheckWithCurrent(manifestPath, "RES-v0.1.11-a68372d", "AST-v0.1.11-a68372d")
	if err != nil {
		t.Fatalf("CheckWithCurrent error = %v", err)
	}

	if result.CompatibilityState != "update_required" {
		t.Fatalf("CompatibilityState = %s", result.CompatibilityState)
	}
	if !result.MigrationRequired {
		t.Fatalf("MigrationRequired = false, want true")
	}
	if result.AgenticNextAction != "update_apply" {
		t.Fatalf("AgenticNextAction = %s", result.AgenticNextAction)
	}
}

func TestCheckWithCurrentRejectsUnsupportedCompatibilityPolicy(t *testing.T) {
	manifestPath := filepath.Join(t.TempDir(), "manifest.json")
	writeUpdateTestFile(t, manifestPath, `{
  "version": "RES-v0.1.20-deadbee",
  "asset_version": "AST-v0.1.20-deadbee",
  "compatibility_policy": "rolling_window"
}
`)

	_, err := CheckWithCurrent(manifestPath, "RES-v0.1.20-deadbee", "AST-v0.1.20-deadbee")
	if err == nil || !strings.Contains(err.Error(), "unsupported compatibility_policy") {
		t.Fatalf("err = %v", err)
	}
}

func TestCheckWithCurrentRejectsIncompleteExactPairManifest(t *testing.T) {
	manifestPath := filepath.Join(t.TempDir(), "manifest.json")
	writeUpdateTestFile(t, manifestPath, `{
  "version": "RES-v0.1.20-deadbee",
  "asset_version": "AST-v0.1.20-deadbee",
  "compatibility_policy": "exact_pair"
}
`)

	_, err := CheckWithCurrent(manifestPath, "RES-v0.1.20-deadbee", "AST-v0.1.20-deadbee")
	if err == nil || !strings.Contains(err.Error(), "min_cli_version") {
		t.Fatalf("err = %v", err)
	}
}

func TestCheckWithCurrentRejectsManifestWithoutCompatibilityPolicy(t *testing.T) {
	manifestPath := filepath.Join(t.TempDir(), "manifest.json")
	writeUpdateTestFile(t, manifestPath, `{
  "version": "RES-v0.1.20-deadbee",
  "asset_version": "AST-v0.1.20-deadbee"
}
`)

	_, err := CheckWithCurrent(manifestPath, "RES-v0.1.20-deadbee", "AST-v0.1.20-deadbee")
	if err == nil || !strings.Contains(err.Error(), "compatibility_policy") {
		t.Fatalf("err = %v", err)
	}
}

func TestCheckWithCurrentRejectsUnsafeVersionPath(t *testing.T) {
	manifestPath := filepath.Join(t.TempDir(), "manifest.json")
	writeUpdateTestFile(t, manifestPath, `{
  "version": "../escape",
  "asset_version": "AST-v0.1.20-deadbee",
  "min_cli_version": "../escape",
  "min_asset_version": "AST-v0.1.20-deadbee",
  "compatibility_policy": "exact_pair",
  "asset_source": {"kind": "local_directory", "path": "."}
}
`)

	_, err := CheckWithCurrent(manifestPath, "SRC-source", "AST-v0.1.20-deadbee")
	if err == nil || !strings.Contains(err.Error(), "unsafe version") {
		t.Fatalf("err = %v", err)
	}
}

func TestApplyWritesCurrentAndPreservesPreviousVersions(t *testing.T) {
	dir := t.TempDir()
	manifestPath := filepath.Join(dir, "source", "manifest.json")
	installDir := filepath.Join(dir, "install")
	writeUpdateTestFile(t, manifestPath, `{
  "version": "RES-v0.1.20-deadbee",
  "asset_version": "RES-v0.1.20-deadbee",
  "min_cli_version": "RES-v0.1.20-deadbee",
  "min_asset_version": "RES-v0.1.20-deadbee",
  "compatibility_policy": "exact_pair",
  "asset_source": {"kind": "local_directory", "path": "."},
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

func TestApplyRejectsInstallDirectoryInsideAssetSource(t *testing.T) {
	source := t.TempDir()
	manifestPath := filepath.Join(source, "manifest.json")
	writeUpdateTestFile(t, manifestPath, `{
  "version": "SRC-source",
  "asset_version": "AST-v0.1.20-deadbee",
  "min_cli_version": "SRC-source",
  "min_asset_version": "AST-v0.1.20-deadbee",
  "compatibility_policy": "exact_pair",
  "asset_source": {"kind": "local_directory", "path": "."}
}
`)

	_, err := Apply(manifestPath, filepath.Join(source, "install"))
	if err == nil || !strings.Contains(err.Error(), "asset source contains install target") {
		t.Fatalf("err = %v", err)
	}
}

func TestApplyLocalRejectsReleaseForDifferentCLI(t *testing.T) {
	dir := t.TempDir()
	manifestPath := filepath.Join(dir, "source", "manifest.json")
	writeUpdateTestFile(t, manifestPath, `{
  "version": "RES-v0.1.20-deadbee",
  "asset_version": "AST-v0.1.20-deadbee",
  "min_cli_version": "RES-v0.1.20-deadbee",
  "min_asset_version": "AST-v0.1.20-deadbee",
  "compatibility_policy": "exact_pair",
  "asset_source": {"kind": "local_directory", "path": "."}
}
`)

	_, err := ApplyLocal(manifestPath, filepath.Join(dir, "install"), "SRC-source")
	if err == nil || !strings.Contains(err.Error(), "local update cannot replace running CLI") {
		t.Fatalf("err = %v", err)
	}
}

func TestApplyManifestRemovesNewBinaryWhenMetadataSwitchFailsOnFirstInstall(t *testing.T) {
	installDir := t.TempDir()
	stagedBinary := filepath.Join(t.TempDir(), "agentic-cli")
	writeUpdateTestFile(t, stagedBinary, "new binary")
	stagedAssets := filepath.Join(installDir, "versions", "AST-v0.1.20-deadbee", "assets")
	writeUpdateTestFile(t, filepath.Join(stagedAssets, "manifest.json"), "{}\n")
	manifest := Manifest{
		Version: "RES-v0.1.20-deadbee", AssetVersion: "AST-v0.1.20-deadbee",
		MinCLIVersion: "RES-v0.1.20-deadbee", MinAssetVersion: "AST-v0.1.20-deadbee",
		CompatibilityPolicy: "exact_pair", AssetSource: AssetSource{Kind: "github_release", Repository: "tapstate/agentic-ops", Ref: "v0.1.20", Path: "assets.tar.gz"},
	}
	original := persistCurrent
	persistCurrent = func(string, currentState) error { return fmt.Errorf("forced metadata failure") }
	defer func() { persistCurrent = original }()

	_, err := applyManifest(manifest, installDir, nil, stagedBinary, stagedAssets)
	if err == nil || !strings.Contains(err.Error(), "forced metadata failure") {
		t.Fatalf("err = %v", err)
	}
	if _, statErr := os.Stat(filepath.Join(installDir, "bin", "agentic-cli")); !os.IsNotExist(statErr) {
		t.Fatalf("active binary changed after failed metadata switch: %v", statErr)
	}
}

func TestCheckRemoteDownloadsManifestURL(t *testing.T) {
	restore := SetHTTPClientForTest(&http.Client{Transport: updateRoundTripFunc(func(r *http.Request) *http.Response {
		if r.URL.String() != "https://updates.example.test/manifest.json" {
			t.Fatalf("url = %s", r.URL.String())
		}
		return updateHTTPResponse(http.StatusOK, `{
  "version": "RES-v0.1.20-deadbee",
  "asset_version": "RES-v0.1.20-deadbee",
  "min_cli_version": "RES-v0.1.20-deadbee",
  "min_asset_version": "RES-v0.1.20-deadbee",
  "compatibility_policy": "exact_pair",
  "asset_source": {"kind": "github_release", "repository": "tapstate/agentic-ops", "ref": "v0.1.20", "path": "agentic-ops-assets_RES-v0.1.20-deadbee.tar.gz"},
  "severity": "required",
  "reason": "remote required update"
}
`)
	})})
	defer restore()

	result, err := CheckRemote("https://updates.example.test/manifest.json", "RES-v0.1.11-a68372d")
	if err != nil {
		t.Fatalf("CheckRemote error = %v", err)
	}
	if !result.UpdateAvailable || result.LatestVersion != "RES-v0.1.20-deadbee" {
		t.Fatalf("result = %#v", result)
	}
	if result.AgenticNextAction != "update_apply" {
		t.Fatalf("AgenticNextAction = %s", result.AgenticNextAction)
	}
}

func TestApplyRemoteDownloadsArtifactsAndVerifiesChecksums(t *testing.T) {
	installDir := t.TempDir()
	version := "RES-v0.1.20-deadbee"
	writeUpdateTestFile(t, filepath.Join(installDir, "current.json"), `{
  "agentic_cli_version": "RES-v0.1.11-a68372d",
  "asset_version": "AST-v0.1.11-a68372d",
  "active_binary_path": "bin/agentic-cli",
  "active_asset_path": "assets/AST-v0.1.11-a68372d"
}
`)
	writeUpdateTestFile(t, filepath.Join(installDir, "bin", "agentic-cli"), "old binary")
	if err := os.MkdirAll(filepath.Join(installDir, "assets", "AST-v0.1.11-a68372d"), 0o755); err != nil {
		t.Fatalf("MkdirAll previous assets error = %v", err)
	}
	writeUpdateTestFile(t, filepath.Join(installDir, "assets", "AST-v0.1.11-a68372d", "manifest.json"), `{"asset_version":"AST-v0.1.11-a68372d"}`)
	binaryName := "agentic-cli_darwin-arm64.tar.gz"
	assetsName := "agentic-ops-assets_" + version + ".tar.gz"
	binaryContent := agenticCLITarGZ(t, "new binary")
	assetsContent := agenticAssetsTarGZ(t, version)
	checksums := fmt.Sprintf("%x  %s\n%x  %s\n", sha256.Sum256(binaryContent), binaryName, sha256.Sum256(assetsContent), assetsName)
	restore := SetHTTPClientForTest(&http.Client{Transport: updateRoundTripFunc(func(r *http.Request) *http.Response {
		switch r.URL.String() {
		case "https://updates.example.test/manifest.json":
			return updateHTTPResponse(http.StatusOK, `{
  "version": "RES-v0.1.20-deadbee",
  "asset_version": "RES-v0.1.20-deadbee",
  "min_cli_version": "RES-v0.1.20-deadbee",
  "min_asset_version": "RES-v0.1.20-deadbee",
  "compatibility_policy": "exact_pair",
  "asset_source": {"kind": "github_release", "repository": "tapstate/agentic-ops", "ref": "v0.1.20", "path": "agentic-ops-assets_RES-v0.1.20-deadbee.tar.gz"},
  "artifacts": [
    {"name": "agentic-cli_darwin-arm64.tar.gz", "target": "darwin-arm64", "type": "binary"},
    {"name": "agentic-ops-assets_RES-v0.1.20-deadbee.tar.gz", "target": "all", "type": "assets"}
  ]
}
`)
		case "https://updates.example.test/checksums.txt":
			return updateHTTPResponse(http.StatusOK, checksums)
		case "https://updates.example.test/" + binaryName:
			return updateHTTPBytes(http.StatusOK, binaryContent)
		case "https://updates.example.test/" + assetsName:
			return updateHTTPBytes(http.StatusOK, assetsContent)
		default:
			t.Fatalf("unexpected url = %s", r.URL.String())
			return updateHTTPResponse(http.StatusNotFound, "")
		}
	})})
	defer restore()

	result, err := ApplyRemote("https://updates.example.test/manifest.json", installDir, "darwin-arm64")
	if err != nil {
		t.Fatalf("ApplyRemote error = %v", err)
	}
	if result.AgenticCLIVersion != version || result.AssetVersion != version {
		t.Fatalf("result = %#v", result)
	}
	if _, err := os.Stat(filepath.Join(installDir, "downloads", version, binaryName)); err != nil {
		t.Fatalf("binary artifact missing: %v", err)
	}
	if _, err := os.Stat(filepath.Join(installDir, "downloads", version, assetsName)); err != nil {
		t.Fatalf("assets artifact missing: %v", err)
	}
	activated, err := os.ReadFile(filepath.Join(installDir, "bin", "agentic-cli"))
	if err != nil {
		t.Fatalf("activated binary missing: %v", err)
	}
	if string(activated) != "new binary" {
		t.Fatalf("activated binary = %q", string(activated))
	}
	current := readCurrent(filepath.Join(installDir, "current.json"))
	if current.ActiveAssetPath == "" {
		t.Fatalf("ActiveAssetPath is empty")
	}
	if _, err := os.Stat(filepath.Join(installDir, current.ActiveAssetPath, "manifest.json")); err != nil {
		t.Fatalf("activated asset manifest missing: %v", err)
	}
	if current.PreviousBinaryPath == "" {
		t.Fatalf("PreviousBinaryPath is empty")
	}
	if current.PreviousBinarySHA256 == "" {
		t.Fatalf("PreviousBinarySHA256 is empty")
	}
	previousBinary, err := os.ReadFile(filepath.Join(installDir, current.PreviousBinaryPath))
	if err != nil {
		t.Fatalf("previous binary missing: %v", err)
	}
	if string(previousBinary) != "old binary" {
		t.Fatalf("previous binary = %q", string(previousBinary))
	}
}

func TestRollbackRestoresPreviousBinaryAssetsAndMetadata(t *testing.T) {
	installDir := t.TempDir()
	writeUpdateTestFile(t, filepath.Join(installDir, "bin", "agentic-cli"), "new binary")
	writeUpdateTestFile(t, filepath.Join(installDir, "rollback", "RES-v0.1.11-a68372d", "agentic-cli"), "old binary")
	if err := os.MkdirAll(filepath.Join(installDir, "assets", "AST-v0.1.11-a68372d"), 0o755); err != nil {
		t.Fatalf("MkdirAll previous assets error = %v", err)
	}
	writeUpdateTestFile(t, filepath.Join(installDir, "assets", "AST-v0.1.11-a68372d", "manifest.json"), `{"asset_version":"AST-v0.1.11-a68372d"}`)
	oldBinaryChecksum := fmt.Sprintf("%x", sha256.Sum256([]byte("old binary")))
	writeUpdateTestFile(t, filepath.Join(installDir, "current.json"), fmt.Sprintf(`{
  "agentic_cli_version": "RES-v0.1.20-deadbee",
  "asset_version": "AST-v0.1.20-deadbee",
  "previous_agentic_cli_version": "RES-v0.1.11-a68372d",
  "previous_asset_version": "AST-v0.1.11-a68372d",
  "active_binary_path": "bin/agentic-cli",
  "active_asset_path": "assets/AST-v0.1.20-deadbee",
  "previous_binary_path": "rollback/RES-v0.1.11-a68372d/agentic-cli",
  "previous_binary_sha256": "%s",
  "previous_asset_path": "assets/AST-v0.1.11-a68372d"
}
`, oldBinaryChecksum))

	result, err := Rollback(installDir)
	if err != nil {
		t.Fatalf("Rollback error = %v", err)
	}
	if result.AgenticCLIVersion != "RES-v0.1.11-a68372d" || result.AssetVersion != "AST-v0.1.11-a68372d" {
		t.Fatalf("result = %#v", result)
	}
	activeBinary, err := os.ReadFile(filepath.Join(installDir, "bin", "agentic-cli"))
	if err != nil {
		t.Fatalf("active binary missing: %v", err)
	}
	if string(activeBinary) != "old binary" {
		t.Fatalf("active binary = %q", string(activeBinary))
	}
	current := readCurrent(filepath.Join(installDir, "current.json"))
	if current.AgenticCLIVersion != "RES-v0.1.11-a68372d" || current.AssetVersion != "AST-v0.1.11-a68372d" {
		t.Fatalf("current = %#v", current)
	}
}

func TestRollbackRejectsTamperedPreviousBinary(t *testing.T) {
	installDir := t.TempDir()
	writeUpdateTestFile(t, filepath.Join(installDir, "bin", "agentic-cli"), "new binary")
	writeUpdateTestFile(t, filepath.Join(installDir, "rollback", "RES-v0.1.11-a68372d", "agentic-cli"), "tampered binary")
	writeUpdateTestFile(t, filepath.Join(installDir, "current.json"), `{
  "agentic_cli_version": "RES-v0.1.20-deadbee",
  "asset_version": "AST-v0.1.20-deadbee",
  "previous_agentic_cli_version": "RES-v0.1.11-a68372d",
  "previous_asset_version": "AST-v0.1.11-a68372d",
  "active_binary_path": "bin/agentic-cli",
  "previous_binary_path": "rollback/RES-v0.1.11-a68372d/agentic-cli",
  "previous_binary_sha256": "0000000000000000000000000000000000000000000000000000000000000000"
}
`)

	_, err := Rollback(installDir)
	if err == nil || !strings.Contains(err.Error(), "rollback_target_invalid") {
		t.Fatalf("err = %v", err)
	}
}

func TestRollbackRejectsMissingActiveBinary(t *testing.T) {
	installDir := t.TempDir()
	writeUpdateTestFile(t, filepath.Join(installDir, "rollback", "RES-v0.1.11-a68372d", "agentic-cli"), "old binary")
	oldChecksum := fmt.Sprintf("%x", sha256.Sum256([]byte("old binary")))
	writeUpdateTestFile(t, filepath.Join(installDir, "current.json"), fmt.Sprintf(`{
  "agentic_cli_version": "RES-v0.1.20-deadbee",
  "asset_version": "AST-v0.1.20-deadbee",
  "previous_agentic_cli_version": "RES-v0.1.11-a68372d",
  "previous_asset_version": "AST-v0.1.11-a68372d",
  "active_binary_path": "bin/agentic-cli",
  "previous_binary_path": "rollback/RES-v0.1.11-a68372d/agentic-cli",
  "previous_binary_sha256": "%s"
}
`, oldChecksum))

	_, err := Rollback(installDir)
	if err == nil || !strings.Contains(err.Error(), "rollback_target_invalid") {
		t.Fatalf("err = %v", err)
	}
}

func TestRollbackRejectsMismatchedPreviousAssetManifest(t *testing.T) {
	installDir := t.TempDir()
	writeUpdateTestFile(t, filepath.Join(installDir, "bin", "agentic-cli"), "new binary")
	writeUpdateTestFile(t, filepath.Join(installDir, "rollback", "RES-v0.1.11-a68372d", "agentic-cli"), "old binary")
	writeUpdateTestFile(t, filepath.Join(installDir, "assets", "AST-v0.1.11-a68372d", "manifest.json"), `{"asset_version":"AST-other"}`)
	oldChecksum := fmt.Sprintf("%x", sha256.Sum256([]byte("old binary")))
	writeUpdateTestFile(t, filepath.Join(installDir, "current.json"), fmt.Sprintf(`{
  "agentic_cli_version": "RES-v0.1.20-deadbee",
  "asset_version": "AST-v0.1.20-deadbee",
  "previous_agentic_cli_version": "RES-v0.1.11-a68372d",
  "previous_asset_version": "AST-v0.1.11-a68372d",
  "active_binary_path": "bin/agentic-cli",
  "previous_binary_path": "rollback/RES-v0.1.11-a68372d/agentic-cli",
  "previous_binary_sha256": "%s",
  "previous_asset_path": "assets/AST-v0.1.11-a68372d"
}
`, oldChecksum))

	_, err := Rollback(installDir)
	if err == nil || !strings.Contains(err.Error(), "rollback_target_invalid") {
		t.Fatalf("err = %v", err)
	}
}

func TestRollbackRejectsMissingState(t *testing.T) {
	_, err := Rollback(t.TempDir())
	if err == nil || !strings.Contains(err.Error(), "rollback_state_missing") {
		t.Fatalf("err = %v", err)
	}
}

func TestApplyRemoteRejectsChecksumMismatch(t *testing.T) {
	installDir := t.TempDir()
	restore := SetHTTPClientForTest(&http.Client{Transport: updateRoundTripFunc(func(r *http.Request) *http.Response {
		switch r.URL.String() {
		case "https://updates.example.test/manifest.json":
			return updateHTTPResponse(http.StatusOK, `{
  "version": "RES-v0.1.20-deadbee",
  "asset_version": "RES-v0.1.20-deadbee",
  "min_cli_version": "RES-v0.1.20-deadbee",
  "min_asset_version": "RES-v0.1.20-deadbee",
  "compatibility_policy": "exact_pair",
  "asset_source": {"kind": "github_release", "repository": "tapstate/agentic-ops", "ref": "v0.1.20", "path": "agentic-ops-assets_RES-v0.1.20-deadbee.tar.gz"},
  "artifacts": [
    {"name": "agentic-cli_darwin-arm64.tar.gz", "target": "darwin-arm64", "type": "binary"}
  ]
}
`)
		case "https://updates.example.test/checksums.txt":
			return updateHTTPResponse(http.StatusOK, "0000000000000000000000000000000000000000000000000000000000000000  agentic-cli_darwin-arm64.tar.gz\n")
		case "https://updates.example.test/agentic-cli_darwin-arm64.tar.gz":
			return updateHTTPResponse(http.StatusOK, "binary artifact")
		default:
			t.Fatalf("unexpected url = %s", r.URL.String())
			return updateHTTPResponse(http.StatusNotFound, "")
		}
	})})
	defer restore()

	_, err := ApplyRemote("https://updates.example.test/manifest.json", installDir, "darwin-arm64")
	if err == nil || !strings.Contains(err.Error(), "checksum mismatch") {
		t.Fatalf("err = %v", err)
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

type updateRoundTripFunc func(*http.Request) *http.Response

func (fn updateRoundTripFunc) RoundTrip(request *http.Request) (*http.Response, error) {
	return fn(request), nil
}

func updateHTTPResponse(statusCode int, body string) *http.Response {
	return updateHTTPBytes(statusCode, []byte(body))
}

func updateHTTPBytes(statusCode int, body []byte) *http.Response {
	return &http.Response{
		StatusCode: statusCode,
		Header:     make(http.Header),
		Body:       io.NopCloser(strings.NewReader(string(body))),
	}
}

func agenticCLITarGZ(t *testing.T, content string) []byte {
	t.Helper()
	var buffer bytes.Buffer
	gzipWriter := gzip.NewWriter(&buffer)
	tarWriter := tar.NewWriter(gzipWriter)
	data := []byte(content)
	if err := tarWriter.WriteHeader(&tar.Header{
		Name: "agentic-cli",
		Mode: 0o755,
		Size: int64(len(data)),
	}); err != nil {
		t.Fatalf("WriteHeader error = %v", err)
	}
	if _, err := tarWriter.Write(data); err != nil {
		t.Fatalf("tar Write error = %v", err)
	}
	if err := tarWriter.Close(); err != nil {
		t.Fatalf("tar Close error = %v", err)
	}
	if err := gzipWriter.Close(); err != nil {
		t.Fatalf("gzip Close error = %v", err)
	}
	return buffer.Bytes()
}

func agenticAssetsTarGZ(t *testing.T, version string) []byte {
	t.Helper()
	manifest := `{"asset_version":"` + version + `","min_cli_version":"` + version + `","compatibility_policy":"exact_pair","asset_source":{"kind":"github_release","path":"assets.tar.gz"}}` + "\n"
	files := map[string]string{
		"manifest.json":                     manifest,
		"handbooks/ai-employee-handbook.md": "# handbook\n",
	}
	var buffer bytes.Buffer
	gzipWriter := gzip.NewWriter(&buffer)
	tarWriter := tar.NewWriter(gzipWriter)
	for name, content := range files {
		data := []byte(content)
		if err := tarWriter.WriteHeader(&tar.Header{Name: name, Mode: 0o644, Size: int64(len(data))}); err != nil {
			t.Fatalf("WriteHeader error = %v", err)
		}
		if _, err := tarWriter.Write(data); err != nil {
			t.Fatalf("tar Write error = %v", err)
		}
	}
	if err := tarWriter.Close(); err != nil {
		t.Fatalf("tar Close error = %v", err)
	}
	if err := gzipWriter.Close(); err != nil {
		t.Fatalf("gzip Close error = %v", err)
	}
	return buffer.Bytes()
}
