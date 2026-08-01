package update

import (
	"archive/tar"
	"compress/gzip"
	"crypto/sha256"
	"encoding/json"
	"fmt"
	"io"
	"io/fs"
	"net/http"
	"net/url"
	"os"
	pathpkg "path"
	"path/filepath"
	"strings"
)

type Manifest struct {
	Version             string      `json:"version"`
	AssetVersion        string      `json:"asset_version"`
	MinCLIVersion       string      `json:"min_cli_version"`
	MinAssetVersion     string      `json:"min_asset_version"`
	AssetSource         AssetSource `json:"asset_source"`
	CompatibilityPolicy string      `json:"compatibility_policy"`
	MigrationRequired   bool        `json:"migration_required"`
	Severity            string      `json:"severity"`
	Reason              string      `json:"reason"`
	BlockedOperations   []string    `json:"blocked_operations"`
	ChecksumsURL        string      `json:"checksums_url"`
	Artifacts           []Artifact  `json:"artifacts"`
}

type AssetSource struct {
	Kind       string `json:"kind"`
	Repository string `json:"repository"`
	Ref        string `json:"ref"`
	Path       string `json:"path"`
}

type Artifact struct {
	Name   string `json:"name"`
	Target string `json:"target"`
	Type   string `json:"type"`
	URL    string `json:"url"`
	SHA256 string `json:"sha256"`
}

type CheckResult struct {
	CurrentVersion      string
	CurrentAssetVersion string
	LatestVersion       string
	AssetVersion        string
	MinCLIVersion       string
	MinAssetVersion     string
	CompatibilityPolicy string
	CompatibilityState  string
	MigrationRequired   bool
	UpdateAvailable     bool
	Severity            string
	Reason              string
	BlockedOperations   []string
	AgenticNextAction   string
}

type ApplyResult struct {
	AgenticCLIVersion         string
	AssetVersion              string
	PreviousAgenticCLIVersion string
	PreviousAssetVersion      string
	CurrentPath               string
	DownloadedArtifacts       []string
	ActivatedBinary           string
}

type RollbackResult struct {
	AgenticCLIVersion         string
	AssetVersion              string
	PreviousAgenticCLIVersion string
	PreviousAssetVersion      string
	CurrentPath               string
	ActivatedBinary           string
}

type currentState struct {
	AgenticCLIVersion         string `json:"agentic_cli_version,omitempty"`
	AssetVersion              string `json:"asset_version,omitempty"`
	PreviousAgenticCLIVersion string `json:"previous_agentic_cli_version,omitempty"`
	PreviousAssetVersion      string `json:"previous_asset_version,omitempty"`
	ActiveBinaryPath          string `json:"active_binary_path,omitempty"`
	ActiveAssetPath           string `json:"active_asset_path,omitempty"`
	PreviousBinaryPath        string `json:"previous_binary_path,omitempty"`
	PreviousBinarySHA256      string `json:"previous_binary_sha256,omitempty"`
	PreviousAssetPath         string `json:"previous_asset_path,omitempty"`
	CompatibilityPolicy       string `json:"compatibility_policy,omitempty"`
}

var httpClient = http.DefaultClient
var persistCurrent = writeCurrent

func SetHTTPClientForTest(client *http.Client) func() {
	previous := httpClient
	httpClient = client
	return func() {
		httpClient = previous
	}
}

func Check(manifestPath string, currentVersion string) (CheckResult, error) {
	return CheckWithCurrent(manifestPath, currentVersion, currentVersion)
}

func CheckWithCurrent(manifestPath string, currentVersion string, currentAssetVersion string) (CheckResult, error) {
	manifest, err := LoadManifest(manifestPath)
	if err != nil {
		return CheckResult{}, err
	}
	return checkManifest(manifest, currentVersion, currentAssetVersion)
}

func CheckRemote(manifestURL string, currentVersion string) (CheckResult, error) {
	return CheckRemoteWithCurrent(manifestURL, currentVersion, currentVersion)
}

