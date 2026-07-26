package clihandlers

import (
	"os"
	"path/filepath"

	"github.com/tapstate/agentic-ops/packages/agentic-cli/internal/config"
	"github.com/tapstate/agentic-ops/packages/agentic-cli/internal/profile"
)

type profileResolution struct {
	Effective profile.Profile
	Layers    []profileLayer
}

type profileLayer struct {
	Name string `json:"name"`
	Path string `json:"path"`
	Used bool   `json:"used"`
}

func resolveEffectiveProfile(workspaceName string, workspaceRoot string) (profile.Profile, error) {
	resolution, err := resolveProfileWithLayers(workspaceName, workspaceRoot)
	if err != nil {
		return profile.Profile{}, err
	}
	return resolution.Effective, nil
}

func resolveProfileWithLayers(workspaceName string, workspaceRoot string) (profileResolution, error) {
	projectProfilePath, err := repoProjectProfilePath(workspaceName)
	if err != nil {
		return profileResolution{}, err
	}
	effective, err := profile.LoadFile(projectProfilePath)
	if err != nil {
		return profileResolution{}, err
	}
	resolution := profileResolution{
		Effective: effective,
		Layers: []profileLayer{
			{Name: "company", Path: companyLayerPath(), Used: false},
			{Name: "project_package", Path: projectProfilePath, Used: true},
		},
	}

	if personalPath := personalProfileOverlayPath(workspaceName); personalPath != "" {
		used, err := mergeProfileFileIfExists(&resolution.Effective, personalPath)
		if err != nil {
			return profileResolution{}, err
		}
		resolution.Layers = append(resolution.Layers, profileLayer{Name: "personal", Path: personalPath, Used: used})
	}
	if workspaceRoot != "" {
		workspaceOverlay := filepath.Join(workspaceRoot, ".agentic-ops", "profile.local.yaml")
		used, err := mergeProfileFileIfExists(&resolution.Effective, workspaceOverlay)
		if err != nil {
			return profileResolution{}, err
		}
		resolution.Layers = append(resolution.Layers, profileLayer{Name: "workspace_overlay", Path: workspaceOverlay, Used: used})
	}
	return resolution, nil
}

func companyLayerPath() string {
	path, err := repoCompanyPath()
	if err != nil {
		return "$HOME/.agentic-ops/install-resources/basic/company"
	}
	return path
}

func personalProfileOverlayPath(workspaceName string) string {
	home, err := os.UserHomeDir()
	if err != nil {
		return ""
	}
	installDir := os.Getenv("AGENTIC_OPS_HOME")
	if installDir == "" {
		installDir = config.DefaultInstallDir(home)
	}
	return filepath.Join(installDir, "user", "projects", workspaceName, "profile.local.yaml")
}

func mergeProfileFileIfExists(target *profile.Profile, path string) (bool, error) {
	stat, err := os.Stat(path)
	if err != nil {
		if os.IsNotExist(err) {
			return false, nil
		}
		return false, err
	}
	if stat.IsDir() {
		return false, nil
	}
	overlay, err := profile.LoadFile(path)
	if err != nil {
		return false, err
	}
	mergeProfile(target, overlay)
	return true, nil
}

func mergeProfile(target *profile.Profile, overlay profile.Profile) {
	if overlay.Workspace != "" {
		target.Workspace = overlay.Workspace
	}
	mergeJiraConfig(&target.Jira, overlay.Jira)
	mergeFormMapping(&target.JiraFormMapping, overlay.JiraFormMapping)
	mergeTaskClassMapping(&target.TaskClassMapping, overlay.TaskClassMapping)
	mergeStringMap(&target.StandardProcessMapping, overlay.StandardProcessMapping)
	mergeStringMap(&target.StatusMapping, overlay.StatusMapping)
	mergeStringMap(&target.TransitionMapping, overlay.TransitionMapping)
	mergeJiraTransitionMap(&target.JiraTransitionMapping, overlay.JiraTransitionMapping)
	mergeGitHubConfig(&target.GitHub, overlay.GitHub)
	mergeLocalConfig(&target.Local, overlay.Local)
	if len(overlay.Standards) > 0 {
		target.Standards = append([]string{}, overlay.Standards...)
	}
	if len(overlay.HumanGates) > 0 {
		target.HumanGates = append([]string{}, overlay.HumanGates...)
	}
	mergeReviewGateMap(&target.ReviewGates, overlay.ReviewGates)
	mergeRetryRedoMap(&target.RetryRedo, overlay.RetryRedo)
	mergeStringMap(&target.Templates, overlay.Templates)
}

