package clihandlers

import (
	"context"
	"fmt"
	"github.com/tapstate/agentic-ops/packages/agentic-cli/internal/config"
	"github.com/tapstate/agentic-ops/packages/agentic-cli/internal/feedback"
	"github.com/tapstate/agentic-ops/packages/agentic-cli/internal/jira"
	"github.com/tapstate/agentic-ops/packages/agentic-cli/internal/profile"
	"os"
	"path/filepath"
	"strings"

	"gopkg.in/yaml.v3"
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
		if event.RunID == runID && event.AuditSubmitted && event.AuditReference == completionEvidence {
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
	Adapter     string `yaml:"adapter"`
	BaseURL     string `yaml:"base_url"`
	Email       string `yaml:"email"`
	APIToken    string `yaml:"api_token"`
	APITokenEnv string `yaml:"api_token_env"`
	Source      string `yaml:"-"`
}

func resolveJiraRuntimeConfig(workspaceName string) (jiraRuntimeConfig, error) {
	if adapter := strings.TrimSpace(os.Getenv("AGENTIC_OPS_JIRA_ADAPTER")); adapter != "" {
		return jiraRuntimeConfig{
			Adapter:  strings.ToLower(adapter),
			BaseURL:  os.Getenv("AGENTIC_OPS_JIRA_BASE_URL"),
			Email:    os.Getenv("AGENTIC_OPS_JIRA_EMAIL"),
			APIToken: os.Getenv("AGENTIC_OPS_JIRA_API_TOKEN"),
			Source:   "environment",
		}, nil
	}
	for _, path := range jiraRuntimeConfigPaths(workspaceName) {
		config, used, err := loadJiraRuntimeConfig(path)
		if err != nil {
			return jiraRuntimeConfig{}, err
		}
		if used {
			return config, nil
		}
	}
	return jiraRuntimeConfig{Adapter: "fake", Source: "default"}, nil
}

func loadJiraRuntimeConfig(path string) (jiraRuntimeConfig, bool, error) {
	stat, err := os.Stat(path)
	if err != nil {
		if os.IsNotExist(err) {
			return jiraRuntimeConfig{}, false, nil
		}
		return jiraRuntimeConfig{}, false, err
	}
	if stat.IsDir() {
		return jiraRuntimeConfig{}, false, nil
	}
	data, err := os.ReadFile(path)
	if err != nil {
		return jiraRuntimeConfig{}, false, err
	}
	var config jiraRuntimeConfig
	if err := yaml.Unmarshal(data, &config); err != nil {
		return jiraRuntimeConfig{}, false, fmt.Errorf("load jira config %s: %w", path, err)
	}
	config.Adapter = strings.ToLower(strings.TrimSpace(config.Adapter))
	config.BaseURL = strings.TrimSpace(config.BaseURL)
	config.Email = strings.TrimSpace(config.Email)
	config.APIToken = strings.TrimSpace(config.APIToken)
	config.APITokenEnv = strings.TrimSpace(config.APITokenEnv)
	if config.Adapter == "" && config.BaseURL == "" && config.Email == "" && config.APIToken == "" && config.APITokenEnv == "" {
		return jiraRuntimeConfig{}, false, nil
	}
	if config.APIToken == "" && config.APITokenEnv != "" {
		config.APIToken = os.Getenv(config.APITokenEnv)
	}
	if config.Adapter == "" && (config.BaseURL != "" || config.Email != "" || config.APIToken != "" || config.APITokenEnv != "") {
		config.Adapter = "real"
	}
	config.Source = path
	return config, true, nil
}

func jiraRuntimeConfigPaths(workspaceName string) []string {
	paths := []string{}
	if root, err := workspaceRoot(); err == nil && root != "" {
		paths = append(paths, filepath.Join(root, ".agentic-ops", "jira.local.yaml"))
	}
	installDir := agenticOpsInstallDir()
	if workspaceName != "" {
		paths = append(paths, filepath.Join(installDir, "user", "projects", workspaceName, "jira.local.yaml"))
	}
	paths = append(paths, filepath.Join(installDir, "user", "jira.local.yaml"))
	return paths
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

func jiraTakeoverFields(workspaceProfile profile.Profile, currentAgentID string, takeoverAt string) map[string]any {
	fields := map[string]any{}
	if field := workspaceProfile.JiraFormMapping.Fields["current_agent_id"].JiraField; field != "" {
		fields[field] = currentAgentID
	}
	if field := workspaceProfile.JiraFormMapping.Fields["takeover_at"].JiraField; field != "" {
		fields[field] = takeoverAt
	}
	return fields
}

func jiraReleaseFields(workspaceProfile profile.Profile) map[string]any {
	fields := map[string]any{}
	if field := workspaceProfile.JiraFormMapping.Fields["current_agent_id"].JiraField; field != "" {
		fields[field] = nil
	}
	return fields
}