func CheckRemoteWithCurrent(manifestURL string, currentVersion string, currentAssetVersion string) (CheckResult, error) {
	manifest, _, err := LoadRemoteManifest(manifestURL)
	if err != nil {
		return CheckResult{}, err
	}
	return checkManifest(manifest, currentVersion, currentAssetVersion)
}

func checkManifest(manifest Manifest, currentVersion string, currentAssetVersion string) (CheckResult, error) {
	if err := validateManifest(manifest); err != nil {
		return CheckResult{}, err
	}
	policy := manifest.CompatibilityPolicy
	if policy == "" {
		policy = "exact_pair"
	}
	if policy != "exact_pair" {
		return CheckResult{}, fmt.Errorf("unsupported compatibility_policy %q", policy)
	}
	assetVersion := manifest.AssetVersion
	if assetVersion == "" {
		assetVersion = manifest.Version
	}
	minCLIVersion := manifest.MinCLIVersion
	if minCLIVersion == "" {
		minCLIVersion = manifest.Version
	}
	minAssetVersion := manifest.MinAssetVersion
	if minAssetVersion == "" {
		minAssetVersion = assetVersion
	}
	severity := manifest.Severity
	if severity == "" {
		severity = "recommended"
	}
	available := manifest.Version != "" && (manifest.Version != currentVersion || assetVersion != currentAssetVersion)
	nextAction := "continue"
	compatibilityState := "compatible"
	if available {
		nextAction = "update_apply"
		compatibilityState = "update_available"
		if severity == "required" || manifest.MigrationRequired {
			compatibilityState = "update_required"
		}
	}
	return CheckResult{
		CurrentVersion:      currentVersion,
		CurrentAssetVersion: currentAssetVersion,
		LatestVersion:       manifest.Version,
		AssetVersion:        assetVersion,
		MinCLIVersion:       minCLIVersion,
		MinAssetVersion:     minAssetVersion,
		CompatibilityPolicy: policy,
		CompatibilityState:  compatibilityState,
		MigrationRequired:   manifest.MigrationRequired,
		UpdateAvailable:     available,
		Severity:            severity,
		Reason:              manifest.Reason,
		BlockedOperations:   manifest.BlockedOperations,
		AgenticNextAction:   nextAction,
	}, nil
}

func Apply(manifestPath string, installDir string) (ApplyResult, error) {
	manifest, err := LoadManifest(manifestPath)
	if err != nil {
		return ApplyResult{}, err
	}
	if err := validateManifest(manifest); err != nil {
		return ApplyResult{}, err
	}
	if manifest.AssetSource.Kind != "local_directory" {
		return ApplyResult{}, fmt.Errorf("local manifest requires local_directory asset_source")
	}
	stagedAssets, err := stageLocalAssets(manifestPath, manifest, installDir)
	if err != nil {
		return ApplyResult{}, err
	}
	return applyManifest(manifest, installDir, nil, "", stagedAssets)
}

func ApplyLocal(manifestPath string, installDir string, currentCLIVersion string) (ApplyResult, error) {
	manifest, err := LoadManifest(manifestPath)
	if err != nil {
		return ApplyResult{}, err
	}
	if err := validateManifest(manifest); err != nil {
		return ApplyResult{}, err
	}
	if manifest.Version != currentCLIVersion {
		return ApplyResult{}, fmt.Errorf("local update cannot replace running CLI: current=%q release=%q", currentCLIVersion, manifest.Version)
	}
	return Apply(manifestPath, installDir)
}

