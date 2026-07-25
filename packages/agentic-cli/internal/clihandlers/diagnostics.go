package clihandlers

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"github.com/tapstate/agentic-ops/packages/agentic-cli/internal/assets"
	"github.com/tapstate/agentic-ops/packages/agentic-cli/internal/config"
	"github.com/tapstate/agentic-ops/packages/agentic-cli/internal/contract"
	"github.com/tapstate/agentic-ops/packages/agentic-cli/internal/output"
	"github.com/tapstate/agentic-ops/packages/agentic-cli/internal/policy"
	"github.com/tapstate/agentic-ops/packages/agentic-cli/internal/profile"
	"github.com/tapstate/agentic-ops/packages/agentic-cli/internal/workspace"
	"io"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"time"
)

func runDoctor(args []string, stdout io.Writer) int {
	workspaceName := workspaceNameFromArgsOrAgentConfig(args, "default")
	installDir := readInstallDir(args)
	checks := map[string]map[string]string{
		"install":      checkInstallDir(installDir),
		"version":      {"status": "ok", "message": Version},
		"current":      checkCurrentInstall(installDir),
		"workspace":    checkWorkspaceRoot(),
		"profile":      checkProfile(workspaceName),
		"local_paths":  checkLocalPaths(workspaceName),
		"policy":       checkPolicy(),
		"contracts":    checkContracts(),
		"jira_adapter": checkJiraAdapter(workspaceName, hasFlag(args, "--check-real-jira")),
		"github":       checkGitHubAuth(hasFlag(args, "--check-github")),
	}
	status := "ok"
	for _, check := range checks {
		if check["status"] == "failed" {
			status = "failed"
			break
		}
	}
	nextAction := "continue"
	if status != "ok" {
		nextAction = "fix_environment"
	}
	return writeJSON(stdout, output.Success("doctor", map[string]any{
		"workspace":         workspaceName,
		"version":           Version,
		"version_state":     VersionState,
		"iteration_version": IterationVersion,
		"commit":            Commit,
		"status":            status,
		"checks":            checks,
		"current":           checks["current"],
		"local_paths":       checks["local_paths"],
		"next_action":       nextAction,
	}))
}

func checkJiraAdapter(workspaceName string, realCheck bool) map[string]string {
	if !realCheck {
		return map[string]string{"status": "ok", "message": "fake adapter available"}
	}
	workspaceProfile := takeoverProfile(workspaceName)
	selection, err := selectJiraClient(workspaceName, workspaceProfile)
	if err != nil {
		return map[string]string{"status": "failed", "message": err.Error()}
	}
	if selection.Mode != "real" {
		return map[string]string{"status": "failed", "message": "real Jira adapter is not active"}
	}
	currentUser, err := selection.Client.CurrentUser(context.Background())
	if err != nil {
		return map[string]string{"status": "failed", "message": err.Error()}
	}
	return map[string]string{"status": "ok", "message": "real adapter authenticated as " + currentUser}
}

func checkGitHubAuth(realCheck bool) map[string]string {
	if !realCheck {
		return map[string]string{"status": "skipped", "message": "GitHub CLI check requires --check-github"}
	}
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	if err := runGitHubAuthStatus(ctx); err != nil {
		return map[string]string{"status": "failed", "message": err.Error()}
	}
	return map[string]string{"status": "ok", "message": "GitHub CLI authenticated"}
}

func checkCommandAvailable(name string) map[string]string {
	if commandAvailable(name) {
		return map[string]string{"status": "ok", "message": name + " available"}
	}
	return map[string]string{"status": "failed", "message": name + " not found in PATH"}
}

func runPreflight(args []string, stdout io.Writer) int {
	workspaceName := workspaceNameFromArgsOrAgentConfig(args, "default")
	installDir := readInstallDir(args)
	workspaceProfile := takeoverProfile(workspaceName)
	checks := map[string]map[string]string{
		"runtime":           {"status": "ok", "message": runtime.GOOS + "/" + runtime.GOARCH},
		"version":           {"status": "ok", "message": Version},
		"git":               checkCommandAvailable("git"),
		"github_cli":        checkCommandAvailable("gh"),
		"github_auth":       checkGitHubAuth(hasFlag(args, "--check-github")),
		"profile":           checkProfile(workspaceName),
		"current_directory": checkCurrentDirectoryAllowed(workspaceProfile),
	}
	status := statusFromChecks(checks)
	nextAction := "workspace_init"
	if status != "ok" {
		nextAction = "fix_environment"
	}
	return writeJSON(stdout, output.Success("preflight", map[string]any{
		"workspace":   workspaceName,
		"install_dir": installDir,
		"os":          runtime.GOOS,
		"arch":        runtime.GOARCH,
		"version":     Version,
		"status":      status,
		"checks":      checks,
		"next_action": nextAction,
	}))
}

func checkInstallDir(installDir string) map[string]string {
	if installDir == "" {
		return map[string]string{"status": "failed", "message": "install dir is empty"}
	}
	if _, err := os.Stat(installDir); err != nil {
		return map[string]string{"status": "ok", "message": "install dir will be created when needed"}
	}
	return map[string]string{"status": "ok", "message": installDir}
}

func checkWorkspaceRoot() map[string]string {
	root, err := workspaceRoot()
	if err != nil {
		return map[string]string{"status": "failed", "message": err.Error()}
	}
	if _, err := os.Stat(root); err != nil {
		return map[string]string{"status": "failed", "message": err.Error()}
	}
	return map[string]string{"status": "ok", "message": root}
}

