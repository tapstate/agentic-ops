package clihandlers

import (
	"errors"
	"fmt"
	"github.com/tapstate/agentic-ops/packages/agentic-cli/internal/evidence"
	"github.com/tapstate/agentic-ops/packages/agentic-cli/internal/policy"
	"github.com/tapstate/agentic-ops/packages/agentic-cli/internal/profile"
	"github.com/tapstate/agentic-ops/packages/agentic-cli/internal/runcontext"
	"os"
	"path/filepath"
	"strings"
)

type evidenceRunContext struct {
	IssueKey      string
	AgentID       string
	AgenticID     string
	TaskClass     string
	ProcessID     string
	CurrentStage  string
	PreviousStage string
	TargetRepo    string
}

func evidenceRunState(root string, workspaceName string, runID string) (evidenceRunContext, error) {
	context, err := runcontext.ReadFile(
		filepath.Join(root, ".agentic-ops", "feedback", "events.ndjson"),
		runcontext.Query{AgenticRunID: runID, Workspace: workspaceName, AgentID: agentID()},
	)
	if err != nil {
		return evidenceRunContext{}, err
	}
	if context.Terminal {
		return evidenceRunContext{}, runcontext.ErrLocalStateMismatch
	}
	return evidenceRunContext{
		IssueKey:      context.IssueKey,
		AgentID:       context.AgentID,
		AgenticID:     context.AgenticID,
		TaskClass:     context.TaskClass,
		ProcessID:     context.ProcessID,
		CurrentStage:  context.CurrentStage,
		PreviousStage: context.CurrentStage,
		TargetRepo:    context.TargetRepo,
	}, nil
}

func evidenceStateErrorCode(err error) string {
	return runcontext.ErrorCode(err)
}

func evidenceContentErrorCode(err error) string {
	switch {
	case errors.Is(err, evidence.ErrOutsideWorkspace):
		return "evidence_content_outside_workspace"
	case errors.Is(err, evidence.ErrEvidenceTooLarge):
		return "evidence_content_too_large"
	case errors.Is(err, evidence.ErrInvalidEvidenceSections):
		return "invalid_evidence_sections"
	default:
		return "evidence_content_read_failed"
	}
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