func ApplyRemote(manifestURL string, installDir string, target string) (ApplyResult, error) {
	manifest, baseURL, err := LoadRemoteManifest(manifestURL)
	if err != nil {
		return ApplyResult{}, err
	}
	if err := validateManifest(manifest); err != nil {
		return ApplyResult{}, err
	}
	if manifest.AssetSource.Kind != "github_release" {
		return ApplyResult{}, fmt.Errorf("remote manifest requires github_release asset_source")
	}
	downloads, stagedBinary, stagedAssets, err := downloadArtifacts(manifest, baseURL, installDir, target)
	if err != nil {
		return ApplyResult{}, err
	}
	return applyManifest(manifest, installDir, downloads, stagedBinary, stagedAssets)
}

func applyManifest(manifest Manifest, installDir string, downloads []string, stagedBinary string, stagedAssets string) (ApplyResult, error) {
	if err := validateManifest(manifest); err != nil {
		return ApplyResult{}, err
	}
	assetVersion := manifest.AssetVersion
	if assetVersion == "" {
		assetVersion = manifest.Version
	}
	currentPath := filepath.Join(installDir, "current.json")
	previous := readCurrent(currentPath)
	activeBinaryPath := previous.ActiveBinaryPath
	if activeBinaryPath == "" {
		activeBinaryPath = filepath.Join("bin", "agentic-cli")
	}
	previousBinaryPath := previous.PreviousBinaryPath
	previousBinarySHA256 := previous.PreviousBinarySHA256
	activatedBinary := ""
	if stagedBinary != "" {
		var err error
		previousBinaryPath, previousBinarySHA256, activatedBinary, err = switchBinary(installDir, activeBinaryPath, previous.AgenticCLIVersion, stagedBinary)
		if err != nil {
			return ApplyResult{}, err
		}
	}
	activeAssetPath := previous.ActiveAssetPath
	if stagedAssets != "" {
		relativeAssets, err := filepath.Rel(installDir, stagedAssets)
		if err != nil || relativeAssets == ".." || strings.HasPrefix(relativeAssets, ".."+string(filepath.Separator)) {
			return ApplyResult{}, fmt.Errorf("staged asset path escapes install directory")
		}
		activeAssetPath = relativeAssets
	}
	policy := manifest.CompatibilityPolicy
	if policy == "" {
		policy = "exact_pair"
	}
	next := currentState{
		AgenticCLIVersion:         manifest.Version,
		AssetVersion:              assetVersion,
		PreviousAgenticCLIVersion: previous.AgenticCLIVersion,
		PreviousAssetVersion:      previous.AssetVersion,
		ActiveBinaryPath:          activeBinaryPath,
		ActiveAssetPath:           activeAssetPath,
		PreviousBinaryPath:        previousBinaryPath,
		PreviousBinarySHA256:      previousBinarySHA256,
		PreviousAssetPath:         previous.ActiveAssetPath,
		CompatibilityPolicy:       policy,
	}
	if err := persistCurrent(currentPath, next); err != nil {
		if stagedBinary != "" && previousBinaryPath != "" {
			_ = copyFileAtomic(filepath.Join(installDir, previousBinaryPath), filepath.Join(installDir, activeBinaryPath), 0o755)
		} else if stagedBinary != "" && activatedBinary != "" {
			_ = os.Remove(activatedBinary)
		}
		return ApplyResult{}, err
	}
	_ = ClearCheckState(installDir)
	return ApplyResult{
		AgenticCLIVersion:         next.AgenticCLIVersion,
		AssetVersion:              next.AssetVersion,
		PreviousAgenticCLIVersion: next.PreviousAgenticCLIVersion,
		PreviousAssetVersion:      next.PreviousAssetVersion,
		CurrentPath:               currentPath,
		DownloadedArtifacts:       downloads,
		ActivatedBinary:           activatedBinary,
	}, nil
}

