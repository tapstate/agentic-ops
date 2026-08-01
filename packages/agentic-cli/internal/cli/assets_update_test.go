package cli

import (
	"bytes"
	"crypto/sha256"
	"fmt"
	"github.com/tapstate/agentic-ops/packages/agentic-cli/internal/update"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestAssetsInstallCopiesAssetsToInstallDir(t *testing.T) {
	source := t.TempDir()
	writeCLITestFile(t, filepath.Join(source, "manifest.json"), `{
  "asset_version": "RES-v0.1.1-a68372d",
  "min_cli_version": "SRC-source",
  "compatibility_policy": "exact_pair",
  "asset_source": {"kind": "local_directory", "path": "."}
}
`)
	writeCLITestFile(t, filepath.Join(source, "handbooks", "ai-employee-handbook.md"), "# handbook\n")
	installDir := t.TempDir()

	var stdout bytes.Buffer
	var stderr bytes.Buffer
	code := Run([]string{"assets", "install", "--source", source, "--install-dir", installDir, "--version", "RES-v0.1.1-a68372d"}, &stdout, &stderr)
	if code != 0 {
		t.Fatalf("code = %d stdout = %s stderr = %s", code, stdout.String(), stderr.String())
	}

	assertJSONField(t, stdout.String(), "operation", "assets_install")
	assertJSONField(t, stdout.String(), "asset_version", "RES-v0.1.1-a68372d")
	assertJSONField(t, stdout.String(), "compatibility_policy", "exact_pair")
	if _, err := os.Stat(filepath.Join(installDir, "assets", "RES-v0.1.1-a68372d", "handbooks", "ai-employee-handbook.md")); err != nil {
		t.Fatalf("installed asset missing: %v", err)
	}
}

func TestUpdateCheckAndApplyUseLocalManifest(t *testing.T) {
	dir := t.TempDir()
	manifestPath := filepath.Join(dir, "source", "manifest.json")
	installDir := filepath.Join(dir, "install")
	writeCLITestFile(t, manifestPath, `{
  "version": "SRC-source",
  "asset_version": "RES-v0.1.20-deadbee",
  "min_cli_version": "SRC-source",
  "min_asset_version": "RES-v0.1.20-deadbee",
  "compatibility_policy": "exact_pair",
  "asset_source": {"kind": "local_directory", "path": "."},
  "severity": "required",
  "reason": "takeover_task 可能写入无效证据",
  "blocked_operations": ["takeover_task"]
}
`)

	var checkStdout bytes.Buffer
	var checkStderr bytes.Buffer
	checkCode := Run([]string{"update", "check", "--manifest", manifestPath, "--install-dir", installDir}, &checkStdout, &checkStderr)
	if checkCode != 0 {
		t.Fatalf("checkCode = %d stdout = %s stderr = %s", checkCode, checkStdout.String(), checkStderr.String())
	}
	assertJSONField(t, checkStdout.String(), "operation", "update_check")
	assertJSONField(t, checkStdout.String(), "update_available", true)
	assertJSONField(t, checkStdout.String(), "severity", "required")
	assertJSONField(t, checkStdout.String(), "compatibility_policy", "exact_pair")
	assertJSONField(t, checkStdout.String(), "compatibility_state", "update_required")
	assertJSONField(t, checkStdout.String(), "agentic_next_action", "update_apply")
	if !strings.Contains(checkStdout.String(), `"takeover_task"`) {
		t.Fatalf("check stdout missing blocked operation: %s", checkStdout.String())
	}

	var applyStdout bytes.Buffer
	var applyStderr bytes.Buffer
	applyCode := Run([]string{"update", "apply", "--manifest", manifestPath, "--install-dir", installDir}, &applyStdout, &applyStderr)
	if applyCode != 0 {
		t.Fatalf("applyCode = %d stdout = %s stderr = %s", applyCode, applyStdout.String(), applyStderr.String())
	}
	assertJSONField(t, applyStdout.String(), "operation", "update_apply")
	assertJSONField(t, applyStdout.String(), "version", "SRC-source")
	assertJSONField(t, applyStdout.String(), "asset_version", "RES-v0.1.20-deadbee")
	assertJSONField(t, applyStdout.String(), "agentic_next_action", "doctor")
	if _, err := os.Stat(filepath.Join(installDir, "current.json")); err != nil {
		t.Fatalf("current.json missing: %v", err)
	}
}

func TestUpdateRollbackRestoresPreviousLocalState(t *testing.T) {
	installDir := t.TempDir()
	writeCLITestFile(t, filepath.Join(installDir, "bin", "agentic-cli"), "new binary")
	writeCLITestFile(t, filepath.Join(installDir, "rollback", "RES-v0.1.11-a68372d", "agentic-cli"), "old binary")
	oldBinaryChecksum := fmt.Sprintf("%x", sha256.Sum256([]byte("old binary")))
	if err := os.MkdirAll(filepath.Join(installDir, "assets", "AST-v0.1.11-a68372d"), 0o755); err != nil {
		t.Fatalf("MkdirAll previous assets error = %v", err)
	}
	writeCLITestFile(t, filepath.Join(installDir, "assets", "AST-v0.1.11-a68372d", "manifest.json"), `{"asset_version":"AST-v0.1.11-a68372d"}`)
	writeCLITestFile(t, filepath.Join(installDir, "current.json"), fmt.Sprintf(`{
  "agentic_cli_version": "RES-v0.1.20-deadbee",
  "asset_version": "AST-v0.1.20-deadbee",
  "previous_agentic_cli_version": "RES-v0.1.11-a68372d",
  "previous_asset_version": "AST-v0.1.11-a68372d",
  "active_binary_path": "bin/agentic-cli",
  "active_asset_path": "assets/AST-v0.1.20-deadbee",
  "previous_binary_path": "rollback/RES-v0.1.11-a68372d/agentic-cli",
  "previous_binary_sha256": "%s",
  "previous_asset_path": "assets/AST-v0.1.11-a68372d",
  "compatibility_policy": "exact_pair"
}
`, oldBinaryChecksum))

	var stdout bytes.Buffer
	var stderr bytes.Buffer
	code := Run([]string{"update", "rollback", "--install-dir", installDir}, &stdout, &stderr)
	if code != 0 {
		t.Fatalf("code = %d stdout = %s stderr = %s", code, stdout.String(), stderr.String())
	}
	assertJSONField(t, stdout.String(), "operation", "update_rollback")
	assertJSONField(t, stdout.String(), "version", "RES-v0.1.11-a68372d")
	assertJSONField(t, stdout.String(), "asset_version", "AST-v0.1.11-a68372d")
	assertJSONField(t, stdout.String(), "agentic_next_action", "doctor")
}

func TestRequiredUpdateGuardBlocksConfiguredOperation(t *testing.T) {
	installDir := t.TempDir()
	t.Setenv("AGENTIC_OPS_HOME", installDir)
	writeCLITestFile(t, filepath.Join(installDir, ".local", "update-state.json"), `{
  "compatibility_state": "update_required",
  "severity": "required",
  "blocked_operations": ["takeover_task"],
  "latest_version": "RES-v0.1.20-deadbee",
  "asset_version": "AST-v0.1.20-deadbee"
}
`)

	var stdout bytes.Buffer
	var stderr bytes.Buffer
	code := Run([]string{"takeover-task", "TAP-123", "--workspace", "tapstate"}, &stdout, &stderr)
	if code != 1 {
		t.Fatalf("code = %d stdout = %s stderr = %s", code, stdout.String(), stderr.String())
	}
	assertJSONField(t, stdout.String(), "code", "required_update_blocked")
	assertJSONField(t, stdout.String(), "operation", "takeover_task")
	assertJSONField(t, stdout.String(), "agentic_next_action", "update_apply")
}

func TestRequiredUpdateGuardExemptsDoctor(t *testing.T) {
	installDir := t.TempDir()
	t.Setenv("AGENTIC_OPS_HOME", installDir)
	writeCLITestFile(t, filepath.Join(installDir, ".local", "update-state.json"), `{
  "compatibility_state": "update_required",
  "severity": "required",
  "blocked_operations": ["doctor"]
}
`)

	var stdout bytes.Buffer
	var stderr bytes.Buffer
	Run([]string{"doctor", "--workspace", "missing-workspace"}, &stdout, &stderr)
	if strings.Contains(stdout.String(), `"code":"required_update_blocked"`) {
		t.Fatalf("doctor was blocked by update guard: %s", stdout.String())
	}
}

func TestUpdateCheckUsesRemoteManifestURL(t *testing.T) {
	installDir := t.TempDir()
	restore := update.SetHTTPClientForTest(&http.Client{Transport: cliRoundTripFunc(func(r *http.Request) *http.Response {
		if r.URL.String() != "https://updates.example.test/manifest.json" {
			t.Fatalf("url = %s", r.URL.String())
		}
		return cliHTTPResponse(http.StatusOK, `{
  "version": "RES-v0.1.20-deadbee",
  "asset_version": "RES-v0.1.20-deadbee",
  "min_cli_version": "RES-v0.1.20-deadbee",
  "min_asset_version": "RES-v0.1.20-deadbee",
  "compatibility_policy": "exact_pair",
  "asset_source": {"kind": "github_release", "repository": "tapstate/agentic-ops", "ref": "v0.1.20", "path": "agentic-ops-assets_RES-v0.1.20-deadbee.tar.gz"},
  "severity": "required"
}
`)
	})})
	defer restore()

	var stdout bytes.Buffer
	var stderr bytes.Buffer
	code := Run([]string{"update", "check", "--manifest-url", "https://updates.example.test/manifest.json", "--install-dir", installDir}, &stdout, &stderr)
	if code != 0 {
		t.Fatalf("code = %d stdout = %s stderr = %s", code, stdout.String(), stderr.String())
	}
	assertJSONField(t, stdout.String(), "operation", "update_check")
	assertJSONField(t, stdout.String(), "latest_version", "RES-v0.1.20-deadbee")
	assertJSONField(t, stdout.String(), "source", "remote")
	assertJSONField(t, stdout.String(), "agentic_next_action", "update_apply")
}
