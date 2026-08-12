package clihandlers

import (
	"context"
	"github.com/tapstate/agentic-ops/packages/agentic-cli/internal/feedback"
	"github.com/tapstate/agentic-ops/packages/agentic-cli/internal/jira"
	"github.com/tapstate/agentic-ops/packages/agentic-cli/internal/output"
	"io"
	"time"
)

func runReleaseAgent(args []string, stdout io.Writer) int {
	workspaceName := workspaceNameFromArgsOrAgentConfig(args, "default")
	runID := readFlag(args, "--run-id", "")
	if runID == "" {
		return writeJSON(stdout, output.FailureWithContext("release_agent", output.FailureContext{
			Code:                "missing_agentic_run_id",
			Message:             "缺少 agentic_run_id",
			RequiredHumanAction: "请提供 --run-id",
			TaskType:            "task_takeover",
			CurrentStage:        "completion_cleanup",
			AgenticNextAction:   "ask_owner",
		}))
	}
	issueKey := readFlag(args, "--issue-key", "")
	jiraTransitionID := readFlag(args, "--jira-transition-id", "")
	if issueKey == "" {
		return writeJSON(stdout, output.FailureWithContext("release_agent", output.FailureContext{
			Code:                "missing_issue_key",
			Message:             "缺少 Jira 卡片编号",
			RequiredHumanAction: "请提供 --issue-key",
			TaskType:            "task_takeover",
			CurrentStage:        "completion_cleanup",
			AgenticNextAction:   "ask_owner",
		}))
	}
	completionEvidence := readFlag(args, "--completion-evidence", "")
	if completionEvidence == "" {
		return writeJSON(stdout, output.FailureWithContext("release_agent", output.FailureContext{
			Code:                "missing_agentic_completion_evidence",
			Message:             "缺少完成证据",
			RequiredHumanAction: "请提供 --completion-evidence",
			TaskType:            "task_takeover",
			CurrentStage:        "completion_cleanup",
			AgenticNextAction:   "ask_owner",
		}))
	}
	currentAgentID := agentID()
	completedAt := currentClock.Now().UTC().Format(time.RFC3339)
	workspaceProfile := takeoverProfile(workspaceName)
	selection, err := selectJiraClient(workspaceName, workspaceProfile)
	if err != nil {
		return writeJSON(stdout, output.Failure("release_agent", "jira_adapter_config_failed", err.Error(), "请检查 Jira 适配器配置"))
	}
	auditTarget := "jira_comment"
	auditReference := completionEvidence
	auditSubmitted := true
	if selection.Mode != "real" {
		root, err := workspaceRoot()
		if err != nil {
			return writeJSON(stdout, output.Failure("release_agent", "workspace_root_failed", "无法读取当前工作目录", "请在项目 AI 工作空间中重试"))
		}
		runContext, err := evidenceRunState(root, workspaceName, runID)
		if err != nil {
			return writeJSON(stdout, output.FailureWithContext("release_agent", output.FailureContext{
				Code:                "run_not_found",
				Message:             err.Error(),
				RequiredHumanAction: "请检查 agentic_run_id 是否存在有效接管事件，且仍属于当前 AIAgent",
				TaskType:            "task_takeover",
				CurrentStage:        "completion_cleanup",
				AgenticNextAction:   "ask_owner",
			}))
		}
		if runContext.IssueKey != issueKey {
			return writeJSON(stdout, output.FailureWithContext("release_agent", output.FailureContext{
				Code:                "run_not_found",
				Message:             "agentic_run_id 与 issue_key 不匹配",
				RequiredHumanAction: "请检查 agentic_run_id 和 Jira 卡片编号是否来自同一次接管",
				TaskType:            "task_takeover",
				CurrentStage:        "completion_cleanup",
				AgenticNextAction:   "ask_owner",
			}))
		}
		status, err := completionEvidenceStatus(root, runID, completionEvidence)
		if err != nil {
			return writeJSON(stdout, output.FailureWithContext("release_agent", output.FailureContext{
				Code:                "agentic_completion_evidence_missing",
				Message:             err.Error(),
				RequiredHumanAction: "请先执行 write-evidence，或提供已提交任务级审计记录的引用",
				TaskType:            "task_takeover",
				CurrentStage:        "completion_cleanup",
				AgenticNextAction:   "ask_owner",
			}))
		}
		auditTarget = status.Target
		auditReference = status.Reference
		auditSubmitted = status.Submitted
	}
	if selection.Mode == "real" {
		if !hasFlag(args, "--confirm-real-jira-write") {
			_ = appendRealJiraWriteGateEvent(workspaceName, runID, issueKey, "release_agent", "completion_cleanup", "ask_owner", "real_jira_confirmation_required", false, true)
			return writeJSON(stdout, output.FailureWithContext("release_agent", output.FailureContext{
				Code:                "real_jira_confirmation_required",
				Message:             "真实 Jira 写入需要显式确认",
				RequiredHumanAction: "请确认完成证据、策略和门禁后添加 --confirm-real-jira-write",
				TaskType:            "task_takeover",
				CurrentStage:        "completion_cleanup",
				AgenticNextAction:   "ask_owner",
			}))
		}
		issue, ok, err := selection.Client.GetIssueByKey(context.Background(), workspaceName, issueKey)
		if err != nil {
			return writeJSON(stdout, output.Failure("release_agent", "jira_issue_read_failed", err.Error(), "请检查 Jira 适配器配置和卡片权限"))
		}
		if !ok {
			return writeJSON(stdout, output.Failure("release_agent", "issue_not_found", "未找到 Jira 卡片", "请检查 Jira 卡片编号"))
		}
		currentJiraUser, err := selection.Client.CurrentUser(context.Background())
		if err != nil {
			return writeJSON(stdout, output.Failure("release_agent", "jira_current_user_failed", err.Error(), "请检查 Jira 适配器登录状态"))
		}
		if issue.Assignee != currentJiraUser {
			return writeJSON(stdout, output.FailureWithContext("release_agent", output.FailureContext{
				Code:                "assignee_changed",
				Message:             "当前 Jira assignee 已不是当前用户",
				RequiredHumanAction: "请研发工程师确认是否继续释放代理绑定",
				TaskType:            "task_takeover",
				CurrentStage:        "completion_cleanup",
				AgenticNextAction:   "ask_owner",
			}))
		}
		if issue.AgenticID != currentAgentID {
			return writeJSON(stdout, output.FailureWithContext("release_agent", output.FailureContext{
				Code:                "agent_ownership_conflict",
				Message:             "当前 Jira 卡片未绑定当前 AIAgent",
				RequiredHumanAction: "请研发工程师确认是否释放当前代理绑定",
				TaskType:            "task_takeover",
				CurrentStage:        "completion_cleanup",
				AgenticNextAction:   "ask_owner",
			}))
		}
		if jiraTransitionID == "" {
			resolvedTransitionID, err := resolveJiraTransitionID(context.Background(), selection.Client, issueKey, workspaceProfile, "complete")
			if err != nil {
				_ = appendRealJiraWriteGateEvent(workspaceName, runID, issueKey, "release_agent", "jira_transition", "ask_owner", "jira_transition_mapping_gap", false, true)
				return writeJSON(stdout, output.FailureWithContext("release_agent", output.FailureContext{
					Code:                "jira_transition_mapping_gap",
					Message:             err.Error(),
					RequiredHumanAction: "请维护 workflow profile 的 jira_transition_mapping，或显式提供 --jira-transition-id",
					TaskType:            "task_takeover",
					CurrentStage:        "jira_transition",
					AgenticNextAction:   "ask_owner",
				}))
			}
			jiraTransitionID = resolvedTransitionID
		}
		fields := jiraReleaseFields(workspaceProfile)
		releaseComment := jiraReleaseComment(workspaceProfile, runID, completedAt)
		if len(fields) == 0 && releaseComment == "" {
			_ = appendRealJiraWriteGateEvent(workspaceName, runID, issueKey, "release_agent", "completion_cleanup", "ask_owner", "missing_jira_write_mapping", false, true)
			return writeJSON(stdout, output.Failure("release_agent", "missing_jira_write_mapping", "缺少 agentic_id 字段映射", "请维护 workflow profile 的所有权字段映射"))
		}
		if len(fields) > 0 {
			if err := selection.Client.UpdateFields(context.Background(), issueKey, fields); err != nil {
				_ = appendRealJiraWriteGateEvent(workspaceName, runID, issueKey, "release_agent", "completion_cleanup", "ask_owner", "agent_release_failed", false, false)
				return writeJSON(stdout, output.Failure("release_agent", "agent_release_failed", err.Error(), "请检查 Jira 字段权限并由研发工程师决策是否人工释放"))
			}
		}
		if releaseComment != "" {
			if err := selection.Client.AddComment(context.Background(), issueKey, releaseComment); err != nil {
				_ = appendRealJiraWriteGateEvent(workspaceName, runID, issueKey, "release_agent", "completion_cleanup", "ask_owner", "agent_release_failed", false, false)
				return writeJSON(stdout, output.Failure("release_agent", "agent_release_failed", err.Error(), "请检查 Jira 评论权限并由研发工程师决策是否人工释放"))
			}
		}
		if jiraTransitionID != "" {
			if err := selection.Client.TransitionIssue(context.Background(), issueKey, jira.TransitionRequest{ID: jiraTransitionID}); err != nil {
				_ = appendRealJiraWriteGateEvent(workspaceName, runID, issueKey, "release_agent", "jira_transition", "ask_owner", "jira_transition_failed", false, false)
				return writeJSON(stdout, output.FailureWithContext("release_agent", output.FailureContext{
					Code:                "jira_transition_failed",
					Message:             err.Error(),
					RequiredHumanAction: "请检查 Jira transition 权限、transition id 和 workflow profile 映射",
					TaskType:            "task_takeover",
					CurrentStage:        "jira_transition",
					AgenticNextAction:   "ask_owner",
				}))
			}
			if err := appendRealJiraWriteGateEvent(workspaceName, runID, issueKey, "release_agent", "jira_transition", "completion_cleanup", "", true, false); err != nil {
				return writeJSON(stdout, output.Failure("release_agent", "event_write_failed", err.Error(), "请检查工作空间目录权限"))
			}
		}
		if err := appendRealJiraWriteGateEvent(workspaceName, runID, issueKey, "release_agent", "completed", "task_audit_submitted", "", true, false); err != nil {
			return writeJSON(stdout, output.Failure("release_agent", "event_write_failed", err.Error(), "请检查工作空间目录权限"))
		}
	}
	if err := appendWorkspaceEventWithDetails(workspaceName, feedback.Event{
		Timestamp:                 completedAt,
		AgenticRunID:              runID,
		IssueKey:                  issueKey,
		TaskType:                  "task_takeover",
		Operation:                 "release_agent",
		CurrentStage:              "completed",
		AgenticNextAction:         "task_audit_submitted",
		AgentID:                   currentAgentID,
		AgenticID:                 currentAgentID,
		CompletedAt:               completedAt,
		AgenticCompletionEvidence: completionEvidence,
		AgenticIDCleared:          true,
		AuditTarget:               auditTarget,
		AuditSubmitted:            auditSubmitted,
		AuditReference:            auditReference,
		OK:                        true,
		Gate:                      "release_agent",
		GateStatus:                "passed",
	}); err != nil {
		return writeJSON(stdout, output.Failure("release_agent", "event_write_failed", err.Error(), "请检查工作空间目录权限"))
	}
	return writeJSON(stdout, output.Success("release_agent", map[string]any{
		"workspace":                   workspaceName,
		"issue_key":                   issueKey,
		"agentic_run_id":              runID,
		"agent_id":                    currentAgentID,
		"agentic_id_cleared":          true,
		"jira_transition_id":          jiraTransitionID,
		"completed_at":                completedAt,
		"agentic_completion_evidence": completionEvidence,
		"audit_target":                auditTarget,
		"audit_submitted":             auditSubmitted,
		"audit_reference":             auditReference,
		"current_stage":               "completed",
		"agentic_next_action":         "task_audit_submitted",
	}))
}