func validateManifest(manifest Manifest) error {
	if manifest.Version == "" {
		return fmt.Errorf("manifest version is required")
	}
	if manifest.CompatibilityPolicy == "" {
		return fmt.Errorf("compatibility_policy is required")
	}
	if manifest.CompatibilityPolicy != "exact_pair" {
		return fmt.Errorf("unsupported compatibility_policy %q", manifest.CompatibilityPolicy)
	}
	if manifest.AssetVersion == "" {
		return fmt.Errorf("asset_version is required for exact_pair")
	}
	if err := validateVersionSegment(manifest.Version); err != nil {
		return err
	}
	if err := validateVersionSegment(manifest.AssetVersion); err != nil {
		return err
	}
	if manifest.MinCLIVersion == "" {
		return fmt.Errorf("min_cli_version is required for exact_pair")
	}
	if manifest.MinAssetVersion == "" {
		return fmt.Errorf("min_asset_version is required for exact_pair")
	}
	if manifest.MinCLIVersion != manifest.Version || manifest.MinAssetVersion != manifest.AssetVersion {
		return fmt.Errorf("exact_pair requires min versions to equal release versions")
	}
	if manifest.AssetSource.Kind == "" || manifest.AssetSource.Path == "" {
		return fmt.Errorf("asset_source.kind and asset_source.path are required for exact_pair")
	}
	if manifest.AssetSource.Kind != "local_directory" && manifest.AssetSource.Kind != "github_release" {
		return fmt.Errorf("unsupported asset_source.kind %q", manifest.AssetSource.Kind)
	}
	if manifest.AssetSource.Kind == "github_release" && (manifest.AssetSource.Repository == "" || manifest.AssetSource.Ref == "") {
		return fmt.Errorf("asset_source.repository and asset_source.ref are required for github_release")
	}
	return nil
}

func validateVersionSegment(version string) error {
	if version == "" || version == "." || version == ".." || filepath.Base(version) != version || strings.Contains(version, "\\") {
		return fmt.Errorf("unsafe version %q", version)
	}
	return nil
}

