package cli

import (
	"encoding/json"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"time"

	"github.com/tapstate/agentic-ops/packages/agent-task-ops/internal/config"
	"github.com/tapstate/agentic-ops/packages/agent-task-ops/internal/evidence"
	"github.com/tapstate/agentic-ops/packages/agent-task-ops/internal/feedback"
	"github.com/tapstate/agentic-ops/packages/agent-task-ops/internal/jira"
	"github.com/tapstate/agentic-ops/packages/agent-task-ops/internal/output"
	"github.com/tapstate/agentic-ops/packages/agent-task-ops/internal/workspace"
)

const Version = "0.1.0-dev"

func Run(args []string, stdout io.Writer, stderr io.Writer) int {
	if len(args) == 0 {
		return writeJSON(stdout, output.Failure("unknown", "missing_command", "缺少命令", "请提供命令"))
	}

	switch args[0] {
	case "--version", "version":
		return writeJSON(stdout, output.Success("version", map[string]any{"version": Version}))
	case "preflight":
		return runPreflight(args, stdout)
	case "workspace":
		if len(args) >= 2 && args[1] == "init" {
			return runWorkspaceInit(args, stdout)
		}
	case "agent":
		if len(args) >= 2 && args[1] == "init" {
			return runAgentInit(args, stdout)
		}
	case "list-tasks":
		return runListTasks(args, stdout)
	case "takeover-task":
		return runTakeoverTask(args, stdout)
	case "resume-takeover":
		return runResumeTakeover(args, stdout)
	case "write-evidence":
		return runWriteEvidence(args, stdout)
	case "feedback":
		if len(args) >= 2 && args[1] == "report" {
			return runFeedbackReport(args, stdout)
		}
	}

	fmt.Fprintf(stderr, "unknown command: %s\n", args[0])
	return writeJSON(stdout, output.Failure(args[0], "unknown_command", "未知命令", "请检查命令名称"))
}

func runPreflight(args []string, stdout io.Writer) int {
	home, _ := os.UserHomeDir()
	return writeJSON(stdout, output.Success("preflight", map[string]any{
		"workspace":   readFlag(args, "--workspace", "default"),
		"install_dir": config.DefaultInstallDir(home),
		"go_runtime":  "required",
		"jira":        "fake",
		"github":      "not_used_in_phase_one",
		"next_action": "workspace_init",
	}))
}

func runWorkspaceInit(args []string, stdout io.Writer) int {
	workspaceName := readFlag(args, "--workspace", "default")
	root, err := workspaceRoot()
	if err != nil {
		return writeJSON(stdout, output.Failure("workspace_init", "workspace_root_failed", "无法读取当前工作目录", "请在项目 AI 工作空间中重试"))
	}
	info, err := workspace.Ensure(root, workspaceName)
	if err != nil {
		return writeJSON(stdout, output.Failure("workspace_init", "workspace_init_failed", err.Error(), "请检查工作空间目录权限"))
	}
	return writeJSON(stdout, output.Success("workspace_init", map[string]any{
		"workspace":    info.Name,
		"profile":      info.Name,
		"runs_dir":     info.RunsDir,
		"feedback_dir": info.FeedbackDir,
		"next_action":  "init_agent_capability",
	}))
}

func runAgentInit(args []string, stdout io.Writer) int {
	return writeJSON(stdout, output.Success("agent_init", map[string]any{
		"workspace":     readFlag(args, "--workspace", "default"),
		"task_type":     "capability_initialization",
		"current_stage": "agent_capability_initialized",
		"next_action":   "list_tasks",
		"capabilities": []string{
			"preflight",
			"workspace_init",
			"list_tasks",
			"takeover_task",
			"resume_takeover",
			"write_evidence",
			"feedback_report",
		},
	}))
}

func runListTasks(args []string, stdout io.Writer) int {
	workspaceName := readFlag(args, "--workspace", "default")
	issues := jira.FakeClient{}.ListTasks(workspaceName)
	return writeJSON(stdout, output.Success("list_tasks", map[string]any{
		"workspace":   workspaceName,
		"tasks":       issues,
		"next_action": "takeover_task",
	}))
}

func runTakeoverTask(args []string, stdout io.Writer) int {
	if len(args) < 2 {
		return writeJSON(stdout, output.Failure("takeover_task", "missing_issue_key", "缺少 issue key", "请提供 Jira issue key"))
	}
	workspaceName := readFlag(args, "--workspace", "default")
	issueKey := args[1]
	issue, ok := jira.FakeClient{}.GetIssue(issueKey)
	if !ok {
		return writeJSON(stdout, output.Failure("takeover_task", "issue_not_found", "未找到 Jira issue", "请检查 issue key"))
	}
	runID := feedback.RunID(issue.Key, "task_takeover", fixedNow(), "a8f3")
	if err := appendWorkspaceEvent(workspaceName, runID, issue.Key, "task_takeover", "takeover_task", "takeover_started", "proceed", true, false); err != nil {
		return writeJSON(stdout, output.Failure("takeover_task", "event_write_failed", err.Error(), "请检查工作空间目录权限"))
	}
	return writeJSON(stdout, output.Success("takeover_task", map[string]any{
		"workspace":     workspaceName,
		"issue_key":     issue.Key,
		"run_id":        runID,
		"task_type":     "task_takeover",
		"current_stage": "takeover_started",
		"target_repo":   issue.TargetRepo,
		"next_action":   "proceed",
	}))
}

