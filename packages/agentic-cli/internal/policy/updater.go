package policy

import (
	"fmt"
	"os"
	"path/filepath"
)

type UpdateResult struct {
	Policy     string
	TargetPath string
	SourcePath string
	BackupPath string
}

type RollbackResult struct {
	Policy       string
	TargetPath   string
	RestoredFrom string
}

func UpdateFile(targetPath string, sourcePath string, policyName string) (UpdateResult, error) {
	source, err := LoadFile(sourcePath)
	if err != nil {
		return UpdateResult{}, fmt.Errorf("load source policy: %w", err)
	}
	if source.Policy != policyName {
		return UpdateResult{}, fmt.Errorf("source policy %q does not match %q", source.Policy, policyName)
	}
	if issues := Validate(source); len(issues) > 0 {
		return UpdateResult{}, fmt.Errorf("source policy validation failed: %s", issues[0].Code)
	}
	currentData, err := os.ReadFile(targetPath)
	if err != nil {
		return UpdateResult{}, fmt.Errorf("read current policy: %w", err)
	}
	sourceData, err := os.ReadFile(sourcePath)
	if err != nil {
		return UpdateResult{}, fmt.Errorf("read source policy: %w", err)
	}
	backupPath := targetPath + ".bak"
	if err := os.MkdirAll(filepath.Dir(targetPath), 0o755); err != nil {
		return UpdateResult{}, err
	}
	if err := os.WriteFile(backupPath, currentData, 0o644); err != nil {
		return UpdateResult{}, fmt.Errorf("write policy backup: %w", err)
	}
	if err := os.WriteFile(targetPath, sourceData, 0o644); err != nil {
		return UpdateResult{}, fmt.Errorf("write updated policy: %w", err)
	}
	return UpdateResult{
		Policy:     policyName,
		TargetPath: targetPath,
		SourcePath: sourcePath,
		BackupPath: backupPath,
	}, nil
}

func RollbackFile(targetPath string, policyName string) (RollbackResult, error) {
	backupPath := targetPath + ".bak"
	backup, err := LoadFile(backupPath)
	if err != nil {
		return RollbackResult{}, fmt.Errorf("load policy backup: %w", err)
	}
	if backup.Policy != policyName {
		return RollbackResult{}, fmt.Errorf("backup policy %q does not match %q", backup.Policy, policyName)
	}
	if issues := Validate(backup); len(issues) > 0 {
		return RollbackResult{}, fmt.Errorf("backup policy validation failed: %s", issues[0].Code)
	}
	backupData, err := os.ReadFile(backupPath)
	if err != nil {
		return RollbackResult{}, fmt.Errorf("read policy backup: %w", err)
	}
	if err := os.WriteFile(targetPath, backupData, 0o644); err != nil {
		return RollbackResult{}, fmt.Errorf("restore policy backup: %w", err)
	}
	return RollbackResult{
		Policy:       policyName,
		TargetPath:   targetPath,
		RestoredFrom: backupPath,
	}, nil
}
