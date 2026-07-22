package update

import (
	"crypto/sha256"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"os"
	pathpkg "path"
	"path/filepath"
	"strings"
)

type Manifest struct {
	Version           string     `json:"version"`
	AssetVersion      string     `json:"asset_version"`
	Severity          string     `json:"severity"`
	Reason            string     `json:"reason"`
	BlockedOperations []string   `json:"blocked_operations"`
	ChecksumsURL      string     `json:"checksums_url"`
	Artifacts         []Artifact `json:"artifacts"`
}

type Artifact struct {
	Name   string `json:"name"`
	Target string `json:"target"`
	Type   string `json:"type"`
	URL    string `json:"url"`
	SHA256 string `json:"sha256"`
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
	DownloadedArtifacts       []string
}

type currentState struct {
	AgenticCLIVersion         string `json:"agentic_cli_version,omitempty"`
	AssetVersion              string `json:"asset_version,omitempty"`
	PreviousAgenticCLIVersion string `json:"previous_agentic_cli_version,omitempty"`
	PreviousAssetVersion      string `json:"previous_asset_version,omitempty"`
}

var httpClient = http.DefaultClient

func SetHTTPClientForTest(client *http.Client) func() {
	previous := httpClient
	httpClient = client
	return func() {
		httpClient = previous
	}
}

func Check(manifestPath string, currentVersion string) (CheckResult, error) {
	manifest, err := LoadManifest(manifestPath)
	if err != nil {
		return CheckResult{}, err
	}
	return checkManifest(manifest, currentVersion), nil
}

func CheckRemote(manifestURL string, currentVersion string) (CheckResult, error) {
	manifest, _, err := LoadRemoteManifest(manifestURL)
	if err != nil {
		return CheckResult{}, err
	}
	return checkManifest(manifest, currentVersion), nil
}

func checkManifest(manifest Manifest, currentVersion string) CheckResult {
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
	}
}

func Apply(manifestPath string, installDir string) (ApplyResult, error) {
	manifest, err := LoadManifest(manifestPath)
	if err != nil {
		return ApplyResult{}, err
	}
	return applyManifest(manifest, installDir, nil)
}

func ApplyRemote(manifestURL string, installDir string, target string) (ApplyResult, error) {
	manifest, baseURL, err := LoadRemoteManifest(manifestURL)
	if err != nil {
		return ApplyResult{}, err
	}
	downloads, err := downloadArtifacts(manifest, baseURL, installDir, target)
	if err != nil {
		return ApplyResult{}, err
	}
	return applyManifest(manifest, installDir, downloads)
}

func applyManifest(manifest Manifest, installDir string, downloads []string) (ApplyResult, error) {
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
		DownloadedArtifacts:       downloads,
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

func LoadRemoteManifest(manifestURL string) (Manifest, *url.URL, error) {
	data, err := fetchURL(manifestURL)
	if err != nil {
		return Manifest{}, nil, err
	}
	var manifest Manifest
	if err := json.Unmarshal(data, &manifest); err != nil {
		return Manifest{}, nil, err
	}
	baseURL, err := baseURLFor(manifestURL)
	if err != nil {
		return Manifest{}, nil, err
	}
	return manifest, baseURL, nil
}

func downloadArtifacts(manifest Manifest, baseURL *url.URL, installDir string, target string) ([]string, error) {
	if len(manifest.Artifacts) == 0 {
		return nil, fmt.Errorf("manifest artifacts are required")
	}
	checksums, err := loadChecksums(manifest, baseURL)
	if err != nil {
		return nil, err
	}
	selected := selectedArtifacts(manifest.Artifacts, target)
	if len(selected) == 0 {
		return nil, fmt.Errorf("no artifact for target %s", target)
	}
	assetVersion := manifest.AssetVersion
	if assetVersion == "" {
		assetVersion = manifest.Version
	}
	downloadDir := filepath.Join(installDir, "downloads", assetVersion)
	var downloads []string
	for _, artifact := range selected {
		if err := validateArtifactName(artifact.Name); err != nil {
			return nil, err
		}
		artifactURL := artifact.URL
		if artifactURL == "" {
			artifactURL = baseURL.ResolveReference(&url.URL{Path: artifact.Name}).String()
		}
		data, err := fetchURL(artifactURL)
		if err != nil {
			return nil, err
		}
		expected := artifact.SHA256
		if expected == "" {
			expected = checksums[artifact.Name]
		}
		if expected == "" {
			return nil, fmt.Errorf("checksum missing for %s", artifact.Name)
		}
		actual := fmt.Sprintf("%x", sha256.Sum256(data))
		if !strings.EqualFold(actual, expected) {
			return nil, fmt.Errorf("checksum mismatch for %s", artifact.Name)
		}
		path := filepath.Join(downloadDir, artifact.Name)
		if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
			return nil, err
		}
		if err := os.WriteFile(path, data, 0o644); err != nil {
			return nil, err
		}
		downloads = append(downloads, path)
	}
	return downloads, nil
}

func loadChecksums(manifest Manifest, baseURL *url.URL) (map[string]string, error) {
	checksumsURL := manifest.ChecksumsURL
	if checksumsURL == "" {
		checksumsURL = baseURL.ResolveReference(&url.URL{Path: "checksums.txt"}).String()
	}
	data, err := fetchURL(checksumsURL)
	if err != nil {
		return nil, err
	}
	checksums := map[string]string{}
	for _, line := range strings.Split(string(data), "\n") {
		fields := strings.Fields(line)
		if len(fields) >= 2 {
			checksums[fields[1]] = fields[0]
		}
	}
	return checksums, nil
}

func selectedArtifacts(artifacts []Artifact, target string) []Artifact {
	var selected []Artifact
	for _, artifact := range artifacts {
		switch artifact.Type {
		case "binary":
			if artifact.Target == target {
				selected = append(selected, artifact)
			}
		case "assets":
			if artifact.Target == "" || artifact.Target == "all" || artifact.Target == target {
				selected = append(selected, artifact)
			}
		}
	}
	return selected
}

func validateArtifactName(name string) error {
	if name == "" {
		return fmt.Errorf("artifact name is required")
	}
	if filepath.Base(name) != name || strings.Contains(name, "\\") {
		return fmt.Errorf("artifact name must not contain path separators")
	}
	return nil
}

func fetchURL(remoteURL string) ([]byte, error) {
	request, err := http.NewRequest(http.MethodGet, remoteURL, nil)
	if err != nil {
		return nil, err
	}
	response, err := httpClient.Do(request)
	if err != nil {
		return nil, err
	}
	defer response.Body.Close()
	if response.StatusCode < 200 || response.StatusCode >= 300 {
		return nil, fmt.Errorf("download %s failed with status %d", remoteURL, response.StatusCode)
	}
	return io.ReadAll(response.Body)
}

func baseURLFor(manifestURL string) (*url.URL, error) {
	parsed, err := url.Parse(manifestURL)
	if err != nil {
		return nil, err
	}
	dir := pathpkg.Dir(parsed.Path)
	if dir == "." || dir == "/" {
		parsed.Path = "/"
	} else {
		parsed.Path = dir + "/"
	}
	parsed.RawQuery = ""
	parsed.Fragment = ""
	return parsed, nil
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