func Rollback(installDir string) (RollbackResult, error) {
	currentPath := filepath.Join(installDir, "current.json")
	current := readCurrent(currentPath)
	if current.PreviousAgenticCLIVersion == "" || current.PreviousAssetVersion == "" || current.PreviousBinaryPath == "" || current.PreviousBinarySHA256 == "" {
		return RollbackResult{}, fmt.Errorf("rollback_state_missing: previous local state is unavailable")
	}
	previousBinary, err := resolveInstallPath(installDir, current.PreviousBinaryPath)
	if err != nil {
		return RollbackResult{}, fmt.Errorf("rollback_target_invalid: %w", err)
	}
	if info, err := os.Stat(previousBinary); err != nil || !info.Mode().IsRegular() {
		return RollbackResult{}, fmt.Errorf("rollback_target_invalid: previous binary is unavailable")
	}
	previousBinarySHA256, err := fileSHA256(previousBinary)
	if err != nil || !strings.EqualFold(previousBinarySHA256, current.PreviousBinarySHA256) {
		return RollbackResult{}, fmt.Errorf("rollback_target_invalid: previous binary checksum mismatch")
	}
	if current.PreviousAssetPath != "" {
		previousAssets, err := resolveInstallPath(installDir, current.PreviousAssetPath)
		if err != nil {
			return RollbackResult{}, fmt.Errorf("rollback_target_invalid: %w", err)
		}
		if info, err := os.Stat(previousAssets); err != nil || !info.IsDir() {
			return RollbackResult{}, fmt.Errorf("rollback_target_invalid: previous assets are unavailable")
		}
		if err := validateInstalledAssetVersion(previousAssets, current.PreviousAssetVersion); err != nil {
			return RollbackResult{}, fmt.Errorf("rollback_target_invalid: %w", err)
		}
	}
	activeBinaryPath := current.ActiveBinaryPath
	if activeBinaryPath == "" {
		activeBinaryPath = filepath.Join("bin", "agentic-cli")
	}
	activeBinary, err := resolveInstallPath(installDir, activeBinaryPath)
	if err != nil {
		return RollbackResult{}, fmt.Errorf("rollback_target_invalid: %w", err)
	}
	if info, err := os.Stat(activeBinary); err != nil || !info.Mode().IsRegular() {
		return RollbackResult{}, fmt.Errorf("rollback_target_invalid: active binary is unavailable")
	}
	currentSnapshot := filepath.Join("rollback", safeVersion(current.AgenticCLIVersion), "agentic-cli")
	currentSnapshotSHA256 := ""
	if err := copyFileAtomic(activeBinary, filepath.Join(installDir, currentSnapshot), 0o755); err != nil {
		return RollbackResult{}, fmt.Errorf("rollback_failed: %w", err)
	}
	currentSnapshotSHA256, err = fileSHA256(filepath.Join(installDir, currentSnapshot))
	if err != nil {
		return RollbackResult{}, fmt.Errorf("rollback_failed: %w", err)
	}
	if err := copyFileAtomic(previousBinary, activeBinary, 0o755); err != nil {
		return RollbackResult{}, fmt.Errorf("rollback_failed: %w", err)
	}
	next := currentState{
		AgenticCLIVersion:         current.PreviousAgenticCLIVersion,
		AssetVersion:              current.PreviousAssetVersion,
		PreviousAgenticCLIVersion: current.AgenticCLIVersion,
		PreviousAssetVersion:      current.AssetVersion,
		ActiveBinaryPath:          activeBinaryPath,
		ActiveAssetPath:           current.PreviousAssetPath,
		PreviousBinaryPath:        currentSnapshot,
		PreviousBinarySHA256:      currentSnapshotSHA256,
		PreviousAssetPath:         current.ActiveAssetPath,
		CompatibilityPolicy:       current.CompatibilityPolicy,
	}
	if err := persistCurrent(currentPath, next); err != nil {
		_ = copyFileAtomic(filepath.Join(installDir, currentSnapshot), activeBinary, 0o755)
		return RollbackResult{}, fmt.Errorf("rollback_failed: %w", err)
	}
	_ = ClearCheckState(installDir)
	return RollbackResult{
		AgenticCLIVersion:         next.AgenticCLIVersion,
		AssetVersion:              next.AssetVersion,
		PreviousAgenticCLIVersion: next.PreviousAgenticCLIVersion,
		PreviousAssetVersion:      next.PreviousAssetVersion,
		CurrentPath:               currentPath,
		ActivatedBinary:           activeBinary,
	}, nil
}

