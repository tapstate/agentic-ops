package clihandlers

import (
	"context"
	"fmt"
	"os"
	"path/filepath"
	"strings"

	"github.com/tapstate/agentic-ops/packages/agentic-cli/internal/config"
	"github.com/tapstate/agentic-ops/packages/agentic-cli/internal/feedback"
	"github.com/tapstate/agentic-ops/packages/agentic-cli/internal/jira"
	"github.com/tapstate/agentic-ops/packages/agentic-cli/internal/output"
	"github.com/tapstate/agentic-ops/packages/agentic-cli/internal/profile"
	"github.com/tapstate/agentic-ops/packages/agentic-cli/internal/runtimeconfig"
)

type completionEvidenceCheck struct {
	Target    string
	Reference string
	Submitted bool
}

func completionEvidenceStatus(root string, runID string, completionEvidence string) (completionEvidenceCheck, error) {
	if strings.TrimSpace(completionEvidence) == "" {
		return completionEvidenceCheck{}, fmt.Errorf("completion evidence is required")
	}
	if filepath.IsAbs(completionEvidence) {
		if fileExists(completionEvidence) {
			return completionEvidenceCheck{Target: "local_file", Reference: completionEvidence, Submitted: true}, nil
		}
		return completionEvidenceCheck{}, fmt.Errorf("completion evidence file not found: %s", completionEvidence)
	}
	runEvidencePath := filepath.Join(root, ".agentic-ops", "runs", runID, completionEvidence)
	if fileExists(runEvidencePath) {
		return completionEvidenceCheck{Target: "local_file", Reference: runEvidencePath, Submitted: true}, nil
	}
	events, err := feedback.ReadEvents(filepath.Join(root, ".agentic-ops", "feedback", "events.ndjson"))
	if err != nil {
		return completionEvidenceCheck{}, err
	}
	for i := len(events) - 1; i >= 0; i-- {
		event := events[i]
		if event.AgenticRunID == runID && event.AuditSubmitted && event.AuditReference == completionEvidence {
			return completionEvidenceCheck{Target: event.AuditTarget, Reference: event.AuditReference, Submitted: true}, nil
		}
	}
	return completionEvidenceCheck{}, fmt.Errorf("completion evidence not found: %s", completionEvidence)
}

func fileExists(path string) bool {
	stat, err := os.Stat(path)
	return err == nil && !stat.IsDir()
}

func resolveJiraTransitionID(ctx context.Context, client jira.Client, issueKey string, workspaceProfile profile.Profile, action string) (string, error) {
	transition, ok := workspaceProfile.JiraTransitionMapping[action]
	if !ok {
		return "", fmt.Errorf("jira transition mapping missing for %s", action)
	}
	if transition.ID != "" {
		return transition.ID, nil
	}
	if transition.Name == "" {
		return "", fmt.Errorf("jira transition id or name is required for %s", action)
	}
	transitions, err := client.Transitions(ctx, issueKey)
	if err != nil {
		return "", err
	}
	for _, candidate := range transitions {
		if candidate.Name == transition.Name {
			return candidate.ID, nil
		}
	}
	return "", fmt.Errorf("jira transition %q not found for %s", transition.Name, action)
}

func jiraTakeoverFailure(code string, message string, requiredHumanAction string, runID string, issueKey string, remoteWriteCompleted bool, readbackVerified bool, retrySafe bool) map[string]any {
	result := output.FailureWithContext("takeover_task", output.FailureContext{
		Code:                code,
		Message:             message,
		RequiredHumanAction: requiredHumanAction,
		TaskType:            "task_takeover",
		CurrentStage:        "takeover_gate",
		AgenticNextAction:   "ask_owner",
	})
	result["remote_write_completed"] = remoteWriteCompleted
	result["readback_verified"] = readbackVerified
	result["retry_safe"] = retrySafe
	result["agentic_run_id"] = runID
	result["issue_key"] = issueKey
	return result
}

