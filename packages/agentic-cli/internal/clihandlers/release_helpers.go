package clihandlers

import (
	"context"
	"fmt"
	"github.com/tapstate/agentic-ops/packages/agentic-cli/internal/feedback"
	"github.com/tapstate/agentic-ops/packages/agentic-cli/internal/jira"
	"github.com/tapstate/agentic-ops/packages/agentic-cli/internal/profile"
	"os"
	"path/filepath"
	"strings"
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
}

var selectJiraClient = defaultJiraClient

func defaultJiraClient(workspaceName string, workspaceProfile profile.Profile) (jiraClientSelection, error) {
	if os.Getenv("AGENTIC_OPS_JIRA_ADAPTER") == "real" {
		client, err := jira.NewRealClient(jira.RealClientConfig{
			BaseURL:  os.Getenv("AGENTIC_OPS_JIRA_BASE_URL"),
			Email:    os.Getenv("AGENTIC_OPS_JIRA_EMAIL"),
			APIToken: os.Getenv("AGENTIC_OPS_JIRA_API_TOKEN"),
			Profile:  workspaceProfile,
		})
		if err != nil {
			return jiraClientSelection{}, err
		}
		return jiraClientSelection{Client: client, Mode: "real"}, nil
	}
	return jiraClientSelection{Client: jira.FakeClient{}, Mode: "fake"}, nil
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
