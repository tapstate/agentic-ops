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

func TestCheckRemoteDownloadsManifestURL(t *testing.T) {
	restore := SetHTTPClientForTest(&http.Client{Transport: updateRoundTripFunc(func(r *http.Request) *http.Response {
		if r.URL.String() != "https://updates.example.test/manifest.json" {
			t.Fatalf("url = %s", r.URL.String())
		}
		return updateHTTPResponse(http.StatusOK, `{
  "version": "RES-v0.1.20-deadbee",
  "asset_version": "RES-v0.1.20-deadbee",
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
	if result.NextAction != "update_apply" {
		t.Fatalf("NextAction = %s", result.NextAction)
	}
}

func TestApplyRemoteDownloadsArtifactsAndVerifiesChecksums(t *testing.T) {
	installDir := t.TempDir()
	version := "RES-v0.1.20-deadbee"
	binaryName := "agentic-cli_darwin-arm64.tar.gz"
	assetsName := "agentic-ops-assets_" + version + ".tar.gz"
	binaryContent := agenticCLITarGZ(t, "new binary")
	assetsContent := []byte("assets artifact")
	checksums := fmt.Sprintf("%x  %s\n%x  %s\n", sha256.Sum256(binaryContent), binaryName, sha256.Sum256(assetsContent), assetsName)
	restore := SetHTTPClientForTest(&http.Client{Transport: updateRoundTripFunc(func(r *http.Request) *http.Response {
		switch r.URL.String() {
		case "https://updates.example.test/manifest.json":
			return updateHTTPResponse(http.StatusOK, `{
  "version": "RES-v0.1.20-deadbee",
  "asset_version": "RES-v0.1.20-deadbee",
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
}

func TestApplyRemoteRejectsChecksumMismatch(t *testing.T) {
	installDir := t.TempDir()
	restore := SetHTTPClientForTest(&http.Client{Transport: updateRoundTripFunc(func(r *http.Request) *http.Response {
		switch r.URL.String() {
		case "https://updates.example.test/manifest.json":
			return updateHTTPResponse(http.StatusOK, `{
  "version": "RES-v0.1.20-deadbee",
  "asset_version": "RES-v0.1.20-deadbee",
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
