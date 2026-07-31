package clihandlers

import (
	"context"
	"github.com/tapstate/agentic-ops/packages/agentic-cli/internal/evidence"
	"github.com/tapstate/agentic-ops/packages/agentic-cli/internal/feedback"
	"github.com/tapstate/agentic-ops/packages/agentic-cli/internal/output"
	"io"
	"path/filepath"
)

func runWriteEvidence(args []string, stdout io.Writer) int {
	workspaceName := workspaceNameFromArgsOrAgentConfig(args, "default")
	runID := readFlag(args, "--run-id", "")
	if runID == "" {
		_ = appendWorkspaceEventWithCode(workspaceName, "", "", "evidence_write", "write_evidence", "input_validation", "ask_owner", "missing_agentic_run_id", "input_validation", false, true)
		return writeJSON(stdout, output.FailureWithContext("write_evidence", output.FailureContext{
			Code:                "missing_agentic_run_id",
			Message:             "缺少 agentic_run_id",
			RequiredHumanAction: "请提供 --run-id",
			TaskType:            "evidence_write",
			CurrentStage:        "input_validation",
			AgenticNextAction:   "ask_owner",
		}))
	}
	root, err := workspaceRoot()
	if err != nil {
		return writeJSON(stdout, output.Failure("write_evidence", "workspace_root_failed", "无法读取当前工作目录", "请在项目 AI 工作空间中重试"))
	}
	state, err := evidenceRunState(root, workspaceName, runID)
	if err != nil {
		return writeJSON(stdout, output.FailureWithContext("write_evidence", output.FailureContext{
			Code:                evidenceStateErrorCode(err),
			Message:             err.Error(),
			RequiredHumanAction: "请检查 agentic_run_id 是否存在有效接管事件，且仍属于当前 AIAgent",
			TaskType:            "evidence_write",
			CurrentStage:        "evidence_write_gate",
			AgenticNextAction:   "ask_owner",
		}))
	}
	path := filepath.Join(root, ".agentic-ops", "runs", runID, "evidence.md")
	workspaceProfile := takeoverProfile(workspaceName)
	selection, err := selectJiraClient(workspaceName, workspaceProfile)
	if err != nil {
		return writeJSON(stdout, output.Failure("write_evidence", "jira_adapter_config_failed", err.Error(), "请检查 Jira adapter 配置"))
	}
	issueKey := readFlag(args, "--issue-key", state.IssueKey)
	gateName := evidencePolicyGateName(selection.Mode)
	policyPath, requiresHumanGate, err := policyRequiresHumanGate(gateName)
	if err != nil {
		return writeJSON(stdout, output.FailureWithContext("write_evidence", output.FailureContext{
			Code:                "policy_not_found",
			Message:             err.Error(),
			RequiredHumanAction: "请检查 install-resources/basic/policies/default.yaml 是否存在且通过校验",
			TaskType:            "evidence_write",
			CurrentStage:        "evidence_write_gate",
			AgenticNextAction:   "fix_policy",
		}))
	}
	if requiresHumanGate {
		_ = appendWorkspaceEventWithDetails(workspaceName, feedback.Event{
			AgenticRunID:        runID,
			IssueKey:            state.IssueKey,
			TaskType:            "evidence_write",
			Operation:           "write_evidence",
			CurrentStage:        "evidence_write_gate",
			AgenticNextAction:   "ask_owner",
			AgentID:             state.AgentID,
			AgenticID:           state.AgenticID,
			TargetRepo:          state.TargetRepo,
			TaskClass:           state.TaskClass,
			ProcessID:           state.ProcessID,
			OK:                  false,
			Code:                "policy_gate_required",
			Gate:                gateName,
			GateStatus:          "blocked",
			HumanGate:           true,
			RequiresHumanAction: true,
		})
		return writeJSON(stdout, output.FailureWithContext("write_evidence", output.FailureContext{
			Code:                "policy_gate_required",
			Message:             gateName + " requires a human gate in " + policyPath,
			RequiredHumanAction: "请由负责人确认该 evidence 写入策略，或调整 policy gate 后重试",
			TaskType:            "evidence_write",
			CurrentStage:        "evidence_write_gate",
			AgenticNextAction:   "ask_owner",
		}))
	}
	templatePath, template, err := evidenceTemplate(workspaceProfile)
	if err != nil {
		return writeJSON(stdout, output.FailureWithContext("write_evidence", output.FailureContext{
			Code:                "evidence_template_missing",
			Message:             err.Error(),
			RequiredHumanAction: "请维护 workflow profile 的 development_completed 证据模板",
			TaskType:            "evidence_write",
			CurrentStage:        "evidence_write_gate",
			AgenticNextAction:   "fix_profile",
		}))
	}
	if selection.Mode == "real" {
		if issueKey == "" {
			return writeJSON(stdout, output.Failure("write_evidence", "run_not_found", "未找到 agentic_run_id 对应的 Jira 卡片", "请检查 agentic_run_id 是否存在有效接管事件"))
		}
		if !hasFlag(args, "--confirm-real-jira-write") {
			_ = appendRealJiraWriteGateEvent(workspaceName, runID, issueKey, "write_evidence", "evidence_write_gate", "ask_owner", "real_jira_confirmation_required", false, true)
			return writeJSON(stdout, output.FailureWithContext("write_evidence", output.FailureContext{
				Code:                "real_jira_confirmation_required",
				Message:             "真实 Jira 评论写入需要显式确认",
				RequiredHumanAction: "请确认证据内容、策略和门禁后添加 --confirm-real-jira-write",
				TaskType:            "evidence_write",
				CurrentStage:        "evidence_write_gate",
				AgenticNextAction:   "ask_owner",
			}))
		}
	}
	content := renderEvidenceTemplate(template, map[string]string{
		"workspace":      workspaceName,
		"agentic_run_id": runID,
		"issue_key":      state.IssueKey,
		"task_class":     state.TaskClass,
		"process_id":     state.ProcessID,
		"previous_stage": state.PreviousStage,
		"target_repo":    state.TargetRepo,
	})
	if err := evidence.Write(path, content); err != nil {
		return writeJSON(stdout, output.Failure("write_evidence", "write_failed", err.Error(), "请检查工作空间目录权限"))
	}
	if selection.Mode == "real" {
		if err := selection.Client.AddComment(context.Background(), issueKey, content); err != nil {
			_ = appendRealJiraWriteGateEvent(workspaceName, runID, issueKey, "write_evidence", "evidence_write_gate", "ask_owner", "jira_comment_write_failed", false, false)
			return writeJSON(stdout, output.Failure("write_evidence", "jira_comment_write_failed", err.Error(), "请检查 Jira 评论权限、策略和门禁"))
		}
		if err := appendRealJiraWriteGateEvent(workspaceName, runID, issueKey, "write_evidence", "evidence_written", "request_owner_confirmation", "", true, false); err != nil {
			return writeJSON(stdout, output.Failure("write_evidence", "event_write_failed", err.Error(), "请检查工作空间目录权限"))
		}
	}
	if err := appendWorkspaceEventWithDetails(workspaceName, feedback.Event{
		AgenticRunID:      runID,
		IssueKey:          state.IssueKey,
		TaskType:          "evidence_write",
		Operation:         "write_evidence",
		CurrentStage:      "evidence_written",
		AgenticNextAction: "request_owner_confirmation",
		AgentID:           state.AgentID,
		AgenticID:         state.AgenticID,
		TargetRepo:        state.TargetRepo,
		TaskClass:         state.TaskClass,
		ProcessID:         state.ProcessID,
		AuditTarget:       "local_file",
		AuditSubmitted:    true,
		AuditReference:    path,
		OK:                true,
		Gate:              "write_evidence",
		GateStatus:        "passed",
	}); err != nil {
		return writeJSON(stdout, output.Failure("write_evidence", "event_write_failed", err.Error(), "请检查工作空间目录权限"))
	}
	return writeJSON(stdout, output.Success("write_evidence", map[string]any{
		"workspace":           workspaceName,
		"agentic_run_id":      runID,
		"issue_key":           state.IssueKey,
		"task_class":          state.TaskClass,
		"process_id":          state.ProcessID,
		"target_repo":         state.TargetRepo,
		"evidence":            path,
		"template":            templatePath,
		"audit_target":        "local_file",
		"audit_submitted":     true,
		"audit_reference":     path,
		"current_stage":       "evidence_written",
		"agentic_next_action": "request_owner_confirmation",
	}))
}
