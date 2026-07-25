package clihandlers

import (
	"fmt"
	"github.com/tapstate/agentic-ops/packages/agentic-cli/internal/evidence"
	"github.com/tapstate/agentic-ops/packages/agentic-cli/internal/feedback"
	"github.com/tapstate/agentic-ops/packages/agentic-cli/internal/output"
	"io"
	"os"
	"path/filepath"
	"time"
)

func runFeedbackReport(args []string, stdout io.Writer) int {
	workspaceName := workspaceNameFromArgsOrAgentConfig(args, "default")
	date := readFlag(args, "--date", time.Now().Format("2006-01-02"))
	root, err := workspaceRoot()
	if err != nil {
		return writeJSON(stdout, output.Failure("feedback_report", "workspace_root_failed", "无法读取当前工作目录", "请在项目 AI 工作空间中重试"))
	}
	events, err := feedback.ReadEvents(filepath.Join(root, ".agentic-ops", "feedback", "events.ndjson"))
	if err != nil {
		return writeJSON(stdout, output.Failure("feedback_report", "event_read_failed", err.Error(), "请检查工作空间反馈日志"))
	}
	report := feedback.Summarize(events)
	reportPath := filepath.Join(root, ".agentic-ops", "feedback", "reports", date+".md")
	if err := feedback.WriteMarkdown(reportPath, workspaceName, date, report); err != nil {
		return writeJSON(stdout, output.Failure("feedback_report", "report_write_failed", err.Error(), "请检查工作空间目录权限"))
	}
	payload := map[string]any{
		"workspace":   workspaceName,
		"date":        date,
		"runs":        report.Runs,
		"succeeded":   report.Succeeded,
		"blocked":     report.Blocked,
		"failed":      report.Failed,
		"report":      reportPath,
		"next_action": "review_proposals",
	}
	if len(report.MissingFields) > 0 {
		payload["missing_fields"] = report.MissingFields
	}
	return writeJSON(stdout, output.Success("feedback_report", payload))
}

func runFeedbackBundle(args []string, stdout io.Writer) int {
	workspaceName := workspaceNameFromArgsOrAgentConfig(args, "default")
	runID := readFlag(args, "--run-id", "")
	if runID == "" {
		return writeJSON(stdout, output.FailureWithContext("feedback_bundle", output.FailureContext{
			Code:                "missing_run_id",
			Message:             "缺少 run_id",
			RequiredHumanAction: "请提供 --run-id",
			TaskType:            "diagnosis",
			CurrentStage:        "feedback_bundle",
			NextAction:          "ask_owner",
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
	bundle := fmt.Sprintf("# Feedback Bundle\n\n- workspace: %s\n- run_id: %s\n- redacted: %t\n\n## Events\n\n```json\n%s\n```\n", workspaceName, runID, redact, content)
	if err := evidence.Write(bundlePath, bundle); err != nil {
		return writeJSON(stdout, output.Failure("feedback_bundle", "bundle_write_failed", err.Error(), "请检查工作空间目录权限"))
	}
	return writeJSON(stdout, output.Success("feedback_bundle", map[string]any{
		"workspace":   workspaceName,
		"run_id":      runID,
		"bundle":      bundlePath,
		"redacted":    redact,
		"next_action": "share_bundle_with_maintainer",
	}))
}
