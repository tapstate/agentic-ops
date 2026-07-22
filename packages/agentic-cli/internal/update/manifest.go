package update

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
)

type Manifest struct {
	Version           string   `json:"version"`
	AssetVersion      string   `json:"asset_version"`
	Severity          string   `json:"severity"`
	Reason            string   `json:"reason"`
	BlockedOperations []string `json:"blocked_operations"`
}

type CheckResult struct {
	CurrentVersion    string
	LatestVersion     string
	AssetVersion      string
	UpdateAvailable   bool
	Severity          string
	Reason            string
	BlockedOperations []string
	NextAction        string
}

type ApplyResult struct {
	AgenticCLIVersion         string
	AssetVersion              string
	PreviousAgenticCLIVersion string
	PreviousAssetVersion      string
	CurrentPath               string
}

type currentState struct {
	AgenticCLIVersion         string `json:"agentic_cli_version,omitempty"`
	AssetVersion              string `json:"asset_version,omitempty"`
	PreviousAgenticCLIVersion string `json:"previous_agentic_cli_version,omitempty"`
	PreviousAssetVersion      string `json:"previous_asset_version,omitempty"`
}

func Check(manifestPath string, currentVersion string) (CheckResult, error) {
	manifest, err := LoadManifest(manifestPath)
	if err != nil {
		return CheckResult{}, err
	}
	severity := manifest.Severity
	if severity == "" {
		severity = "recommended"
	}
	available := manifest.Version != "" && manifest.Version != currentVersion
	nextAction := "continue"
	if available {
		nextAction = "update_apply"
	}
	return CheckResult{
		CurrentVersion:    currentVersion,
		LatestVersion:     manifest.Version,
		AssetVersion:      manifest.AssetVersion,
		UpdateAvailable:   available,
		Severity:          severity,
		Reason:            manifest.Reason,
		BlockedOperations: manifest.BlockedOperations,
		NextAction:        nextAction,
	}, nil
}

func Apply(manifestPath string, installDir string) (ApplyResult, error) {
	manifest, err := LoadManifest(manifestPath)
	if err != nil {
		return ApplyResult{}, err
	}
	if manifest.Version == "" {
		return ApplyResult{}, fmt.Errorf("manifest version is required")
	}
	assetVersion := manifest.AssetVersion
	if assetVersion == "" {
		assetVersion = manifest.Version
	}
	currentPath := filepath.Join(installDir, "current.json")
	previous := readCurrent(currentPath)
	next := currentState{
		AgenticCLIVersion:         manifest.Version,
		AssetVersion:              assetVersion,
		PreviousAgenticCLIVersion: previous.AgenticCLIVersion,
		PreviousAssetVersion:      previous.AssetVersion,
	}
	if err := writeCurrent(currentPath, next); err != nil {
		return ApplyResult{}, err
	}
	return ApplyResult{
		AgenticCLIVersion:         next.AgenticCLIVersion,
		AssetVersion:              next.AssetVersion,
		PreviousAgenticCLIVersion: next.PreviousAgenticCLIVersion,
		PreviousAssetVersion:      next.PreviousAssetVersion,
		CurrentPath:               currentPath,
	}, nil
}

func LoadManifest(path string) (Manifest, error) {
	data, err := os.ReadFile(path)
	if err != nil {
		return Manifest{}, err
	}
	var manifest Manifest
	if err := json.Unmarshal(data, &manifest); err != nil {
		return Manifest{}, err
	}
	return manifest, nil
}

func readCurrent(path string) currentState {
	data, err := os.ReadFile(path)
	if err != nil {
		return currentState{}
	}
	var current currentState
	if err := json.Unmarshal(data, &current); err != nil {
		return currentState{}
	}
	return current
}

func writeCurrent(path string, current currentState) error {
	data, err := json.MarshalIndent(current, "", "  ")
	if err != nil {
		return err
	}
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		return err
	}
	return os.WriteFile(path, append(data, '\n'), 0o644)
}
