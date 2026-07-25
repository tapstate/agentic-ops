package commandcatalog

import (
	"bytes"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"testing"
)

func TestGeneratedCatalogIsFresh(t *testing.T) {
	_, file, _, ok := runtime.Caller(0)
	if !ok {
		t.Fatalf("runtime.Caller failed")
	}
	repoRoot := filepath.Clean(filepath.Join(filepath.Dir(file), "..", "..", "..", ".."))
	expectedPath := filepath.Join(repoRoot, "packages", "agentic-cli", "internal", "commandcatalog", "zz_generated.go")
	tempPath := filepath.Join(t.TempDir(), "zz_generated.go")

	cmd := exec.Command("bash", filepath.Join(repoRoot, "scripts", "generate-command-catalog.sh"))
	cmd.Dir = repoRoot
	cmd.Env = append(os.Environ(), "AGENTIC_OPS_COMMAND_CATALOG_OUT="+tempPath)
	output, err := cmd.CombinedOutput()
	if err != nil {
		t.Fatalf("generate command catalog failed: %v\n%s", err, string(output))
	}
	expected, err := os.ReadFile(expectedPath)
	if err != nil {
		t.Fatalf("ReadFile expected catalog error = %v", err)
	}
	generated, err := os.ReadFile(tempPath)
	if err != nil {
		t.Fatalf("ReadFile generated catalog error = %v", err)
	}
	if !bytes.Equal(expected, generated) {
		t.Fatalf("command catalog is stale; run bash scripts/generate-command-catalog.sh")
	}
}
