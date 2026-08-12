package clihandlers

import (
	"context"
	"fmt"
	"io"
	"net/url"
	"path/filepath"
	"strconv"
	"strings"

	"github.com/tapstate/agentic-ops/packages/agentic-cli/internal/evidence"
	"github.com/tapstate/agentic-ops/packages/agentic-cli/internal/feedback"
	"github.com/tapstate/agentic-ops/packages/agentic-cli/internal/github"
	"github.com/tapstate/agentic-ops/packages/agentic-cli/internal/output"
)

func runWritePREvidence(args []string, stdout io.Writer) int {
	workspaceName := workspaceNameFromArgsOrAgentConfig(args, "default")
	runID := readFlag(args, "--run-id", "")
	if runID == "" {
		_ = appendWorkspaceEventWithCode(workspaceName, "", "", "evidence_write", "write_pr_evidence", "input_validation", "ask_owner", "missing_agentic_run_id", "input_validation", false, true)
		return writeJSON(stdout, output.FailureWithContext("write_pr_evidence", output.FailureContext{
			Code: "missing_agentic_run_id", Message: "缺少 agentic_run_id", RequiredHumanAction: "请提供 --run-id",
			TaskType: "evidence_write", CurrentStage: "input_validation", AgenticNextAction: "ask_owner",
		}))
	}
	prURL := readFlag(args, "--pr-url", "")
	if prURL == "" {
		return writePREvidenceFailure(stdout, workspaceName, runID, "", "missing_pr_url", "缺少拉取请求 URL", "请提供 --pr-url", "pr_evidence_gate", "ask_owner")
	}
	repo, pr, err := parsePRURL(prURL)
	if err != nil {
		return writePREvidenceFailure(stdout, workspaceName, runID, "", "invalid_pr_url", err.Error(), "请提供 GitHub 拉取请求 URL", "pr_evidence_gate", "ask_owner")
	}
	root, err := workspaceRoot()
	if err != nil {
		return writeJSON(stdout, output.Failure("write_pr_evidence", "workspace_root_failed", "无法读取当前工作目录", "请在项目 AI 工作空间中重试"))
	}
	state, err := evidenceRunState(root, workspaceName, runID)
	if err != nil {
		return writeJSON(stdout, output.FailureWithContext("write_pr_evidence", output.FailureContext{
			Code: evidenceStateErrorCode(err), Message: err.Error(), RequiredHumanAction: "请检查 agentic_run_id 是否存在有效接管事件，且仍属于当前 AIAgent",
			TaskType: "evidence_write", CurrentStage: "pr_evidence_gate", AgenticNextAction: "ask_owner",
		}))
	}
	if !isPREvidenceStage(state.CurrentStage) {
		return writePREvidenceFailure(stdout, workspaceName, runID, state.IssueKey, "operation_stage_not_allowed", "当前阶段不允许写入拉取请求证据: "+state.CurrentStage, "请先完成 PR 创建、CI 检查或 Review 检查", "pr_evidence_gate", "ask_owner")
	}
	selection, err := selectJiraClient(workspaceName, takeoverProfile(workspaceName))
	if err != nil {
		return writeJSON(stdout, output.Failure("write_pr_evidence", "jira_adapter_config_failed", err.Error(), "请检查 Jira adapter 配置"))
	}
	gateName := evidencePolicyGateName(selection.Mode)
	policyPath, requiresHumanGate, err := policyRequiresHumanGate(gateName)
	if err != nil {
		return writeJSON(stdout, output.FailureWithContext("write_pr_evidence", output.FailureContext{
			Code: "policy_not_found", Message: err.Error(), RequiredHumanAction: "请检查 install-resources/basic/policies/default.yaml 是否存在且通过校验",
			TaskType: "evidence_write", CurrentStage: "pr_evidence_gate", AgenticNextAction: "fix_policy",
		}))
	}
	if requiresHumanGate {
		return writePREvidenceFailure(stdout, workspaceName, runID, state.IssueKey, "policy_gate_required", gateName+" requires a human gate in "+policyPath, "请由负责人确认该 evidence 写入策略，或调整 policy gate 后重试", "pr_evidence_gate", "ask_owner")
	}
	if selection.Mode == "real" {
		if state.IssueKey == "" {
			return writePREvidenceFailure(stdout, workspaceName, runID, "", "run_not_found", "未找到 agentic_run_id 对应的 Jira 卡片", "请检查 agentic_run_id 是否存在有效接管事件", "pr_evidence_gate", "ask_owner")
		}
		if !hasFlag(args, "--confirm-real-jira-write") {
			_ = appendRealJiraWriteGateEvent(workspaceName, runID, state.IssueKey, "write_pr_evidence", "pr_evidence_gate", "ask_owner", "real_jira_confirmation_required", false, true)
			return writeJSON(stdout, output.FailureWithContext("write_pr_evidence", output.FailureContext{
				Code: "real_jira_confirmation_required", Message: "真实 Jira 评论写入需要显式确认", RequiredHumanAction: "请确认证据内容、策略和门禁后添加 --confirm-real-jira-write",
				TaskType: "evidence_write", CurrentStage: "pr_evidence_gate", AgenticNextAction: "ask_owner",
			}))
		}
	}

	facts, err := gitHubClient.ReadPullRequestFacts(context.Background(), repo, pr)
	if err != nil {
		return writePREvidenceFailure(stdout, workspaceName, runID, state.IssueKey, github.ReadErrorCode(err), err.Error(), "请检查 GitHub CLI 登录状态、仓库权限和 PR URL", "pr_evidence_gate", "fix_environment")
	}
	reviewStatus := summarizeReviewStatus(facts.Reviews)
	path := filepath.Join(root, ".agentic-ops", "runs", runID, "pr-evidence.md")
	content := renderPREvidence(workspaceName, runID, state, facts, reviewStatus)
	if err := evidence.Write(path, content); err != nil {
		return writeJSON(stdout, output.Failure("write_pr_evidence", "write_failed", err.Error(), "请检查工作空间目录权限"))
	}
	if selection.Mode == "real" {
		if err := selection.Client.AddComment(context.Background(), state.IssueKey, content); err != nil {
			_ = appendRealJiraWriteGateEvent(workspaceName, runID, state.IssueKey, "write_pr_evidence", "pr_evidence_gate", "ask_owner", "jira_comment_write_failed", false, false)
			return writeJSON(stdout, output.Failure("write_pr_evidence", "jira_comment_write_failed", err.Error(), "请检查 Jira 评论权限、策略和门禁"))
		}
		if err := appendRealJiraWriteGateEvent(workspaceName, runID, state.IssueKey, "write_pr_evidence", "pr_evidence_written", "request_owner_confirmation", "", true, false); err != nil {
			return writeJSON(stdout, output.Failure("write_pr_evidence", "event_write_failed", err.Error(), "请检查工作空间目录权限"))
		}
	}
	if err := appendWorkspaceEventWithDetails(workspaceName, feedback.Event{
		AgenticRunID: runID, IssueKey: state.IssueKey, TaskType: "evidence_write", Operation: "write_pr_evidence",
		CurrentStage: "pr_evidence_written", AgenticNextAction: "request_owner_confirmation", AgentID: state.AgentID, AgenticID: state.AgenticID,
		TargetRepo: state.TargetRepo, TaskClass: state.TaskClass, ProcessID: state.ProcessID, AuditTarget: "local_file", AuditSubmitted: true,
		AuditReference: path, OK: true, Gate: "write_pr_evidence", GateStatus: "passed",
	}); err != nil {
		return writeJSON(stdout, output.Failure("write_pr_evidence", "event_write_failed", err.Error(), "请检查工作空间目录权限"))
	}
	return writeJSON(stdout, output.Success("write_pr_evidence", map[string]any{
		"workspace": workspaceName, "agentic_run_id": runID, "issue_key": state.IssueKey, "target_repo": state.TargetRepo,
		"pr_url": facts.URL, "pr_number": pr, "head_sha": facts.HeadSHA, "ci_status": facts.CI.Status, "review_status": reviewStatus, "review_count": len(facts.Reviews),
		"check_count": len(facts.CI.Checks), "failing_check_count": len(facts.CI.FailingChecks), "pending_check_count": len(facts.CI.PendingChecks), "evidence": path, "audit_target": "local_file", "audit_submitted": true,
		"audit_reference": path, "current_stage": "pr_evidence_written", "agentic_next_action": "request_owner_confirmation",
	}))
}