func validateInstalledAssetVersion(assetDir string, expectedVersion string) error {
	data, err := os.ReadFile(filepath.Join(assetDir, "manifest.json"))
	if err != nil {
		return fmt.Errorf("previous asset manifest is unavailable")
	}
	var manifest struct {
		AssetVersion string `json:"asset_version"`
	}
	if err := json.Unmarshal(data, &manifest); err != nil {
		return fmt.Errorf("previous asset manifest is invalid")
	}
	if manifest.AssetVersion != expectedVersion {
		return fmt.Errorf("previous asset manifest version mismatch")
	}
	return nil
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

func downloadArtifacts(manifest Manifest, baseURL *url.URL, installDir string, target string) ([]string, string, string, error) {
	if len(manifest.Artifacts) == 0 {
		return nil, "", "", fmt.Errorf("manifest artifacts are required")
	}
	checksums, err := loadChecksums(manifest, baseURL)
	if err != nil {
		return nil, "", "", err
	}
	selected := selectedArtifacts(manifest.Artifacts, target)
	if len(selected) == 0 {
		return nil, "", "", fmt.Errorf("no artifact for target %s", target)
	}
	assetVersion := manifest.AssetVersion
	if assetVersion == "" {
		assetVersion = manifest.Version
	}
	downloadDir := filepath.Join(installDir, "downloads", assetVersion)
	var downloads []string
	var binaryArchive string
	var assetArchive string
	for _, artifact := range selected {
		if err := validateArtifactName(artifact.Name); err != nil {
			return nil, "", "", err
		}
		artifactURL := artifact.URL
		if artifactURL == "" {
			artifactURL = baseURL.ResolveReference(&url.URL{Path: artifact.Name}).String()
		}
		data, err := fetchURL(artifactURL)
		if err != nil {
			return nil, "", "", err
		}
		expected := artifact.SHA256
		if expected == "" {
			expected = checksums[artifact.Name]
		}
		if expected == "" {
			return nil, "", "", fmt.Errorf("checksum missing for %s", artifact.Name)
		}
		actual := fmt.Sprintf("%x", sha256.Sum256(data))
		if !strings.EqualFold(actual, expected) {
			return nil, "", "", fmt.Errorf("checksum mismatch for %s", artifact.Name)
		}
		path := filepath.Join(downloadDir, artifact.Name)
		if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
			return nil, "", "", err
		}
		if err := os.WriteFile(path, data, 0o644); err != nil {
			return nil, "", "", err
		}
		downloads = append(downloads, path)
		if artifact.Type == "binary" {
			binaryArchive = path
		} else if artifact.Type == "assets" {
			assetArchive = path
		}
	}
	if binaryArchive == "" {
		return nil, "", "", fmt.Errorf("binary artifact missing for target %s", target)
	}
	if assetArchive == "" {
		return nil, "", "", fmt.Errorf("asset artifact missing for target %s", target)
	}
	if filepath.Base(manifest.AssetSource.Path) != filepath.Base(assetArchive) {
		return nil, "", "", fmt.Errorf("asset artifact does not match asset_source.path")
	}
	stagedBinary, err := stageBinaryArtifact(binaryArchive, installDir, manifest.Version)
	if err != nil {
		return nil, "", "", err
	}
	stagedAssets, err := stageAssetsArtifact(assetArchive, installDir, manifest)
	if err != nil {
		return nil, "", "", err
	}
	return downloads, stagedBinary, stagedAssets, nil
}

func stageLocalAssets(manifestPath string, manifest Manifest, installDir string) (string, error) {
	source := manifest.AssetSource.Path
	if !filepath.IsAbs(source) {
		source = filepath.Join(filepath.Dir(manifestPath), source)
	}
	if err := validateAssetDirectory(source, manifest); err != nil {
		return "", err
	}
	return copyAssetsToVersionDir(source, installDir, manifest.AssetVersion)
}

func stageAssetsArtifact(archivePath string, installDir string, manifest Manifest) (string, error) {
	parent := filepath.Join(installDir, "versions", safeVersion(manifest.AssetVersion))
	if err := os.MkdirAll(parent, 0o755); err != nil {
		return "", err
	}
	tempDir, err := os.MkdirTemp(parent, ".assets-*")
	if err != nil {
		return "", err
	}
	defer os.RemoveAll(tempDir)
	file, err := os.Open(archivePath)
	if err != nil {
		return "", err
	}
	defer file.Close()
	gzipReader, err := gzip.NewReader(file)
	if err != nil {
		return "", err
	}
	defer gzipReader.Close()
	tarReader := tar.NewReader(gzipReader)
	for {
		header, err := tarReader.Next()
		if err == io.EOF {
			break
		}
		if err != nil {
			return "", err
		}
		clean := pathpkg.Clean(header.Name)
		if clean == "." || strings.HasPrefix(clean, "../") || pathpkg.IsAbs(clean) {
			return "", fmt.Errorf("unsafe asset archive path %q", header.Name)
		}
		target := filepath.Join(tempDir, filepath.FromSlash(clean))
		switch header.Typeflag {
		case tar.TypeDir:
			if err := os.MkdirAll(target, 0o755); err != nil {
				return "", err
			}
		case tar.TypeReg:
			if err := os.MkdirAll(filepath.Dir(target), 0o755); err != nil {
				return "", err
			}
			data, err := io.ReadAll(tarReader)
			if err != nil {
				return "", err
			}
			if err := os.WriteFile(target, data, 0o644); err != nil {
				return "", err
			}
		default:
			return "", fmt.Errorf("unsupported asset archive entry %q", header.Name)
		}
	}
	if err := validateAssetDirectory(tempDir, manifest); err != nil {
		return "", err
	}
	target := filepath.Join(parent, "assets")
	if _, err := os.Stat(target); err == nil {
		return "", fmt.Errorf("asset target already exists: %s", target)
	} else if !os.IsNotExist(err) {
		return "", err
	}
	if err := os.Rename(tempDir, target); err != nil {
		return "", err
	}
	return target, nil
}

