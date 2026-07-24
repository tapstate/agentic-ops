package git

import (
	"context"
	"os"
	"os/exec"
	"path/filepath"
	"testing"
)

func TestInspectWorkspaceReportsBranchDirtyAndChangedFiles(t *testing.T) {
	root := t.TempDir()
	runGitTestCommand(t, root, "init", "-b", "feature/tap-123")
	runGitTestCommand(t, root, "config", "user.email", "agent@example.com")
	runGitTestCommand(t, root, "config", "user.name", "Agentic Ops")
	if err := os.WriteFile(filepath.Join(root, "README.md"), []byte("# Demo\n"), 0o644); err != nil {
		t.Fatalf("WriteFile README error = %v", err)
	}
	runGitTestCommand(t, root, "add", "README.md")
	runGitTestCommand(t, root, "commit", "-m", "initial")
	if err := os.WriteFile(filepath.Join(root, "README.md"), []byte("# Demo\n\nchanged\n"), 0o644); err != nil {
		t.Fatalf("WriteFile README change error = %v", err)
	}
	if err := os.WriteFile(filepath.Join(root, "new.txt"), []byte("new\n"), 0o644); err != nil {
		t.Fatalf("WriteFile new file error = %v", err)
	}

	status, err := InspectWorkspace(context.Background(), root)
	if err != nil {
		t.Fatalf("InspectWorkspace error = %v", err)
	}
	if status.Branch != "feature/tap-123" {
		t.Fatalf("Branch = %q, want feature/tap-123", status.Branch)
	}
	if status.Commit == "" {
		t.Fatalf("Commit is empty")
	}
	if !status.Dirty {
		t.Fatalf("Dirty = false, want true")
	}
	if !contains(status.ChangedFiles, "README.md") || !contains(status.ChangedFiles, "new.txt") {
		t.Fatalf("ChangedFiles = %#v, want README.md and new.txt", status.ChangedFiles)
	}
}

func runGitTestCommand(t *testing.T, dir string, args ...string) {
	t.Helper()
	cmd := exec.Command("git", append([]string{"-C", dir}, args...)...)
	output, err := cmd.CombinedOutput()
	if err != nil {
		t.Fatalf("git %v failed: %v\n%s", args, err, string(output))
	}
}

func contains(values []string, want string) bool {
	for _, value := range values {
		if value == want {
			return true
		}
	}
	return false
}