func writePREvidenceFailure(stdout io.Writer, workspaceName string, runID string, issueKey string, code string, message string, action string, stage string, nextAction string) int {
	_ = appendWorkspaceEventWithDetails(workspaceName, feedback.Event{
		AgenticRunID: runID, IssueKey: issueKey, TaskType: "evidence_write", Operation: "write_pr_evidence",
		CurrentStage: stage, AgenticNextAction: nextAction, OK: false, Code: code, Gate: "write_pr_evidence",
		GateStatus: gateStatus(false, code == "policy_gate_required" || code == "missing_pr_url" || code == "operation_stage_not_allowed"),
		HumanGate:  code == "policy_gate_required" || code == "missing_pr_url" || code == "operation_stage_not_allowed", RequiresHumanAction: true,
	})
	return writeJSON(stdout, output.FailureWithContext("write_pr_evidence", output.FailureContext{
		Code: code, Message: message, RequiredHumanAction: action, TaskType: "evidence_write", CurrentStage: stage, AgenticNextAction: nextAction,
	}))
}

func parsePRURL(raw string) (string, string, error) {
	parsed, err := url.Parse(raw)
	if err != nil || parsed.Scheme != "https" || (parsed.Hostname() != "github.com" && parsed.Hostname() != "www.github.com") {
		return "", "", fmt.Errorf("PR URL 必须是 github.com 的 HTTPS 地址")
	}
	parts := strings.Split(strings.Trim(parsed.Path, "/"), "/")
	if len(parts) != 4 || parts[2] != "pull" || parts[0] == "" || parts[1] == "" || parts[3] == "" {
		return "", "", fmt.Errorf("PR URL 路径必须符合 /<owner>/<repo>/pull/<number>")
	}
	if _, err := strconv.Atoi(parts[3]); err != nil {
		return "", "", fmt.Errorf("PR 编号必须是数字")
	}
	return parts[0] + "/" + parts[1], parts[3], nil
}

