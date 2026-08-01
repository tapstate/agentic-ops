package clihandlers

import (
	"github.com/tapstate/agentic-ops/packages/agentic-cli/internal/feedback"
	"os"
	"path/filepath"
	"time"
)

func appendWorkspaceEvent(workspaceName string, runID string, issueKey string, taskType string, operation string, currentStage string, nextAction string, ok bool, requiresHumanAction bool) error {
	return appendWorkspaceEventWithCode(workspaceName, runID, issueKey, taskType, operation, currentStage, nextAction, "", operation, ok, requiresHumanAction)
}

func appendWorkspaceEventWithCode(workspaceName string, runID string, issueKey string, taskType string, operation string, currentStage string, nextAction string, code string, gate string, ok bool, requiresHumanAction bool) error {
	return appendWorkspaceEventWithDetails(workspaceName, feedback.Event{
		AgenticRunID:        runID,
		IssueKey:            issueKey,
		TaskType:            taskType,
		Operation:           operation,
		CurrentStage:        currentStage,
		AgenticNextAction:   nextAction,
		OK:                  ok,
		Code:                code,
		Gate:                gate,
		GateStatus:          gateStatus(ok, requiresHumanAction),
		HumanGate:           requiresHumanAction,
		RequiresHumanAction: requiresHumanAction,
	})
}

func appendWorkspaceEventWithDetails(workspaceName string, event feedback.Event) error {
	root, err := workspaceRoot()
	if err != nil {
		return err
	}
	event.Timestamp = fixedNow().Format(time.RFC3339)
	event.Workspace = workspaceName
	event.AgentTaskOpsVersion = Version
	event.VersionState = VersionState
	event.AssetVersion = readAssetVersion()
	return feedback.AppendEvent(filepath.Join(root, ".agentic-ops", "feedback", "events.ndjson"), event)
}

func appendRealJiraWriteGateEvent(workspaceName string, runID string, issueKey string, operation string, currentStage string, nextAction string, code string, ok bool, requiresHumanAction bool) error {
	taskType := "task_takeover"
	if operation == "write_evidence" || operation == "write_pr_evidence" {
		taskType = "evidence_write"
	}
	return appendWorkspaceEventWithDetails(workspaceName, feedback.Event{
		AgenticRunID:        runID,
		IssueKey:            issueKey,
		TaskType:            taskType,
		Operation:           operation,
		CurrentStage:        currentStage,
		AgenticNextAction:   nextAction,
		AgentID:             agentID(),
		AgenticID:           agentID(),
		OK:                  ok,
		Code:                code,
		Gate:                "real_jira_write",
		GateStatus:          gateStatus(ok, requiresHumanAction),
		HumanGate:           true,
		RequiresHumanAction: requiresHumanAction,
	})
}

func issueKeyForRun(root string, runID string) (string, error) {
	events, err := feedback.ReadEvents(filepath.Join(root, ".agentic-ops", "feedback", "events.ndjson"))
	if err != nil {
		return "", err
	}
	for _, event := range events {
		if event.AgenticRunID == runID && event.IssueKey != "" {
			return event.IssueKey, nil
		}
	}
	return "", nil
}

func readAssetVersion() string {
	if version := os.Getenv("AGENTIC_OPS_ASSET_VERSION"); version != "" {
		return version
	}
	return "unknown"
}

func gateStatus(ok bool, requiresHumanAction bool) string {
	if ok {
		return "passed"
	}
	if requiresHumanAction {
		return "blocked"
	}
	return "failed"
}
