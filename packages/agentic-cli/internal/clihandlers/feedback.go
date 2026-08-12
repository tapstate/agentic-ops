package clihandlers

import (
	"fmt"
	"github.com/tapstate/agentic-ops/packages/agentic-cli/internal/evidence"
	"github.com/tapstate/agentic-ops/packages/agentic-cli/internal/feedback"
	"github.com/tapstate/agentic-ops/packages/agentic-cli/internal/output"
	"io"
	"os"
	"path/filepath"
	"strings"
)

func runFeedbackReport(args []string, stdout io.Writer) int {
	workspaceName := workspaceNameFromArgsOrAgentConfig(args, "default")
	date := readFlag(args, "--date", currentClock.Now().UTC().Format("2006-01-02"))
	events, root, err := readFilteredFeedbackEvents(args, workspaceName)
	if err != nil {
		return writeJSON(stdout, output.Failure("feedback_report", err.code, err.message, err.action))
	}
	report := feedback.Summarize(events)
	reportPath := filepath.Join(root, ".agentic-ops", "feedback", "reports", date+".md")
	if err := feedback.WriteMarkdown(reportPath, workspaceName, date, report); err != nil {
		return writeJSON(stdout, output.Failure("feedback_report", "report_write_failed", err.Error(), "请检查工作空间目录权限"))
	}
	payload := map[string]any{
		"workspace":           workspaceName,
		"date":                date,
		"runs":                report.Runs,
		"succeeded":           report.Succeeded,
		"blocked":             report.Blocked,
		"failed":              report.Failed,
		"report":              reportPath,
		"agentic_next_action": "review_proposals",
	}
	if len(report.MissingFields) > 0 {
		payload["missing_fields"] = report.MissingFields
	}
	return writeJSON(stdout, output.Success("feedback_report", payload))
}

func runFeedbackAnalyze(args []string, stdout io.Writer) int {
	workspaceName := workspaceNameFromArgsOrAgentConfig(args, "default")
	events, root, err := readFilteredFeedbackEvents(args, workspaceName)
	if err != nil {
		return writeJSON(stdout, output.Failure("feedback_analyze", err.code, err.message, err.action))
	}
	analysis := feedback.Analyze(events)
	scope := feedbackScope(args)
	analysisPath := filepath.Join(root, ".agentic-ops", "feedback", "reports", "analysis-"+scope+".md")
	if err := feedback.WriteAnalysisMarkdown(analysisPath, workspaceName, scope, analysis); err != nil {
		return writeJSON(stdout, output.Failure("feedback_analyze", "report_write_failed", err.Error(), "请检查工作空间目录权限"))
	}
	return writeJSON(stdout, output.Success("feedback_analyze", map[string]any{
		"workspace":            workspaceName,
		"scope":                scope,
		"runs":                 analysis.Runs,
		"failure_patterns":     analysis.FailurePatterns,
		"recovery_patterns":    analysis.RecoveryPatterns,
		"human_gate_hotspots":  analysis.HumanGateHotspots,
		"missing_field_trends": analysis.MissingFieldTrends,
		"suggested_assets":     analysis.SuggestedAssets,
		"analysis_report":      analysisPath,
		"agentic_next_action":  "review_proposals",
	}))
}

func runFeedbackPropose(args []string, stdout io.Writer) int {
	workspaceName := workspaceNameFromArgsOrAgentConfig(args, "default")
	events, root, err := readFilteredFeedbackEvents(args, workspaceName)
	if err != nil {
		return writeJSON(stdout, output.Failure("feedback_propose", err.code, err.message, err.action))
	}
	proposals := feedback.Propose(events)
	scope := feedbackScope(args)
	proposalPath := filepath.Join(root, ".agentic-ops", "feedback", "reports", "proposals-"+scope+".md")
	if err := feedback.WriteProposalsMarkdown(proposalPath, workspaceName, scope, proposals); err != nil {
		return writeJSON(stdout, output.Failure("feedback_propose", "report_write_failed", err.Error(), "请检查工作空间目录权限"))
	}
	return writeJSON(stdout, output.Success("feedback_propose", map[string]any{
		"workspace":           workspaceName,
		"scope":               scope,
		"proposals":           proposals,
		"proposal_report":     proposalPath,
		"agentic_next_action": "maintainer_decision_required",
	}))
}