func isPREvidenceStage(stage string) bool {
	switch stage {
	case "pr_plan_prepared", "pr_created", "ci_passed", "review_approved":
		return true
	default:
		return false
	}
}

func summarizeReviewStatus(reviews []github.PRReview) string {
	status := "pending"
	for _, review := range reviews {
		switch strings.ToUpper(review.State) {
		case "CHANGES_REQUESTED":
			return "changes_requested"
		case "APPROVED":
			status = "approved"
		}
	}
	return status
}

func renderPREvidence(workspaceName string, runID string, state evidenceRunContext, facts github.PullRequestFacts, reviewStatus string) string {
	risk := "未发现 GitHub CI 事实风险。"
	if facts.CI.Status == "not_configured" {
		risk = "GitHub 未配置 CI 检查，不能视为 CI 已通过；是否继续由项目策略和研发工程师决定。"
	}
	return fmt.Sprintf("# 拉取请求证据\n\n- 工作空间：%s\n- Agentic 运行编号：%s\n- Jira 卡片：%s\n- 目标仓库：%s\n- PR URL：%s\n- PR head SHA：%s\n- CI 状态：%s\n- 检查总数：%d\n- 失败检查数：%d\n- 待完成检查数：%d\n- Review 状态：%s\n- Review 数量：%d\n- GitHub 事实读取时间：%s\n- 事实来源：GitHub REST API（gh api）\n\n## 当前结论\n\n已回读 PR、CI、评论和 Review 事实，等待研发工程师确认。\n\n## 风险\n\n风险：%s\n\n## 需要研发工程师处理\n\n请核对 PR head、CI 四态和 Review 结论后决定是否继续。\n", workspaceName, runID, state.IssueKey, state.TargetRepo, facts.URL, facts.HeadSHA, facts.CI.Status, len(facts.CI.Checks), len(facts.CI.FailingChecks), len(facts.CI.PendingChecks), reviewStatus, len(facts.Reviews), facts.ReadAt.Format("2006-01-02T15:04:05Z07:00"), risk)
}
