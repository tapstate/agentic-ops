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

func TestPlanTapdataBranchAlignmentForDevelopUsesPluginKitAndKeepsApplication(t *testing.T) {
	workRoot := t.TempDir()
	initTapdataAlignmentFixture(t, workRoot)

	plan, err := PlanTapdataBranchAlignment(context.Background(), BranchAlignmentRequest{
		WorkRoot:   workRoot,
		BranchSpec: "develop",
		NoFetch:    true,
	})
	if err != nil {
		t.Fatalf("PlanTapdataBranchAlignment error = %v", err)
	}
	if plan.Blocked {
		t.Fatalf("Blocked = true, rows = %#v", plan.Rows)
	}
	assertPlanTarget(t, plan, "tapdata", "develop", "skip")
	assertPlanTarget(t, plan, "tapdata-enterprise", "develop", "switch")
	assertPlanTarget(t, plan, "tapdata-web", "develop", "switch")
	assertPlanTarget(t, plan, "tapdata-license", "main", "switch")
	assertPlanTarget(t, plan, "tapdata-connectors", "release-v4.9", "switch")
	assertPlanTarget(t, plan, "tapdata-connectors-enterprise", "release-v4.9", "switch")
	assertPlanTarget(t, plan, "tapdata-common-lib", "release-v5.0", "switch")
	assertPlanTarget(t, plan, "tapdata-application", "KEEP_CURRENT", "keep")
}

func TestPlanTapdataBranchAlignmentBlocksUnresolvedEnterpriseAndWeb(t *testing.T) {
	workRoot := t.TempDir()
	initTapdataAlignmentFixture(t, workRoot)
	runGitTestCommand(t, filepath.Join(workRoot, "tapdata"), "switch", "-c", "feature/TAP-999-demo")

	plan, err := PlanTapdataBranchAlignment(context.Background(), BranchAlignmentRequest{
		WorkRoot:   workRoot,
		BranchSpec: "feature/TAP-999-demo",
		NoFetch:    true,
	})
	if err != nil {
		t.Fatalf("PlanTapdataBranchAlignment error = %v", err)
	}
	if !plan.Blocked {
		t.Fatalf("Blocked = false, want true; rows = %#v", plan.Rows)
	}
	assertPlanTarget(t, plan, "tapdata-enterprise", "UNRESOLVED", "blocked")
	assertPlanTarget(t, plan, "tapdata-web", "UNRESOLVED", "blocked")
}

func TestApplyTapdataBranchAlignmentStashesDirtyReposAndSwitches(t *testing.T) {
	workRoot := t.TempDir()
	initTapdataAlignmentFixture(t, workRoot)
	dirtyPath := filepath.Join(workRoot, "tapdata-enterprise", "dirty.txt")
	if err := os.WriteFile(dirtyPath, []byte("keep me\n"), 0o644); err != nil {
		t.Fatalf("WriteFile dirty file error = %v", err)
	}
	plan, err := PlanTapdataBranchAlignment(context.Background(), BranchAlignmentRequest{
		WorkRoot:   workRoot,
		BranchSpec: "develop",
		NoFetch:    true,
	})
	if err != nil {
		t.Fatalf("PlanTapdataBranchAlignment error = %v", err)
	}
	switched, err := ApplyTapdataBranchAlignment(context.Background(), plan)
	if err != nil {
		t.Fatalf("ApplyTapdataBranchAlignment error = %v", err)
	}
	if len(switched) == 0 {
		t.Fatalf("switched rows empty")
	}
	status, err := InspectWorkspace(context.Background(), filepath.Join(workRoot, "tapdata-enterprise"))
	if err != nil {
		t.Fatalf("InspectWorkspace error = %v", err)
	}
	if status.Branch != "develop" {
		t.Fatalf("enterprise branch = %q, want develop", status.Branch)
	}
	if _, err := os.Stat(dirtyPath); err != nil {
		t.Fatalf("dirty file not restored after stash pop: %v", err)
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

func initTapdataAlignmentFixture(t *testing.T, workRoot string) {
	t.Helper()
	initTapdataRepoForAlignment(t, workRoot, "tapdata", "develop", nil)
	pluginPath := filepath.Join(workRoot, "tapdata", tapdataPluginPath)
	if err := os.MkdirAll(filepath.Dir(pluginPath), 0o755); err != nil {
		t.Fatalf("MkdirAll plugin path error = %v", err)
	}
	if err := os.WriteFile(pluginPath, []byte("tapdata.api.verison=4.9-SNAPSHOT\n"), 0o644); err != nil {
		t.Fatalf("WriteFile pluginKit error = %v", err)
	}
	runGitTestCommand(t, filepath.Join(workRoot, "tapdata"), "add", tapdataPluginPath)
	runGitTestCommand(t, filepath.Join(workRoot, "tapdata"), "commit", "-m", "plugin version")

	initTapdataRepoForAlignment(t, workRoot, "tapdata-enterprise", "main", []string{"develop"})
	initTapdataRepoForAlignment(t, workRoot, "tapdata-web", "main", []string{"develop"})
	initTapdataRepoForAlignment(t, workRoot, "tapdata-connectors", "main", []string{"release-v4.8", "release-v4.9"})
	initTapdataRepoForAlignment(t, workRoot, "tapdata-connectors-enterprise", "main", []string{"release-v4.9"})
	initTapdataRepoForAlignment(t, workRoot, "tapdata-license", "develop", []string{"main"})
	initTapdataRepoForAlignment(t, workRoot, "tapdata-common-lib", "main", []string{"release-v5.0"})
	initTapdataRepoForAlignment(t, workRoot, "tapdata-application", "feature/local", nil)
}

func initTapdataRepoForAlignment(t *testing.T, workRoot string, repo string, branch string, extraBranches []string) {
	t.Helper()
	root := filepath.Join(workRoot, repo)
	if err := os.MkdirAll(root, 0o755); err != nil {
		t.Fatalf("MkdirAll repo error = %v", err)
	}
	runGitTestCommand(t, root, "init", "-b", branch)
	runGitTestCommand(t, root, "config", "user.email", "agent@example.com")
	runGitTestCommand(t, root, "config", "user.name", "Agentic Ops")
	if err := os.WriteFile(filepath.Join(root, "README.md"), []byte("# "+repo+"\n"), 0o644); err != nil {
		t.Fatalf("WriteFile README error = %v", err)
	}
	runGitTestCommand(t, root, "add", "README.md")
	runGitTestCommand(t, root, "commit", "-m", "initial")
	for _, extra := range extraBranches {
		runGitTestCommand(t, root, "branch", extra)
	}
}

func assertPlanTarget(t *testing.T, plan BranchAlignmentPlan, repo string, target string, action string) {
	t.Helper()
	for _, row := range plan.Rows {
		if row.Repo == repo {
			if row.Target != target || row.Action != action {
				t.Fatalf("%s row = %#v, want target %q action %q", repo, row, target, action)
			}
			return
		}
	}
	t.Fatalf("repo %s not found in rows %#v", repo, plan.Rows)
}