type feedbackReadError struct {
	code    string
	message string
	action  string
}

func readFilteredFeedbackEvents(args []string, workspaceName string) ([]feedback.Event, string, *feedbackReadError) {
	root, err := workspaceRoot()
	if err != nil {
		return nil, "", &feedbackReadError{code: "workspace_root_failed", message: "无法读取当前工作目录", action: "请在项目 AI 工作空间中重试"}
	}
	events, err := feedback.ReadEvents(filepath.Join(root, ".agentic-ops", "feedback", "events.ndjson"))
	if err != nil {
		return nil, "", &feedbackReadError{code: "event_read_failed", message: err.Error(), action: "请检查工作空间反馈日志"}
	}
	filtered, err := feedback.FilterEvents(events, feedback.EventFilter{
		Workspace:    workspaceName,
		AgenticRunID: readFlag(args, "--run-id", ""),
		IssueKey:     readFlag(args, "--issue-key", ""),
		TaskType:     readFlag(args, "--task-type", ""),
		Code:         readFlag(args, "--code", ""),
		Date:         readFlag(args, "--date", ""),
		From:         readFlag(args, "--from", ""),
		To:           readFlag(args, "--to", ""),
	})
	if err != nil {
		return nil, "", &feedbackReadError{code: "invalid_filter", message: err.Error(), action: "请检查日期和时间范围参数"}
	}
	return filtered, root, nil
}

func feedbackScope(args []string) string {
	if date := readFlag(args, "--date", ""); date != "" {
		return date
	}
	from := readFlag(args, "--from", "")
	to := readFlag(args, "--to", "")
	if from != "" || to != "" {
		return strings.ReplaceAll(strings.Trim(from+"-"+to, "-"), ":", "-")
	}
	return currentClock.Now().UTC().Format("2006-01-02")
}

func runFeedbackBundle(args []string, stdout io.Writer) int {
	workspaceName := workspaceNameFromArgsOrAgentConfig(args, "default")
	runID := readFlag(args, "--run-id", "")
	if runID == "" {
		return writeJSON(stdout, output.FailureWithContext("feedback_bundle", output.FailureContext{
			Code:                "missing_agentic_run_id",
			Message:             "缺少 agentic_run_id",
			RequiredHumanAction: "请提供 --run-id",
			TaskType:            "diagnosis",
			CurrentStage:        "feedback_bundle",
			AgenticNextAction:   "ask_owner",
		}))
	}
	redact := hasFlag(args, "--redact")
	root, err := workspaceRoot()
	if err != nil {
		return writeJSON(stdout, output.Failure("feedback_bundle", "workspace_root_failed", "无法读取当前工作目录", "请在项目 AI 工作空间中重试"))
	}
	eventsPath := filepath.Join(root, ".agentic-ops", "feedback", "events.ndjson")
	rawEvents, err := os.ReadFile(eventsPath)
	if err != nil {
		return writeJSON(stdout, output.Failure("feedback_bundle", "event_read_failed", err.Error(), "请检查工作空间反馈日志"))
	}
	content := string(rawEvents)
	if redact {
		content = redactSensitive(content)
	}
	bundlePath := filepath.Join(root, ".agentic-ops", "feedback", "bundles", runID+".md")
	bundle := fmt.Sprintf("# Feedback Bundle\n\n- workspace: %s\n- agentic_run_id: %s\n- redacted: %t\n\n## Events\n\n```json\n%s\n```\n", workspaceName, runID, redact, content)
	if err := evidence.Write(bundlePath, bundle); err != nil {
		return writeJSON(stdout, output.Failure("feedback_bundle", "bundle_write_failed", err.Error(), "请检查工作空间目录权限"))
	}
	return writeJSON(stdout, output.Success("feedback_bundle", map[string]any{
		"workspace":           workspaceName,
		"agentic_run_id":      runID,
		"bundle":              bundlePath,
		"redacted":            redact,
		"agentic_next_action": "share_bundle_with_maintainer",
	}))
}
