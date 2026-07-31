package clihandlers

import (
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/tapstate/agentic-ops/packages/agentic-cli/internal/jira"
	"github.com/tapstate/agentic-ops/packages/agentic-cli/internal/runcontext"
)

func TestWriteResumeFeedbackCreatesSafeWritableComment(t *testing.T) {
	root := t.TempDir()
	context := resumeFeedbackContext()
	decision := jira.ResumeDecision{
		Code:                     "agent_binding_lost",
		Message:                  "Jira 上的 AIAgent 绑定已丢失",
		RequiredHumanAction:      "请研发工程师确认后重新接管",
		JiraFeedbackRequired:     true,
		JiraFeedbackWriteAllowed: true,
	}

	got, err := writeResumeFeedback(root, context, decision)
	if err != nil {
		t.Fatalf("writeResumeFeedback error = %v", err)
	}
	if !got.Required || !got.WriteAllowed || got.Category != "blocked" || got.AgenticNextAction != "add_task_comment" {
		t.Fatalf("resumeFeedback = %#v", got)
	}
	wantFile := filepath.ToSlash(filepath.Join(
		".agentic-ops",
		"runs",
		"run-1",
		"resume-blocked-agent_binding_lost.md",
	))
	if got.File != wantFile {
		t.Fatalf("File = %q, want %q", got.File, wantFile)
	}
	content, err := os.ReadFile(filepath.Join(root, filepath.FromSlash(got.File)))
	if err != nil {
		t.Fatalf("ReadFile error = %v", err)
	}
	for _, want := range []string{
		"# AgenticOps 恢复阻塞",
		"resume-blocked:run-1:agent_binding_lost",
		"工作空间: tapstate",
		"Jira 卡片: TAP-123",
		"错误码: agent_binding_lost",
		"需要处理: 请研发工程师确认后重新接管",
	} {
		if !strings.Contains(string(content), want) {
			t.Fatalf("feedback missing %q: %s", want, string(content))
		}
	}
	if strings.Contains(string(content), root) {
		t.Fatalf("feedback contains absolute workspace path: %s", string(content))
	}
}

func TestWriteResumeFeedbackRoutesOwnerOnlyAndNonFeedbackFailures(t *testing.T) {
	tests := []struct {
		name       string
		decision   jira.ResumeDecision
		wantFile   bool
		wantAction string
	}{
		{
			name: "owner only",
			decision: jira.ResumeDecision{
				Code:                 "agent_ownership_conflict",
				Message:              "当前 Jira 卡片已绑定其他 AIAgent",
				RequiredHumanAction:  "请研发工程师处理",
				JiraFeedbackRequired: true,
			},
			wantFile:   true,
			wantAction: "ask_owner_to_add_task_comment",
		},
		{
			name: "terminal run",
			decision: jira.ResumeDecision{
				Code:                "terminal_run",
				Message:             "当前 run 已完成",
				RequiredHumanAction: "请检查审计记录",
			},
		},
	}

	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			root := t.TempDir()
			got, err := writeResumeFeedback(root, resumeFeedbackContext(), test.decision)
			if err != nil {
				t.Fatalf("writeResumeFeedback error = %v", err)
			}
			if (got.File != "") != test.wantFile || got.AgenticNextAction != test.wantAction {
				t.Fatalf("resumeFeedback = %#v", got)
			}
		})
	}
}

func resumeFeedbackContext() runcontext.Context {
	return runcontext.Context{
		Workspace:         "tapstate",
		AgenticRunID:      "run-1",
		IssueKey:          "TAP-123",
		AgentID:           "agent-1",
		AgenticID:         "agent-1",
		TaskClass:         "technical_task",
		ProcessID:         "development_change_v1",
		TargetRepo:        "tapstate/example-repo",
		CurrentStage:      "takeover_started",
		AgenticNextAction: "proceed",
	}
}
