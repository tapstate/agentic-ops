package profile

import (
	"fmt"
	"os"
	"path/filepath"
)

type UpdateResult struct {
	Workspace  string
	TargetPath string
	SourcePath string
	BackupPath string
}

type RollbackResult struct {
	Workspace    string
	TargetPath   string
	RestoredFrom string
}

func UpdateFile(targetPath string, sourcePath string, workspace string) (UpdateResult, error) {
	source, err := LoadFile(sourcePath)
	if err != nil {
		return UpdateResult{}, fmt.Errorf("load source profile: %w", err)
	}
	if source.Workspace != workspace {
		return UpdateResult{}, fmt.Errorf("source profile workspace %q does not match %q", source.Workspace, workspace)
	}
	if issues := Validate(source); len(issues) > 0 {
		return UpdateResult{}, fmt.Errorf("source profile validation failed: %s", issues[0].Code)
	}
	currentData, err := os.ReadFile(targetPath)
	if err != nil {
		return UpdateResult{}, fmt.Errorf("read current profile: %w", err)
	}
	sourceData, err := os.ReadFile(sourcePath)
	if err != nil {
		return UpdateResult{}, fmt.Errorf("read source profile: %w", err)
	}
	backupPath := targetPath + ".bak"
	if err := os.MkdirAll(filepath.Dir(targetPath), 0o755); err != nil {
		return UpdateResult{}, err
	}
	if err := os.WriteFile(backupPath, currentData, 0o644); err != nil {
		return UpdateResult{}, fmt.Errorf("write profile backup: %w", err)
	}
	if err := os.WriteFile(targetPath, sourceData, 0o644); err != nil {
		return UpdateResult{}, fmt.Errorf("write updated profile: %w", err)
	}
	return UpdateResult{
		Workspace:  workspace,
		TargetPath: targetPath,
		SourcePath: sourcePath,
		BackupPath: backupPath,
	}, nil
}

func RollbackFile(targetPath string, workspace string) (RollbackResult, error) {
	backupPath := targetPath + ".bak"
	backup, err := LoadFile(backupPath)
	if err != nil {
		return RollbackResult{}, fmt.Errorf("load profile backup: %w", err)
	}
	if backup.Workspace != workspace {
		return RollbackResult{}, fmt.Errorf("backup profile workspace %q does not match %q", backup.Workspace, workspace)
	}
	if issues := Validate(backup); len(issues) > 0 {
		return RollbackResult{}, fmt.Errorf("backup profile validation failed: %s", issues[0].Code)
	}
	backupData, err := os.ReadFile(backupPath)
	if err != nil {
		return RollbackResult{}, fmt.Errorf("read profile backup: %w", err)
	}
	if err := os.WriteFile(targetPath, backupData, 0o644); err != nil {
		return RollbackResult{}, fmt.Errorf("restore profile backup: %w", err)
	}
	return RollbackResult{
		Workspace:    workspace,
		TargetPath:   targetPath,
		RestoredFrom: backupPath,
	}, nil
}
