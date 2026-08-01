package update

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
)

type CheckState struct {
	CurrentVersion      string   `json:"current_version,omitempty"`
	CurrentAssetVersion string   `json:"current_asset_version,omitempty"`
	LatestVersion       string   `json:"latest_version,omitempty"`
	AssetVersion        string   `json:"asset_version,omitempty"`
	CompatibilityPolicy string   `json:"compatibility_policy,omitempty"`
	CompatibilityState  string   `json:"compatibility_state,omitempty"`
	MigrationRequired   bool     `json:"migration_required,omitempty"`
	Severity            string   `json:"severity,omitempty"`
	Reason              string   `json:"reason,omitempty"`
	BlockedOperations   []string `json:"blocked_operations,omitempty"`
}

func SaveCheckState(installDir string, result CheckResult) error {
	state := CheckState{
		CurrentVersion:      result.CurrentVersion,
		CurrentAssetVersion: result.CurrentAssetVersion,
		LatestVersion:       result.LatestVersion,
		AssetVersion:        result.AssetVersion,
		CompatibilityPolicy: result.CompatibilityPolicy,
		CompatibilityState:  result.CompatibilityState,
		MigrationRequired:   result.MigrationRequired,
		Severity:            result.Severity,
		Reason:              result.Reason,
		BlockedOperations:   result.BlockedOperations,
	}
	data, err := json.MarshalIndent(state, "", "  ")
	if err != nil {
		return err
	}
	path := checkStatePath(installDir)
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		return err
	}
	temp, err := os.CreateTemp(filepath.Dir(path), ".update-state-*.json")
	if err != nil {
		return err
	}
	tempPath := temp.Name()
	defer os.Remove(tempPath)
	if _, err := temp.Write(append(data, '\n')); err != nil {
		temp.Close()
		return err
	}
	if err := temp.Chmod(0o644); err != nil {
		temp.Close()
		return err
	}
	if err := temp.Close(); err != nil {
		return err
	}
	return os.Rename(tempPath, path)
}

func GuardOperation(installDir string, operation string) error {
	if guardExempt(operation) {
		return nil
	}
	data, err := os.ReadFile(checkStatePath(installDir))
	if os.IsNotExist(err) {
		return nil
	}
	if err != nil {
		return fmt.Errorf("update_state_invalid: %w", err)
	}
	var state CheckState
	if err := json.Unmarshal(data, &state); err != nil {
		return fmt.Errorf("update_state_invalid: %w", err)
	}
	if state.CompatibilityState != "update_required" {
		return nil
	}
	for _, blocked := range state.BlockedOperations {
		if blocked == operation {
			return fmt.Errorf("required_update_blocked: operation %s requires %s with assets %s", operation, state.LatestVersion, state.AssetVersion)
		}
	}
	return nil
}

func ClearCheckState(installDir string) error {
	err := os.Remove(checkStatePath(installDir))
	if os.IsNotExist(err) {
		return nil
	}
	return err
}

func checkStatePath(installDir string) string {
	return filepath.Join(installDir, ".local", "update-state.json")
}

func guardExempt(operation string) bool {
	switch operation {
	case "help", "version", "doctor", "preflight", "update_check", "update_apply", "update_rollback":
		return true
	default:
		return false
	}
}
