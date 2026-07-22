package workspace

import (
	"os"
	"path/filepath"
	"testing"
)

func TestEnsureCreatesWorkspaceDirs(t *testing.T) {
	root := t.TempDir()
	info, err := Ensure(root, "tapstate")
	if err != nil {
		t.Fatalf("Ensure error = %v", err)
	}
	if info.Name != "tapstate" {
		t.Fatalf("Name = %s", info.Name)
	}
	for _, dir := range []string{info.RunsDir, info.FeedbackDir} {
		stat, err := os.Stat(dir)
		if err != nil {
			t.Fatalf("missing dir %s: %v", dir, err)
		}
		if !stat.IsDir() {
			t.Fatalf("%s is not dir", dir)
		}
	}
	if filepath.Base(filepath.Dir(info.RunsDir)) != ".agentic-ops" {
		t.Fatalf("RunsDir = %s", info.RunsDir)
	}
}

func TestEnsureRejectsEmptyWorkspaceName(t *testing.T) {
	_, err := Ensure(t.TempDir(), "")
	if err == nil {
		t.Fatal("expected error for empty workspace name")
	}
}