type jiraClientSelection struct {
	Client jira.Client
	Mode   string
	Source string
}

var selectJiraClient = defaultJiraClient

func defaultJiraClient(workspaceName string, workspaceProfile profile.Profile) (jiraClientSelection, error) {
	runtimeConfig, err := resolveJiraRuntimeConfig(workspaceName)
	if err != nil {
		return jiraClientSelection{}, err
	}
	if runtimeConfig.Adapter == "real" {
		client, err := jira.NewRealClient(jira.RealClientConfig{
			BaseURL:  runtimeConfig.BaseURL,
			Email:    runtimeConfig.Email,
			APIToken: runtimeConfig.APIToken,
			Profile:  workspaceProfile,
		})
		if err != nil {
			return jiraClientSelection{}, err
		}
		return jiraClientSelection{Client: client, Mode: "real", Source: runtimeConfig.Source}, nil
	}
	return jiraClientSelection{Client: jira.FakeClient{}, Mode: "fake", Source: runtimeConfig.Source}, nil
}

type jiraRuntimeConfig struct {
	Adapter     string   `yaml:"adapter"`
	BaseURL     string   `yaml:"base_url"`
	Email       string   `yaml:"email"`
	APIToken    string   `yaml:"-"`
	APITokenEnv string   `yaml:"-"`
	EnvFile     string   `yaml:"-"`
	EnvFiles    []string `yaml:"-"`
	Source      string   `yaml:"-"`
}

const jiraAPITokenEnvName = "AGENTIC_OPS_JIRA_API_TOKEN"
const jiraTokenHelpURL = "https://id.atlassian.com/manage-profile/security/api-tokens"

func addJiraTokenGuidance(payload map[string]any, status string, tokenEnv string, envFile string) {
	if status != "needs_jira_api_token" {
		return
	}
	tokenEnv = strings.TrimSpace(tokenEnv)
	if tokenEnv == "" {
		tokenEnv = jiraAPITokenEnvName
	}
	if strings.TrimSpace(envFile) != "" {
		payload["jira_env_file"] = envFile
	}
	payload["jira_token_env_has_value"] = jiraTokenConfigured(tokenEnv, envFile)
	payload["jira_token_help_url"] = jiraTokenHelpURL
	if strings.TrimSpace(envFile) != "" {
		payload["jira_token_setup"] = "edit " + envFile + " and set " + tokenEnv + "=<api-token>"
	} else {
		payload["jira_token_setup"] = "read -s " + tokenEnv + " && export " + tokenEnv
	}
}

func addJiraTokenDiagnostics(payload map[string]any, runtimeConfig jiraRuntimeConfig) {
	tokenEnv := jiraTokenEnvName(runtimeConfig)
	payload["jira_token_env"] = tokenEnv
	payload["jira_token_env_has_value"] = jiraTokenConfiguredInFiles(tokenEnv, runtimeConfig.EnvFiles)
	if strings.TrimSpace(runtimeConfig.EnvFile) != "" {
		payload["jira_env_file"] = runtimeConfig.EnvFile
	}
	if strings.TrimSpace(runtimeConfig.Source) != "" {
		payload["jira_config_source"] = runtimeConfig.Source
	}
	addJiraTokenGuidance(payload, "needs_jira_api_token", tokenEnv, runtimeConfig.EnvFile)
}

func jiraTokenEnvName(runtimeConfig jiraRuntimeConfig) string {
	return jiraAPITokenEnvName
}

func jiraTokenEnvHasValue(tokenEnv string) bool {
	return strings.TrimSpace(os.Getenv(strings.TrimSpace(tokenEnv))) != ""
}

func jiraTokenConfigured(tokenEnv string, envFile string) bool {
	return jiraTokenConfiguredInFiles(tokenEnv, []string{envFile})
}

