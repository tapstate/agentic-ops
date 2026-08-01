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
	ActiveBinaryPath            string `json:"active_binary_path,omitempty"`
	ActiveAssetPath             string `json:"active_asset_path,omitempty"`
	PreviousBinaryPath          string `json:"previous_binary_path,omitempty"`
	PreviousBinarySHA256        string `json:"previous_binary_sha256,omitempty"`
	PreviousAssetPath           string `json:"previous_asset_path,omitempty"`
	CompatibilityPolicy         string `json:"compatibility_policy,omitempty"`
}

type InstallResult struct {
	AssetVersion        string
	AssetsDir           string
	CurrentPath         string
	CompatibilityPolicy string
}

type Manifest struct {
	AssetVersion        string      `json:"asset_version"`
	MinCLIVersion       string      `json:"min_cli_version"`
	CompatibilityPolicy string      `json:"compatibility_policy"`
	AssetSource         AssetSource `json:"asset_source"`
}

type AssetSource struct {
	Kind string `json:"kind"`
	Path string `json:"path"`
}

func Install(sourceDir string, installDir string, version string) (InstallResult, error) {
	return install(sourceDir, installDir, version, "")
}

func InstallCompatible(sourceDir string, installDir string, version string, currentCLIVersion string) (InstallResult, error) {
	if err := validateAssetVersion(version); err != nil {
		return InstallResult{}, err
	}
	manifestPath := filepath.Join(sourceDir, "manifest.json")
	data, err := os.ReadFile(manifestPath)
	if err != nil {
		if os.IsNotExist(err) {
			return InstallResult{}, fmt.Errorf("asset_manifest_missing: %s", manifestPath)
		}
		return InstallResult{}, fmt.Errorf("read asset manifest: %w", err)
	}
	var manifest Manifest
	if err := json.Unmarshal(data, &manifest); err != nil {
		return InstallResult{}, fmt.Errorf("asset_manifest_invalid: %w", err)
	}
	if manifest.AssetVersion == "" || manifest.AssetVersion != version {
		return InstallResult{}, fmt.Errorf("asset_version_mismatch: manifest=%q requested=%q", manifest.AssetVersion, version)
	}
	if err := validateAssetVersion(manifest.AssetVersion); err != nil {
		return InstallResult{}, err
	}
	if manifest.CompatibilityPolicy != "exact_pair" {
		return InstallResult{}, fmt.Errorf("unsupported compatibility_policy %q", manifest.CompatibilityPolicy)
	}
	if manifest.MinCLIVersion == "" || manifest.MinCLIVersion != currentCLIVersion {
		return InstallResult{}, fmt.Errorf("incompatible_cli_version: current=%q required=%q", currentCLIVersion, manifest.MinCLIVersion)
	}
	if manifest.AssetSource.Kind == "" || manifest.AssetSource.Path == "" {
		return InstallResult{}, fmt.Errorf("asset_manifest_invalid: asset_source.kind and asset_source.path are required")
	}
	if manifest.AssetSource.Kind != "local_directory" {
		return InstallResult{}, fmt.Errorf("unsupported asset_source.kind %q", manifest.AssetSource.Kind)
	}
	return install(sourceDir, installDir, version, manifest.CompatibilityPolicy)
}

func validateAssetVersion(version string) error {
	if version == "" || version == "." || version == ".." || filepath.Base(version) != version {
		return fmt.Errorf("unsafe asset version %q", version)
	}
	return nil
}

func install(sourceDir string, installDir string, version string, compatibilityPolicy string) (InstallResult, error) {
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
		ActiveAssetPath:      filepath.Join("assets", version),
		PreviousAssetPath:    previous.ActiveAssetPath,
		ActiveBinaryPath:     previous.ActiveBinaryPath,
		PreviousBinaryPath:   previous.PreviousBinaryPath,
		PreviousBinarySHA256: previous.PreviousBinarySHA256,
		CompatibilityPolicy:  compatibilityPolicy,
	}
	if previous.AgentTaskOpsVersion != "" {
		current.AgentTaskOpsVersion = previous.AgentTaskOpsVersion
		current.PreviousAgentTaskOpsVersion = previous.AgentTaskOpsVersion
	}
	if err := writeCurrent(currentPath, current); err != nil {
		return InstallResult{}, err
	}

	return InstallResult{
		AssetVersion:        version,
		AssetsDir:           targetDir,
		CurrentPath:         currentPath,
		CompatibilityPolicy: compatibilityPolicy,
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
	temp, err := os.CreateTemp(filepath.Dir(path), ".current-*.json")
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
