package cli

import (
	"bytes"
	"github.com/tapstate/agentic-ops/packages/agentic-cli/internal/update"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestAssetsInstallCopiesAssetsToInstallDir(t *testing.T) {
	source := t.TempDir()
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
	if _, err := os.Stat(filepath.Join(installDir, "assets", "RES-v0.1.1-a68372d", "handbooks", "ai-employee-handbook.md")); err != nil {
		t.Fatalf("installed asset missing: %v", err)
	}
}

func TestUpdateCheckAndApplyUseLocalManifest(t *testing.T) {
	dir := t.TempDir()
	manifestPath := filepath.Join(dir, "manifest.json")
	installDir := filepath.Join(dir, "install")
	writeCLITestFile(t, manifestPath, `{
  "version": "RES-v0.1.20-deadbee",
  "asset_version": "RES-v0.1.20-deadbee",
  "severity": "required",
  "reason": "takeover_task 可能写入无效证据",
  "blocked_operations": ["takeover_task"]
}
`)

	var checkStdout bytes.Buffer
	var checkStderr bytes.Buffer
	checkCode := Run([]string{"update", "check", "--manifest", manifestPath}, &checkStdout, &checkStderr)
	if checkCode != 0 {
		t.Fatalf("checkCode = %d stdout = %s stderr = %s", checkCode, checkStdout.String(), checkStderr.String())
	}
	assertJSONField(t, checkStdout.String(), "operation", "update_check")
	assertJSONField(t, checkStdout.String(), "update_available", true)
	assertJSONField(t, checkStdout.String(), "severity", "required")
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
	assertJSONField(t, applyStdout.String(), "version", "RES-v0.1.20-deadbee")
	assertJSONField(t, applyStdout.String(), "asset_version", "RES-v0.1.20-deadbee")
	assertJSONField(t, applyStdout.String(), "agentic_next_action", "doctor")
	if _, err := os.Stat(filepath.Join(installDir, "current.json")); err != nil {
		t.Fatalf("current.json missing: %v", err)
	}
}

func TestUpdateCheckUsesRemoteManifestURL(t *testing.T) {
	restore := update.SetHTTPClientForTest(&http.Client{Transport: cliRoundTripFunc(func(r *http.Request) *http.Response {
		if r.URL.String() != "https://updates.example.test/manifest.json" {
			t.Fatalf("url = %s", r.URL.String())
		}
		return cliHTTPResponse(http.StatusOK, `{
  "version": "RES-v0.1.20-deadbee",
  "asset_version": "RES-v0.1.20-deadbee",
  "severity": "required"
}
`)
	})})
	defer restore()

	var stdout bytes.Buffer
	var stderr bytes.Buffer
	code := Run([]string{"update", "check", "--manifest-url", "https://updates.example.test/manifest.json"}, &stdout, &stderr)
	if code != 0 {
		t.Fatalf("code = %d stdout = %s stderr = %s", code, stdout.String(), stderr.String())
	}
	assertJSONField(t, stdout.String(), "operation", "update_check")
	assertJSONField(t, stdout.String(), "latest_version", "RES-v0.1.20-deadbee")
	assertJSONField(t, stdout.String(), "source", "remote")
	assertJSONField(t, stdout.String(), "agentic_next_action", "update_apply")
}