func jiraTokenConfiguredInFiles(tokenEnv string, envFiles []string) bool {
	tokenEnv = strings.TrimSpace(tokenEnv)
	if tokenEnv == "" {
		return false
	}
	value, ok, err := lookupTokenEnv(tokenEnv, envFiles)
	return err == nil && ok && strings.TrimSpace(value) != ""
}

func jiraAdapterConfigFailure(operation string, workspaceName string, err error, requiredHumanAction string) map[string]any {
	result := output.Failure(operation, "jira_adapter_config_failed", err.Error(), requiredHumanAction)
	if err == nil || !strings.Contains(err.Error(), "jira API token is required") {
		return result
	}
	runtimeConfig, configErr := resolveJiraRuntimeConfig(workspaceName)
	if configErr != nil {
		runtimeConfig = jiraRuntimeConfig{}
	}
	addJiraTokenDiagnostics(result, runtimeConfig)
	tokenEnv := jiraTokenEnvName(runtimeConfig)
	if runtimeConfig.EnvFile != "" {
		result["required_human_action"] = "请到 Atlassian 创建 Jira API token，然后写入 " + runtimeConfig.EnvFile + " 中的 " + tokenEnv
	} else {
		result["required_human_action"] = "请到 Atlassian 创建 Jira API token，然后设置进程环境变量 " + tokenEnv
	}
	result["agentic_next_action"] = "set_jira_api_token"
	return result
}

func resolveJiraRuntimeConfig(workspaceName string) (jiraRuntimeConfig, error) {
	if adapter := strings.TrimSpace(os.Getenv("AGENTIC_OPS_JIRA_ADAPTER")); adapter != "" {
		return jiraRuntimeConfig{
			Adapter:  strings.ToLower(adapter),
			BaseURL:  jira.NormalizeBaseURL(os.Getenv("AGENTIC_OPS_JIRA_BASE_URL")),
			Email:    os.Getenv("AGENTIC_OPS_JIRA_EMAIL"),
			APIToken: os.Getenv("AGENTIC_OPS_JIRA_API_TOKEN"),
			Source:   "environment",
		}, nil
	}
	scope := runtimeConfigScope(workspaceName)
	var config jiraRuntimeConfig
	source, used, err := runtimeconfig.ResolveProjectModule(scope, "jira", &config)
	if err != nil {
		return jiraRuntimeConfig{}, err
	}
	if used {
		return finalizeJiraRuntimeConfig(scope, source, config)
	}
	return jiraRuntimeConfig{Adapter: "fake", Source: "default"}, nil
}

func loadJiraRuntimeConfig(path string) (jiraRuntimeConfig, bool, error) {
	var config jiraRuntimeConfig
	used, err := runtimeconfig.ReadProjectModule(path, "", "jira", &config)
	if err != nil || !used {
		return jiraRuntimeConfig{}, used, err
	}
	finalized, err := finalizeJiraRuntimeConfig(runtimeConfigScope(""), path, config)
	return finalized, true, err
}

func finalizeJiraRuntimeConfig(scope runtimeconfig.Scope, source string, config jiraRuntimeConfig) (jiraRuntimeConfig, error) {
	config.Adapter = strings.ToLower(strings.TrimSpace(config.Adapter))
	config.BaseURL = jira.NormalizeBaseURL(config.BaseURL)
	config.Email = strings.TrimSpace(config.Email)
	if config.Adapter == "" && config.BaseURL == "" && config.Email == "" && config.APIToken == "" {
		return jiraRuntimeConfig{}, nil
	}
	config.APITokenEnv = jiraAPITokenEnvName
	config.EnvFile = scope.UserEnvPath()
	config.EnvFiles = jiraRuntimeEnvFiles(scope)
	if config.APIToken == "" && config.APITokenEnv != "" {
		value, ok, err := lookupTokenEnv(config.APITokenEnv, config.EnvFiles)
		if err != nil {
			return jiraRuntimeConfig{}, err
		}
		if ok {
			config.APIToken = value
		}
	}
	if config.Adapter == "" && (config.BaseURL != "" || config.Email != "" || config.APIToken != "" || config.APITokenEnv != "") {
		config.Adapter = "real"
	}
	config.Source = source
	return config, nil
}