func checkProfile(workspaceName string) map[string]string {
	path, err := repoProfilePath(workspaceName)
	if err != nil {
		return map[string]string{"status": "failed", "message": "repo root not found"}
	}
	loadedProfile, err := profile.LoadFile(path)
	if err != nil {
		return map[string]string{"status": "failed", "message": err.Error()}
	}
	if issues := profile.Validate(loadedProfile); len(issues) > 0 {
		return map[string]string{"status": "failed", "message": issues[0].Code}
	}
	registry, err := repoProcessRegistry()
	if err != nil {
		return map[string]string{"status": "failed", "message": err.Error()}
	}
	if issues := profile.ValidateProcesses(loadedProfile, registry); len(issues) > 0 {
		return map[string]string{"status": "failed", "message": issues[0].Code}
	}
	return map[string]string{"status": "ok", "message": path}
}

func checkCurrentInstall(installDir string) map[string]string {
	path := config.CurrentPath(installDir)
	data, err := os.ReadFile(path)
	if err != nil {
		if errors.Is(err, os.ErrNotExist) {
			return map[string]string{"status": "skipped", "message": "current.json not found"}
		}
		return map[string]string{"status": "failed", "message": err.Error()}
	}
	var current assets.Current
	if err := json.Unmarshal(data, &current); err != nil {
		return map[string]string{"status": "failed", "message": err.Error()}
	}
	if strings.TrimSpace(current.AssetVersion) == "" {
		return map[string]string{"status": "failed", "message": "asset_version missing in current.json"}
	}
	if current.AgentTaskOpsVersion != "" && current.AgentTaskOpsVersion != Version {
		return map[string]string{"status": "failed", "message": "installed CLI version " + current.AgentTaskOpsVersion + " does not match running " + Version}
	}
	return map[string]string{"status": "ok", "message": path}
}

func checkLocalPaths(workspaceName string) map[string]string {
	workspaceProfile, err := loadWorkspaceProfile(workspaceName)
	if err != nil {
		return map[string]string{"status": "failed", "message": err.Error()}
	}
	root, err := workspaceRoot()
	if err != nil {
		return map[string]string{"status": "failed", "message": err.Error()}
	}
	sourceRoot := workspace.ResolveProjectPath(workspaceProfile.Local.SourceRoot, root)
	if strings.TrimSpace(sourceRoot) == "." || strings.TrimSpace(sourceRoot) == "" {
		sourceRoot = root
	}
	if stat, err := os.Stat(sourceRoot); err != nil || !stat.IsDir() {
		if err != nil {
			return map[string]string{"status": "failed", "message": "source_root " + sourceRoot + ": " + err.Error()}
		}
		return map[string]string{"status": "failed", "message": "source_root is not a directory: " + sourceRoot}
	}
	for name, path := range map[string]string{
		"runs_dir":     workspaceProfile.Local.RunsDir,
		"feedback_dir": workspaceProfile.Local.FeedbackDir,
		"run_logs_dir": workspaceProfile.Local.RunLogsDir,
	} {
		resolved := workspace.ResolveProjectPath(path, root)
		status := workspace.DirectoryStatus(resolved)
		if status.Status != "ok" {
			return map[string]string{"status": "failed", "message": name + " " + resolved + ": " + status.Message}
		}
	}
	return map[string]string{"status": "ok", "message": "local paths valid"}
}

func checkCurrentDirectoryAllowed(workspaceProfile profile.Profile) map[string]string {
	cwd, err := os.Getwd()
	if err != nil {
		return map[string]string{"status": "failed", "message": err.Error()}
	}
	root, err := workspaceRoot()
	if err != nil {
		return map[string]string{"status": "failed", "message": err.Error()}
	}
	sourceRoot := workspace.ResolveProjectPath(workspaceProfile.Local.SourceRoot, root)
	if strings.TrimSpace(sourceRoot) == "." || strings.TrimSpace(sourceRoot) == "" {
		sourceRoot = root
	}
	if pathWithin(cwd, root) || pathWithin(cwd, sourceRoot) {
		return map[string]string{"status": "ok", "message": cwd}
	}
	return map[string]string{"status": "failed", "message": cwd + " is outside workspace_root and source_root"}
}

func statusFromChecks(checks map[string]map[string]string) string {
	for _, check := range checks {
		if check["status"] == "failed" {
			return "failed"
		}
	}
	return "ok"
}

func checkPolicy() map[string]string {
	path, err := repoPolicyPath()
	if err != nil {
		return map[string]string{"status": "failed", "message": "repo root not found"}
	}
	loadedPolicy, err := policy.LoadFile(path)
	if err != nil {
		return map[string]string{"status": "failed", "message": err.Error()}
	}
	if issues := policy.Validate(loadedPolicy); len(issues) > 0 {
		return map[string]string{"status": "failed", "message": issues[0].Code}
	}
	return map[string]string{"status": "ok", "message": path}
}

func checkContracts() map[string]string {
	root, err := repoRoot()
	if err != nil {
		return map[string]string{"status": "failed", "message": "repo root not found"}
	}
	paths, err := filepath.Glob(filepath.Join(repoBasicResourcesPath(root), "contracts", "operations", "*.yaml"))
	if err != nil {
		return map[string]string{"status": "failed", "message": err.Error()}
	}
	if len(paths) == 0 {
		return map[string]string{"status": "failed", "message": "operation contracts not found"}
	}
	for _, path := range paths {
		op, err := contract.LoadFile(path)
		if err != nil {
			return map[string]string{"status": "failed", "message": err.Error()}
		}
		if issues := contract.Validate(op); len(issues) > 0 {
			return map[string]string{"status": "failed", "message": issues[0].Code}
		}
	}
	return map[string]string{"status": "ok", "message": fmt.Sprintf("%d operation contracts", len(paths))}
}
