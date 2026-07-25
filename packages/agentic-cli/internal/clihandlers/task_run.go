package clihandlers

import (
	"context"
	"io"

	"github.com/tapstate/agentic-ops/packages/agentic-cli/internal/feedback"
	"github.com/tapstate/agentic-ops/packages/agentic-cli/internal/output"
	"github.com/tapstate/agentic-ops/packages/agentic-cli/internal/tasklifecycle"
)

func runTaskRun(args []string, stdout io.Writer) int {
	if len(args) < 3 {
		return writeJSON(stdout, output.Failure("task_run", "missing_issue_key", "缺少 Jira 卡片编号", "请提供 Jira 卡片编号"))
	}
	workspaceName := workspaceNameFromArgsOrAgentConfig(args, "default")
	workspaceProfile := takeoverProfile(workspaceName)
	selection, err := selectJiraClient(workspaceName, workspaceProfile)
	if err != nil {
		return writeJSON(stdout, output.Failure("task_run", "jira_adapter_config_failed", err.Error(), "请检查 Jira 适配器配置"))
	}
	processRegistry, err := repoProcessRegistry()
	if err != nil {
		processRegistry = defaultProcessRegistry()
	}
	result := tasklifecycle.TaskLifecycleRunner{
		Client:               selection.Client,
		Mode:                 selection.Mode,
		Profile:              workspaceProfile,
		ProcessRegistry:      processRegistry,
		AgentID:              agentID(),
		Now:                  fixedNow(),
		ConfirmRealJiraWrite: hasFlag(args, "--confirm-real-jira-write"),
		AppendEvent: func(event feedback.Event) error {
			return appendWorkspaceEventWithDetails(workspaceName, event)
		},
		TakeoverFields: jiraTakeoverFields,
		ReleaseFields:  jiraReleaseFields,
	}.Run(context.Background(), tasklifecycle.Request{
		Workspace: workspaceName,
		IssueKey:  args[2],
		Process:   readFlag(args, "--process", ""),
	})
	if !result.OK {
		return writeJSON(stdout, output.FailureWithContext("task_run", output.FailureContext{
			Code:                result.Code,
			Message:             result.Message,
			RequiredHumanAction: result.RequiredHumanAction,
			TaskType:            result.TaskType,
			CurrentStage:        result.CurrentStage,
			NextAction:          result.NextAction,
		}))
	}
	return writeJSON(stdout, output.Success("task_run", taskRunOutput(result)))
}

func taskRunOutput(result tasklifecycle.Result) map[string]any {
	payload := map[string]any{
		"workspace":                result.Workspace,
		"issue_key":                result.IssueKey,
		"run_id":                   result.RunID,
		"agent_id":                 result.AgentID,
		"current_agent_id":         result.CurrentAgentID,
		"takeover_at":              result.TakeoverAt,
		"task_type":                result.TaskType,
		"task_class":               result.TaskClass,
		"task_class_source":        result.TaskClassSource,
		"process_id":               result.ProcessID,
		"capability_id":            result.CapabilityID,
		"current_stage":            result.CurrentStage,
		"target_repo":              result.TargetRepo,
		"next_action":              result.NextAction,
		"current_agent_id_cleared": result.CurrentAgentIDCleared,
		"audit_submitted":          result.AuditSubmitted,
	}
	if result.DefectComplexity != "" {
		payload["defect_complexity"] = result.DefectComplexity
	}
	if result.AuditTarget != "" {
		payload["audit_target"] = result.AuditTarget
	}
	if result.AuditReference != "" {
		payload["audit_reference"] = result.AuditReference
	}
	return payload
}