func validateAssetDirectory(source string, release Manifest) error {
	data, err := os.ReadFile(filepath.Join(source, "manifest.json"))
	if err != nil {
		return fmt.Errorf("asset manifest unavailable: %w", err)
	}
	var assetManifest struct {
		AssetVersion        string      `json:"asset_version"`
		MinCLIVersion       string      `json:"min_cli_version"`
		CompatibilityPolicy string      `json:"compatibility_policy"`
		AssetSource         AssetSource `json:"asset_source"`
	}
	if err := json.Unmarshal(data, &assetManifest); err != nil {
		return fmt.Errorf("asset manifest invalid: %w", err)
	}
	if assetManifest.AssetVersion != release.AssetVersion || assetManifest.MinCLIVersion != release.Version || assetManifest.CompatibilityPolicy != "exact_pair" {
		return fmt.Errorf("asset manifest does not match release exact_pair")
	}
	if assetManifest.AssetSource.Kind == "" || assetManifest.AssetSource.Path == "" {
		return fmt.Errorf("asset manifest source is required")
	}
	return nil
}

func copyAssetsToVersionDir(source string, installDir string, version string) (string, error) {
	sourceAbs, err := filepath.Abs(source)
	if err != nil {
		return "", err
	}
	installAbs, err := filepath.Abs(installDir)
	if err != nil {
		return "", err
	}
	relativeInstall, err := filepath.Rel(sourceAbs, installAbs)
	if err != nil {
		return "", err
	}
	if relativeInstall == "." || (relativeInstall != ".." && !strings.HasPrefix(relativeInstall, ".."+string(filepath.Separator))) {
		return "", fmt.Errorf("asset source contains install target")
	}
	parent := filepath.Join(installDir, "versions", safeVersion(version))
	if err := os.MkdirAll(parent, 0o755); err != nil {
		return "", err
	}
	tempDir, err := os.MkdirTemp(parent, ".assets-*")
	if err != nil {
		return "", err
	}
	defer os.RemoveAll(tempDir)
	if err := filepath.WalkDir(source, func(path string, entry fs.DirEntry, walkErr error) error {
		if walkErr != nil {
			return walkErr
		}
		relative, err := filepath.Rel(source, path)
		if err != nil {
			return err
		}
		if relative == "." {
			return nil
		}
		target := filepath.Join(tempDir, relative)
		if entry.IsDir() {
			return os.MkdirAll(target, 0o755)
		}
		if entry.Type()&os.ModeSymlink != 0 {
			return fmt.Errorf("asset source symlink is not allowed: %s", path)
		}
		data, err := os.ReadFile(path)
		if err != nil {
			return err
		}
		if err := os.MkdirAll(filepath.Dir(target), 0o755); err != nil {
			return err
		}
		return os.WriteFile(target, data, 0o644)
	}); err != nil {
		return "", err
	}
	target := filepath.Join(parent, "assets")
	if _, err := os.Stat(target); err == nil {
		return "", fmt.Errorf("asset target already exists: %s", target)
	} else if !os.IsNotExist(err) {
		return "", err
	}
	if err := os.Rename(tempDir, target); err != nil {
		return "", err
	}
	return target, nil
}