func lookupTokenEnv(tokenEnv string, envFiles []string) (string, bool, error) {
	if value := strings.TrimSpace(os.Getenv(strings.TrimSpace(tokenEnv))); value != "" {
		return value, true, nil
	}
	if value, ok, err := runtimeconfig.LookupEnvFiles(envFiles, tokenEnv); err != nil || ok {
		return value, ok, err
	}
	return "", false, nil
}

func jiraRuntimeEnvFiles(scope runtimeconfig.Scope) []string {
	if strings.TrimSpace(scope.UserEnvPath()) == "" {
		return nil
	}
	return []string{scope.UserEnvPath()}
}

func runtimeConfigScope(workspaceName string) runtimeconfig.Scope {
	if root, err := workspaceRoot(); err == nil && root != "" {
		return runtimeconfig.NewScope(agenticOpsInstallDir(), root, workspaceName)
	}
	return runtimeconfig.NewScope(agenticOpsInstallDir(), "", workspaceName)
}

func agenticOpsInstallDir() string {
	if installDir := os.Getenv("AGENTIC_OPS_HOME"); installDir != "" {
		return installDir
	}
	home, err := os.UserHomeDir()
	if err != nil {
		return ".agentic-ops"
	}
	return config.DefaultInstallDir(home)
}

func jiraTakeoverFields(workspaceProfile profile.Profile, runID string, currentAgentID string, takeoverAt string, nextAction string) map[string]any {
	fields := map[string]any{}
	values := map[string]any{
		"agentic_id":                  currentAgentID,
		"agentic_run_id":              runID,
		"agentic_takeover_at":         takeoverAt,
		"agentic_next_action":         nextAction,
		"agentic_completion_evidence": nil,
		"agentic_heartbeat_at":        takeoverAt,
	}
	for name, value := range values {
		if field := workspaceProfile.JiraFormMapping.Fields[name].JiraField; field != "" {
			fields[field] = value
		}
	}
	return fields
}

func jiraTakeoverComment(workspaceProfile profile.Profile, runID string, currentAgentID string, takeoverAt string, nextAction string) string {
	if !usesJiraCommentOwnership(workspaceProfile) {
		return ""
	}
	return strings.Join([]string{
		"AgenticOps ownership",
		"agentic_run_id: " + runID,
		"agentic_id: " + currentAgentID,
		"agentic_takeover_at: " + takeoverAt,
		"agentic_next_action: " + nextAction,
		"agentic_completion_evidence: ",
		"agentic_heartbeat_at: " + takeoverAt,
	}, "\n")
}

func jiraReleaseFields(workspaceProfile profile.Profile) map[string]any {
	fields := map[string]any{}
	if field := workspaceProfile.JiraFormMapping.Fields["agentic_id"].JiraField; field != "" {
		fields[field] = nil
	}
	return fields
}

func jiraReleaseComment(workspaceProfile profile.Profile, runID string, completedAt string) string {
	if !usesJiraCommentOwnership(workspaceProfile) {
		return ""
	}
	return strings.Join([]string{
		"AgenticOps ownership",
		"agentic_run_id: " + runID,
		"agentic_id: ",
		"released_at: " + completedAt,
	}, "\n")
}

func usesJiraCommentOwnership(workspaceProfile profile.Profile) bool {
	for _, name := range []string{"agentic_id", "agentic_run_id", "agentic_takeover_at", "agentic_next_action", "agentic_completion_evidence", "agentic_heartbeat_at"} {
		if field := workspaceProfile.JiraFormMapping.Fields[name]; field.Source == "jira_comment" {
			return true
		}
	}
	return false
}
