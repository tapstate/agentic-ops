package policy

import (
	"os"
	"path/filepath"
	"testing"
)

func TestUpdateFileInstallsValidatedPolicyAndWritesBackup(t *testing.T) {
	dir := t.TempDir()
	target := filepath.Join(dir, "default.yaml")
	source := filepath.Join(dir, "source.yaml")
	writePolicyTestFile(t, target, validPolicyYAML("default", false))
	writePolicyTestFile(t, source, validPolicyYAML("default", true))

	result, err := UpdateFile(target, source, "default")
	if err != nil {
		t.Fatalf("UpdateFile error = %v", err)
	}

	if result.Policy != "default" {
		t.Fatalf("Policy = %s", result.Policy)
	}
	if result.BackupPath == "" {
		t.Fatalf("BackupPath is empty")
	}
	updated, err := LoadFile(target)
	if err != nil {
		t.Fatalf("LoadFile updated error = %v", err)
	}
	if !updated.Gates["write_jira_comment"].Required {
		t.Fatalf("updated write_jira_comment required = false")
	}
	backup, err := LoadFile(result.BackupPath)
	if err != nil {
		t.Fatalf("LoadFile backup error = %v", err)
	}
	if backup.Gates["write_jira_comment"].Required {
		t.Fatalf("backup write_jira_comment required = true")
	}
}

func TestUpdateFileRejectsPolicyNameMismatch(t *testing.T) {
	dir := t.TempDir()
	target := filepath.Join(dir, "default.yaml")
	source := filepath.Join(dir, "source.yaml")
	writePolicyTestFile(t, target, validPolicyYAML("default", false))
	writePolicyTestFile(t, source, validPolicyYAML("other", true))

	if _, err := UpdateFile(target, source, "default"); err == nil {
		t.Fatalf("UpdateFile error = nil, want mismatch error")
	}

	kept, err := LoadFile(target)
	if err != nil {
		t.Fatalf("LoadFile target error = %v", err)
	}
	if kept.Gates["write_jira_comment"].Required {
		t.Fatalf("target changed after rejected update: %+v", kept)
	}
}

func TestRollbackFileRestoresBackup(t *testing.T) {
	dir := t.TempDir()
	target := filepath.Join(dir, "default.yaml")
	backup := target + ".bak"
	writePolicyTestFile(t, target, validPolicyYAML("default", true))
	writePolicyTestFile(t, backup, validPolicyYAML("default", false))

	result, err := RollbackFile(target, "default")
	if err != nil {
		t.Fatalf("RollbackFile error = %v", err)
	}

	if result.RestoredFrom != backup {
		t.Fatalf("RestoredFrom = %s, want %s", result.RestoredFrom, backup)
	}
	restored, err := LoadFile(target)
	if err != nil {
		t.Fatalf("LoadFile restored error = %v", err)
	}
	if restored.Gates["write_jira_comment"].Required {
		t.Fatalf("restored write_jira_comment required = true")
	}
}

func writePolicyTestFile(t *testing.T, path string, content string) {
	t.Helper()
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		t.Fatalf("MkdirAll error = %v", err)
	}
	if err := os.WriteFile(path, []byte(content), 0o644); err != nil {
		t.Fatalf("WriteFile error = %v", err)
	}
}

func validPolicyYAML(policyName string, requireJiraComment bool) string {
	jiraCommentRequired := "false"
	if requireJiraComment {
		jiraCommentRequired = "true"
	}
	return "policy: " + policyName + "\n" +
		"version: 1\n" +
		"gates:\n" +
		"  write_jira_comment:\n" +
		"    required: " + jiraCommentRequired + "\n" +
		"  write_local_evidence:\n" +
		"    required: false\n" +
		"  transition_jira_status:\n" +
		"    required: true\n" +
		"  git_commit:\n" +
		"    required: true\n" +
		"  git_push:\n" +
		"    required: true\n" +
		"  git_merge:\n" +
		"    required: true\n" +
		"  git_rebase:\n" +
		"    required: true\n" +
		"  git_clean:\n" +
		"    required: true\n" +
		"  create_pr:\n" +
		"    required: true\n" +
		"  update_pr:\n" +
		"    required: true\n" +
		"  fix_pr_comments:\n" +
		"    required: true\n" +
		"  scope_change:\n" +
		"    required: true\n"
}
