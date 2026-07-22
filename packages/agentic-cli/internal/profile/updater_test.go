package profile

import (
	"os"
	"path/filepath"
	"testing"
)

func TestUpdateFileInstallsValidatedProfileAndWritesBackup(t *testing.T) {
	dir := t.TempDir()
	target := filepath.Join(dir, "tapstate.yaml")
	source := filepath.Join(dir, "source.yaml")
	writeProfileTestFile(t, target, validProfileYAML("tapstate", "TAP"))
	writeProfileTestFile(t, source, validProfileYAML("tapstate", "OPS"))

	result, err := UpdateFile(target, source, "tapstate")
	if err != nil {
		t.Fatalf("UpdateFile error = %v", err)
	}

	if result.Workspace != "tapstate" {
		t.Fatalf("Workspace = %s", result.Workspace)
	}
	if result.BackupPath == "" {
		t.Fatalf("BackupPath is empty")
	}
	updated, err := LoadFile(target)
	if err != nil {
		t.Fatalf("LoadFile updated error = %v", err)
	}
	if updated.Jira.Project != "OPS" {
		t.Fatalf("updated Jira.Project = %s", updated.Jira.Project)
	}
	backup, err := LoadFile(result.BackupPath)
	if err != nil {
		t.Fatalf("LoadFile backup error = %v", err)
	}
	if backup.Jira.Project != "TAP" {
		t.Fatalf("backup Jira.Project = %s", backup.Jira.Project)
	}
}

func TestUpdateFileRejectsWorkspaceMismatch(t *testing.T) {
	dir := t.TempDir()
	target := filepath.Join(dir, "tapstate.yaml")
	source := filepath.Join(dir, "source.yaml")
	writeProfileTestFile(t, target, validProfileYAML("tapstate", "TAP"))
	writeProfileTestFile(t, source, validProfileYAML("other", "OPS"))

	if _, err := UpdateFile(target, source, "tapstate"); err == nil {
		t.Fatalf("UpdateFile error = nil, want mismatch error")
	}

	kept, err := LoadFile(target)
	if err != nil {
		t.Fatalf("LoadFile target error = %v", err)
	}
	if kept.Workspace != "tapstate" || kept.Jira.Project != "TAP" {
		t.Fatalf("target changed after rejected update: %+v", kept)
	}
}

func TestRollbackFileRestoresBackup(t *testing.T) {
	dir := t.TempDir()
	target := filepath.Join(dir, "tapstate.yaml")
	backup := target + ".bak"
	writeProfileTestFile(t, target, validProfileYAML("tapstate", "OPS"))
	writeProfileTestFile(t, backup, validProfileYAML("tapstate", "TAP"))

	result, err := RollbackFile(target, "tapstate")
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
	if restored.Jira.Project != "TAP" {
		t.Fatalf("restored Jira.Project = %s", restored.Jira.Project)
	}
}

func writeProfileTestFile(t *testing.T, path string, content string) {
	t.Helper()
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		t.Fatalf("MkdirAll error = %v", err)
	}
	if err := os.WriteFile(path, []byte(content), 0o644); err != nil {
		t.Fatalf("WriteFile error = %v", err)
	}
}

func validProfileYAML(workspace string, jiraProject string) string {
	return "workspace: " + workspace + "\n" +
		"jira:\n" +
		"  project: " + jiraProject + "\n" +
		"  task_query: project = " + jiraProject + "\n" +
		"jira_form_mapping:\n" +
		"  fields:\n" +
		"    owner:\n" +
		"      source: jira_assignee\n" +
		"task_class_mapping:\n" +
		"  issue_types:\n" +
		"    Task: technical_task\n" +
		"standard_process_mapping:\n" +
		"  technical_task: development-change-v1\n" +
		"status_mapping:\n" +
		"  in_progress: In Progress\n" +
		"transition_mapping:\n" +
		"  start_progress: Start Progress\n" +
		"local:\n" +
		"  source_root: /tmp/source\n"
}