func stageBinaryArtifact(archivePath string, installDir string, version string) (string, error) {
	file, err := os.Open(archivePath)
	if err != nil {
		return "", err
	}
	defer file.Close()
	gzipReader, err := gzip.NewReader(file)
	if err != nil {
		return "", err
	}
	defer gzipReader.Close()
	tarReader := tar.NewReader(gzipReader)
	for {
		header, err := tarReader.Next()
		if err == io.EOF {
			break
		}
		if err != nil {
			return "", err
		}
		if header.Typeflag != tar.TypeReg {
			continue
		}
		if filepath.Base(header.Name) != "agentic-cli" || strings.Contains(header.Name, "..") {
			continue
		}
		data, err := io.ReadAll(tarReader)
		if err != nil {
			return "", err
		}
		binPath := filepath.Join(installDir, "versions", safeVersion(version), "agentic-cli")
		if err := os.MkdirAll(filepath.Dir(binPath), 0o755); err != nil {
			return "", err
		}
		if err := os.WriteFile(binPath, data, 0o755); err != nil {
			return "", err
		}
		return binPath, nil
	}
	return "", fmt.Errorf("agentic-cli binary not found in %s", archivePath)
}

func switchBinary(installDir string, activePath string, previousVersion string, stagedBinary string) (string, string, string, error) {
	activeBinary, err := resolveInstallPath(installDir, activePath)
	if err != nil {
		return "", "", "", err
	}
	previousPath := ""
	previousSHA256 := ""
	if info, statErr := os.Stat(activeBinary); statErr == nil && info.Mode().IsRegular() {
		previousPath = filepath.Join("rollback", safeVersion(previousVersion), "agentic-cli")
		if err := copyFileAtomic(activeBinary, filepath.Join(installDir, previousPath), 0o755); err != nil {
			return "", "", "", err
		}
		previousSHA256, err = fileSHA256(filepath.Join(installDir, previousPath))
		if err != nil {
			return "", "", "", err
		}
	}
	if err := copyFileAtomic(stagedBinary, activeBinary, 0o755); err != nil {
		return "", "", "", err
	}
	return previousPath, previousSHA256, activeBinary, nil
}

func fileSHA256(path string) (string, error) {
	data, err := os.ReadFile(path)
	if err != nil {
		return "", err
	}
	return fmt.Sprintf("%x", sha256.Sum256(data)), nil
}

func copyFileAtomic(source string, target string, mode os.FileMode) error {
	data, err := os.ReadFile(source)
	if err != nil {
		return err
	}
	if err := os.MkdirAll(filepath.Dir(target), 0o755); err != nil {
		return err
	}
	temp, err := os.CreateTemp(filepath.Dir(target), ".agentic-cli-*")
	if err != nil {
		return err
	}
	tempPath := temp.Name()
	defer os.Remove(tempPath)
	if _, err := temp.Write(data); err != nil {
		temp.Close()
		return err
	}
	if err := temp.Chmod(mode); err != nil {
		temp.Close()
		return err
	}
	if err := temp.Close(); err != nil {
		return err
	}
	return os.Rename(tempPath, target)
}

func resolveInstallPath(installDir string, relative string) (string, error) {
	if relative == "" || filepath.IsAbs(relative) {
		return "", fmt.Errorf("path must be relative to install directory")
	}
	clean := filepath.Clean(relative)
	if clean == ".." || strings.HasPrefix(clean, ".."+string(filepath.Separator)) {
		return "", fmt.Errorf("path escapes install directory")
	}
	return filepath.Join(installDir, clean), nil
}

func safeVersion(version string) string {
	if version == "" {
		return "unknown"
	}
	return strings.NewReplacer("/", "-", "\\", "-", "..", "-").Replace(version)
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

func ReadCurrentPair(installDir string) (string, string) {
	current := readCurrent(filepath.Join(installDir, "current.json"))
	return current.AgenticCLIVersion, current.AssetVersion
}

func writeCurrent(path string, current currentState) error {
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