func runResumeTakeover(args []string, stdout io.Writer) int {
	runID := readFlag(args, "--run-id", "")
	if runID == "" {
		return writeJSON(stdout, output.Failure("resume_takeover", "missing_run_id", "缺少 run_id", "请提供 --run-id"))
	}
	workspaceName := readFlag(args, "--workspace", "default")
	if err := appendWorkspaceEvent(workspaceName, runID, "", "task_takeover", "resume_takeover", "takeover_resumed", "continue_development", true, false); err != nil {
		return writeJSON(stdout, output.Failure("resume_takeover", "event_write_failed", err.Error(), "请检查工作空间目录权限"))
	}
	return writeJSON(stdout, output.Success("resume_takeover", map[string]any{
		"workspace":     workspaceName,
		"run_id":        runID,
		"task_type":     "task_takeover",
		"current_stage": "takeover_resumed",
		"next_action":   "continue_development",
	}))
}

func runWriteEvidence(args []string, stdout io.Writer) int {
	workspaceName := readFlag(args, "--workspace", "default")
	runID := readFlag(args, "--run-id", "")
	if runID == "" {
		return writeJSON(stdout, output.Failure("write_evidence", "missing_run_id", "缺少 run_id", "请提供 --run-id"))
	}
	root, err := workspaceRoot()
	if err != nil {
		return writeJSON(stdout, output.Failure("write_evidence", "workspace_root_failed", "无法读取当前工作目录", "请在项目 AI 工作空间中重试"))
	}
	path := filepath.Join(root, ".agentic-ops", "runs", runID, "evidence.md")
	content := fmt.Sprintf("# Evidence\n\n- workspace: %s\n- run_id: %s\n- status: evidence_written\n", workspaceName, runID)
	if err := evidence.Write(path, content); err != nil {
		return writeJSON(stdout, output.Failure("write_evidence", "write_failed", err.Error(), "请检查工作空间目录权限"))
	}
	if err := appendWorkspaceEvent(workspaceName, runID, "", "evidence_write", "write_evidence", "evidence_written", "request_owner_confirmation", true, true); err != nil {
		return writeJSON(stdout, output.Failure("write_evidence", "event_write_failed", err.Error(), "请检查工作空间目录权限"))
	}
	return writeJSON(stdout, output.Success("write_evidence", map[string]any{
		"workspace":     workspaceName,
		"run_id":        runID,
		"evidence":      path,
		"current_stage": "evidence_written",
		"next_action":   "request_owner_confirmation",
	}))
}

func runFeedbackReport(args []string, stdout io.Writer) int {
	workspaceName := readFlag(args, "--workspace", "default")
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
	reportPath := filepath.Join(root, ".agentic-ops", "feedback", "daily", date+".md")
	if err := feedback.WriteMarkdown(reportPath, workspaceName, date, report); err != nil {
		return writeJSON(stdout, output.Failure("feedback_report", "report_write_failed", err.Error(), "请检查工作空间目录权限"))
	}
	return writeJSON(stdout, output.Success("feedback_report", map[string]any{
		"workspace":   workspaceName,
		"date":        date,
		"runs":        report.Runs,
		"succeeded":   report.Succeeded,
		"blocked":     report.Blocked,
		"failed":      report.Failed,
		"report":      reportPath,
		"next_action": "review_proposals",
	}))
}

func writeJSON(stdout io.Writer, payload map[string]any) int {
	encoded, err := json.Marshal(payload)
	if err != nil {
		fmt.Fprintln(stdout, `{"ok":false,"operation":"internal","code":"json_encode_failed","message":"JSON 编码失败"}`)
		return 1
	}
	fmt.Fprintln(stdout, string(encoded))
	if ok, _ := payload["ok"].(bool); ok {
		return 0
	}
	return 1
}

func readFlag(args []string, name string, fallback string) string {
	for i := 0; i < len(args)-1; i++ {
		if args[i] == name {
			return args[i+1]
		}
	}
	return fallback
}

func fixedNow() time.Time {
	return time.Date(2026, 7, 21, 10, 30, 12, 0, time.UTC)
}

func appendWorkspaceEvent(workspaceName string, runID string, issueKey string, taskType string, operation string, currentStage string, nextAction string, ok bool, requiresHumanAction bool) error {
	root, err := workspaceRoot()
	if err != nil {
		return err
	}
	return feedback.AppendEvent(filepath.Join(root, ".agentic-ops", "feedback", "events.ndjson"), feedback.Event{
		Timestamp:           fixedNow().Format(time.RFC3339),
		Workspace:           workspaceName,
		RunID:               runID,
		IssueKey:            issueKey,
		TaskType:            taskType,
		Operation:           operation,
		CurrentStage:        currentStage,
		NextAction:          nextAction,
		OK:                  ok,
		HumanGate:           false,
		RequiresHumanAction: requiresHumanAction,
	})
}

func workspaceRoot() (string, error) {
	if root := os.Getenv("AGENTIC_OPS_WORKSPACE_ROOT"); root != "" {
		return root, nil
	}
	return os.Getwd()
}
