package assets

import (
	"encoding/json"
	"fmt"
	"io/fs"
	"os"
	"path/filepath"
)

type Current struct {
	AgentTaskOpsVersion         string `json:"agentic_cli_version,omitempty"`
	AssetVersion                string `json:"asset_version"`
	PreviousAgentTaskOpsVersion string `json:"previous_agentic_cli_version,omitempty"`
	PreviousAssetVersion        string `json:"previous_asset_version,omitempty"`
}

type InstallResult struct {
	AssetVersion string
	AssetsDir    string
	CurrentPath  string
}

func Install(sourceDir string, installDir string, version string) (InstallResult, error) {
	if version == "" {
		return InstallResult{}, fmt.Errorf("asset version is required")
	}
	info, err := os.Stat(sourceDir)
	if err != nil {
		return InstallResult{}, fmt.Errorf("read asset source: %w", err)
	}
	if !info.IsDir() {
		return InstallResult{}, fmt.Errorf("asset source is not a directory")
	}

	targetDir := filepath.Join(installDir, "assets", version)
	if err := copyDir(sourceDir, targetDir); err != nil {
		return InstallResult{}, err
	}

	currentPath := filepath.Join(installDir, "current.json")
	previous := readCurrent(currentPath)
	current := Current{
		AssetVersion:         version,
		PreviousAssetVersion: previous.AssetVersion,
	}
	if previous.AgentTaskOpsVersion != "" {
		current.AgentTaskOpsVersion = previous.AgentTaskOpsVersion
		current.PreviousAgentTaskOpsVersion = previous.AgentTaskOpsVersion
	}
	if err := writeCurrent(currentPath, current); err != nil {
		return InstallResult{}, err
	}

	return InstallResult{
		AssetVersion: version,
		AssetsDir:    targetDir,
		CurrentPath:  currentPath,
	}, nil
}

func copyDir(sourceDir string, targetDir string) error {
	return filepath.WalkDir(sourceDir, func(path string, entry fs.DirEntry, walkErr error) error {
		if walkErr != nil {
			return walkErr
		}
		relative, err := filepath.Rel(sourceDir, path)
		if err != nil {
			return err
		}
		if relative == "." {
			return os.MkdirAll(targetDir, 0o755)
		}
		target := filepath.Join(targetDir, relative)
		if entry.IsDir() {
			return os.MkdirAll(target, 0o755)
		}
		data, err := os.ReadFile(path)
		if err != nil {
			return err
		}
		if err := os.MkdirAll(filepath.Dir(target), 0o755); err != nil {
			return err
		}
		return os.WriteFile(target, data, 0o644)
	})
}

func readCurrent(path string) Current {
	data, err := os.ReadFile(path)
	if err != nil {
		return Current{}
	}
	var current Current
	if err := json.Unmarshal(data, &current); err != nil {
		return Current{}
	}
	return current
}

func writeCurrent(path string, current Current) error {
	data, err := json.MarshalIndent(current, "", "  ")
	if err != nil {
		return err
	}
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		return err
	}
	return os.WriteFile(path, append(data, '\n'), 0o644)
}
