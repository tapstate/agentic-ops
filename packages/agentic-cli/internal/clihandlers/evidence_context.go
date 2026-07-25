package clihandlers

import (
	"fmt"
	"github.com/tapstate/agentic-ops/packages/agentic-cli/internal/feedback"
	"github.com/tapstate/agentic-ops/packages/agentic-cli/internal/policy"
	"github.com/tapstate/agentic-ops/packages/agentic-cli/internal/profile"
	"os"
	"path/filepath"
	"strings"
)

type evidenceRunContext struct {
	IssueKey       string
	AgentID        string
	CurrentAgentID string
	TaskClass      string
	ProcessID      string
	PreviousStage  string
	TargetRepo     string
}

func evidenceRunState(root string, workspaceName string, runID string) (evidenceRunContext, error) {
	events, err := feedback.ReadEvents(filepath.Join(root, ".agentic-ops", "feedback", "events.ndjson"))
	if err != nil {
		return evidenceRunContext{}, err
	}
	var latest *feedback.Event
	targetRepo := ""
	for i := range events {
		if events[i].RunID == runID && (events[i].Operation == "takeover_task" || events[i].Operation == "resume_takeover") {
			latest = &events[i]
			if events[i].TargetRepo != "" {
				targetRepo = events[i].TargetRepo
			}
		}
	}
	if latest == nil {
		return evidenceRunContext{}, errResumeRunNotFound
	}
	if latest.Workspace != workspaceName {
		return evidenceRunContext{}, errResumeWorkspaceMismatch
	}
	if latest.IssueKey == "" ||
		latest.AgentID == "" ||
		latest.CurrentAgentID == "" ||
		latest.CurrentAgentID != latest.AgentID ||
		latest.CurrentAgentID != agentID() ||
		latest.TaskClass == "" ||
		latest.ProcessID == "" ||
		latest.CurrentStage == "" ||
		!latest.OK ||
		latest.CurrentStage == "completed" ||
		latest.NextAction == "task_audit_submitted" {
		return evidenceRunContext{}, errResumeLocalStateMismatch
	}
	return evidenceRunContext{
		IssueKey:       latest.IssueKey,
		AgentID:        latest.AgentID,
		CurrentAgentID: latest.CurrentAgentID,
		TaskClass:      latest.TaskClass,
		ProcessID:      latest.ProcessID,
		PreviousStage:  latest.CurrentStage,
		TargetRepo:     targetRepo,
	}, nil
}

func evidenceStateErrorCode(err error) string {
	return resumeErrorCode(err)
}

func evidenceTemplate(workspaceProfile profile.Profile) (string, string, error) {
	templateName := workspaceProfile.Templates["development_completed"]
	if strings.TrimSpace(templateName) == "" {
		return "", "", fmt.Errorf("development_completed evidence template is required")
	}
	root, err := repoRoot()
	if err != nil {
		return "", "", err
	}
	templatePath := filepath.Join(repoBasicResourcesPath(root), templateName)
	if strings.HasPrefix(templateName, "install-resources/basic/") {
		templatePath = filepath.Join(root, templateName)
	}
	data, err := os.ReadFile(templatePath)
	if err != nil {
		return "", "", err
	}
	return templatePath, string(data), nil
}

func evidencePolicyGateName(jiraMode string) string {
	if jiraMode == "real" {
		return "write_jira_comment"
	}
	return "write_local_evidence"
}

func policyRequiresHumanGate(gateName string) (string, bool, error) {
	policyPath, err := repoPolicyPath()
	if err != nil {
		return "", false, err
	}
	loadedPolicy, err := policy.LoadFile(policyPath)
	if err != nil {
		return policyPath, false, err
	}
	if issues := policy.Validate(loadedPolicy); len(issues) > 0 {
		return policyPath, false, fmt.Errorf("policy validation failed: %s", issues[0].Code)
	}
	return policyPath, policy.RequiresHumanGate(loadedPolicy, gateName), nil
}

func renderEvidenceTemplate(template string, values map[string]string) string {
	rendered := template
	for key, value := range values {
		rendered = strings.ReplaceAll(rendered, "<"+key+">", value)
	}
	return rendered
}
