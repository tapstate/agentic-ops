package clihandlers

import (
	"fmt"
	"os"
	"path/filepath"
	"strings"

	"github.com/tapstate/agentic-ops/packages/agentic-cli/internal/jira"
	"github.com/tapstate/agentic-ops/packages/agentic-cli/internal/runcontext"
)

type resumeFeedback struct {
	Required          bool
	WriteAllowed      bool
	File              string
	Category          string
	AgenticNextAction string
}

func writeResumeFeedback(root string, context runcontext.Context, decision jira.ResumeDecision) (resumeFeedback, error) {
	if !decision.JiraFeedbackRequired {
		return resumeFeedback{}, nil
	}
	if !safeResumeFeedbackSegment(context.AgenticRunID) || !safeResumeFeedbackSegment(decision.Code) {
		return resumeFeedback{}, fmt.Errorf("invalid resume feedback path segment")
	}
	relativePath := filepath.Join(
		".agentic-ops",
		"runs",
		context.AgenticRunID,
		"resume-blocked-"+decision.Code+".md",
	)
	absolutePath := filepath.Join(root, relativePath)
	if !pathWithin(absolutePath, root) {
		return resumeFeedback{}, fmt.Errorf("resume feedback path escapes workspace")
	}
	if err := os.MkdirAll(filepath.Dir(absolutePath), 0o755); err != nil {
		return resumeFeedback{}, err
	}
	content := strings.Join([]string{
		"# AgenticOps 恢复阻塞",
		"",
		"- 反馈编号: resume-blocked:" + context.AgenticRunID + ":" + decision.Code,
		"- 工作空间: " + context.Workspace,
		"- Jira 卡片: " + context.IssueKey,
		"- agentic_run_id: " + context.AgenticRunID,
		"- 错误码: " + decision.Code,
		"- 说明: " + decision.Message,
		"- 需要处理: " + decision.RequiredHumanAction,
		"",
	}, "\n")
	if err := os.WriteFile(absolutePath, []byte(content), 0o644); err != nil {
		return resumeFeedback{}, err
	}
	nextAction := "ask_owner_to_add_task_comment"
	if decision.JiraFeedbackWriteAllowed {
		nextAction = "add_task_comment"
	}
	return resumeFeedback{
		Required:          true,
		WriteAllowed:      decision.JiraFeedbackWriteAllowed,
		File:              filepath.ToSlash(relativePath),
		Category:          "blocked",
		AgenticNextAction: nextAction,
	}, nil
}

func safeResumeFeedbackSegment(value string) bool {
	return value != "" && value != "." && value != ".." && filepath.Base(value) == value
}
