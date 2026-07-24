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
	for _, dir := range []string{info.RunsDir, info.RunLogsDir, info.FeedbackDir, info.ProfilesDir} {
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
	if filepath.Base(info.RunLogsDir) != "run-logs" {
		t.Fatalf("RunLogsDir = %s", info.RunLogsDir)
	}
	if filepath.Base(info.ProfilesDir) != "profiles" {
		t.Fatalf("ProfilesDir = %s", info.ProfilesDir)
	}
}

func TestEnsureRejectsEmptyWorkspaceName(t *testing.T) {
	_, err := Ensure(t.TempDir(), "")
	if err == nil {
		t.Fatal("expected error for empty workspace name")
	}
}

func TestResolveProjectPathReplacesWorkspacePlaceholder(t *testing.T) {
	got := ResolveProjectPath("<project-ai-workspace>/src", "/tmp/agentic-workspace")
	if got != "/tmp/agentic-workspace/src" {
		t.Fatalf("ResolveProjectPath = %q", got)
	}
}

func TestDirectoryStatusReportsExistsAndCreatable(t *testing.T) {
	root := t.TempDir()
	existing := DirectoryStatus(root)
	if existing.Status != "ok" || existing.Message != "exists" {
		t.Fatalf("existing status = %#v", existing)
	}

	creatable := DirectoryStatus(filepath.Join(root, "new", "dir"))
	if creatable.Status != "ok" || creatable.Message != "creatable" {
		t.Fatalf("creatable status = %#v", creatable)
	}
}