func mergeJiraConfig(target *profile.JiraConfig, overlay profile.JiraConfig) {
	if overlay.User != "" {
		target.User = overlay.User
	}
	if overlay.Project != "" {
		target.Project = overlay.Project
	}
	if overlay.TaskQuery != "" {
		target.TaskQuery = overlay.TaskQuery
	}
}

func mergeFormMapping(target *profile.FormMapping, overlay profile.FormMapping) {
	if len(overlay.Fields) == 0 {
		return
	}
	if target.Fields == nil {
		target.Fields = map[string]profile.FormField{}
	}
	for key, value := range overlay.Fields {
		target.Fields[key] = value
	}
}

func mergeTaskClassMapping(target *profile.TaskClassMapping, overlay profile.TaskClassMapping) {
	mergeStringMap(&target.IssueTypes, overlay.IssueTypes)
	mergeStringMap(&target.Labels, overlay.Labels)
	mergeStringMap(&target.Components, overlay.Components)
}

func mergeGitHubConfig(target *profile.GitHubConfig, overlay profile.GitHubConfig) {
	if overlay.Organization != "" {
		target.Organization = overlay.Organization
	}
	if overlay.Repositories.Default != "" {
		target.Repositories.Default = overlay.Repositories.Default
	}
	mergeStringMap(&target.Repositories.ByComponent, overlay.Repositories.ByComponent)
	mergeStringMap(&target.Repositories.ByLabel, overlay.Repositories.ByLabel)
	mergeStringMap(&target.Repositories.ByIssueType, overlay.Repositories.ByIssueType)
}

func mergeLocalConfig(target *profile.LocalConfig, overlay profile.LocalConfig) {
	if overlay.WorkspaceRoot != "" {
		target.WorkspaceRoot = overlay.WorkspaceRoot
	}
	if overlay.SourceRoot != "" {
		target.SourceRoot = overlay.SourceRoot
	}
	if overlay.RunsDir != "" {
		target.RunsDir = overlay.RunsDir
	}
	if overlay.RunLogsDir != "" {
		target.RunLogsDir = overlay.RunLogsDir
	}
	if overlay.FeedbackDir != "" {
		target.FeedbackDir = overlay.FeedbackDir
	}
}

func mergeStringMap(target *map[string]string, overlay map[string]string) {
	if len(overlay) == 0 {
		return
	}
	if *target == nil {
		*target = map[string]string{}
	}
	for key, value := range overlay {
		(*target)[key] = value
	}
}

func mergeJiraTransitionMap(target *map[string]profile.JiraTransition, overlay map[string]profile.JiraTransition) {
	if len(overlay) == 0 {
		return
	}
	if *target == nil {
		*target = map[string]profile.JiraTransition{}
	}
	for key, value := range overlay {
		(*target)[key] = value
	}
}

func mergeReviewGateMap(target *map[string]profile.ReviewGate, overlay map[string]profile.ReviewGate) {
	if len(overlay) == 0 {
		return
	}
	if *target == nil {
		*target = map[string]profile.ReviewGate{}
	}
	for key, value := range overlay {
		(*target)[key] = value
	}
}

func mergeRetryRedoMap(target *map[string]profile.RetryRedoPolicy, overlay map[string]profile.RetryRedoPolicy) {
	if len(overlay) == 0 {
		return
	}
	if *target == nil {
		*target = map[string]profile.RetryRedoPolicy{}
	}
	for key, value := range overlay {
		(*target)[key] = value
	}
}
