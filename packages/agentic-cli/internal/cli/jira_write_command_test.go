package cli

import (
	"bytes"
	"errors"
	"path/filepath"
	"strings"
	"testing"

	"github.com/tapstate/agentic-ops/packages/agentic-cli/internal/clihandlers"
)

func TestAddTaskCommentRequiresRealJiraConfirmation(t *testing.T) {
	root := t.TempDir()
	t.Setenv("AGENTIC_OPS_WORKSPACE_ROOT", root)
	contentPath := filepath.Join(root, "analysis.md")
	writeCLITestFile(t, contentPath, "# 准入分析\n\n需要确认修复分支。")
	client := &recordingJiraClient{issue: realModeIssue()}
	withJiraClientForTest(t, clihandlers.JiraClientSelection{Client: client, Mode: "real"})

	var stdout bytes.Buffer
	var stderr bytes.Buffer
	code := Run([]string{"add-task-comment", "TAP-123", "--workspace", "tapdata", "--category", "analysis", "--content-file", contentPath}, &stdout, &stderr)
	if code != 1 {
		t.Fatalf("code = %d stdout = %s stderr = %s", code, stdout.String(), stderr.String())
	}
	assertJSONField(t, stdout.String(), "code", "real_jira_confirmation_required")
	if client.commentKey != "" {
		t.Fatalf("comment should not be written before confirmation: %s", client.commentKey)
	}
	assertEventLogContains(t, root, `"operation":"add_task_comment"`)
	assertEventLogContains(t, root, `"gate_status":"blocked"`)
}

func TestAddTaskCommentWritesCategorizedContentAndAuditEvent(t *testing.T) {
	root := t.TempDir()
	t.Setenv("AGENTIC_OPS_WORKSPACE_ROOT", root)
	contentPath := filepath.Join(root, "plan.md")
	writeCLITestFile(t, contentPath, "# 修复计划 v1\n\n运行目标模块测试。")
	client := &recordingJiraClient{issue: realModeIssue()}
	withJiraClientForTest(t, clihandlers.JiraClientSelection{Client: client, Mode: "real"})

	var stdout bytes.Buffer
	var stderr bytes.Buffer
	code := Run([]string{"add-task-comment", "TAP-123", "--workspace", "tapdata", "--category", "plan", "--content-file", contentPath, "--confirm-real-jira-write"}, &stdout, &stderr)
	if code != 0 {
		t.Fatalf("code = %d stdout = %s stderr = %s", code, stdout.String(), stderr.String())
	}
	if client.commentKey != "TAP-123" {
		t.Fatalf("commentKey = %s", client.commentKey)
	}
	for _, want := range []string{"AgenticOps 任务记录", "分类: plan", "# 修复计划 v1"} {
		if !strings.Contains(client.commentBody, want) {
			t.Fatalf("comment missing %q: %s", want, client.commentBody)
		}
	}
	assertJSONField(t, stdout.String(), "category", "plan")
	assertJSONField(t, stdout.String(), "current_stage", "jira_write_completed")
	assertEventLogContains(t, root, `"gate_status":"passed"`)
}

func TestUpdateTaskDescriptionSectionsWritesValidatedSections(t *testing.T) {
	root := t.TempDir()
	t.Setenv("AGENTIC_OPS_WORKSPACE_ROOT", root)
	sectionsPath := filepath.Join(root, "sections.yaml")
	writeCLITestFile(t, sectionsPath, "sections:\n  问题分支: develop\n  修复分支: release-v3.31\n  验收标准: 告警不再重复出现\n")
	client := &recordingJiraClient{issue: realModeIssue()}
	withJiraClientForTest(t, clihandlers.JiraClientSelection{Client: client, Mode: "real"})

	var stdout bytes.Buffer
	var stderr bytes.Buffer
	code := Run([]string{"update-task-description-sections", "TAP-123", "--workspace", "tapdata", "--sections-file", sectionsPath, "--confirm-real-jira-write"}, &stdout, &stderr)
	if code != 0 {
		t.Fatalf("code = %d stdout = %s stderr = %s", code, stdout.String(), stderr.String())
	}
	if client.descriptionKey != "TAP-123" || client.descriptionSections["问题分支"] != "develop" {
		t.Fatalf("description update = %s %#v", client.descriptionKey, client.descriptionSections)
	}
	assertJSONNumber(t, stdout.String(), "updated_section_count", 3)
}

func TestUpdateTaskFormMapsOnlyWritableJiraFields(t *testing.T) {
	root := t.TempDir()
	t.Setenv("AGENTIC_OPS_WORKSPACE_ROOT", root)
	valuesPath := filepath.Join(root, "values.yaml")
	writeCLITestFile(t, valuesPath, "values:\n  issue_analysis: 已定位到告警去重条件缺失\n  fix_details: 增加任务维度去重\n  verification_method: go test ./manager/tm/...\n")
	issue := realModeIssue()
	issue.Status = "In Progress"
	client := &recordingJiraClient{issue: issue}
	withJiraClientForTest(t, clihandlers.JiraClientSelection{Client: client, Mode: "real"})

	var stdout bytes.Buffer
	var stderr bytes.Buffer
	code := Run([]string{"update-task-form", "TAP-123", "--workspace", "tapdata", "--values-file", valuesPath, "--confirm-real-jira-write"}, &stdout, &stderr)
	if code != 0 {
		t.Fatalf("code = %d stdout = %s stderr = %s", code, stdout.String(), stderr.String())
	}
	if client.updatedFields["customfield_10092"] != "已定位到告警去重条件缺失" ||
		client.updatedFields["customfield_10093"] != "增加任务维度去重" ||
		client.updatedFields["customfield_10049"] != "go test ./manager/tm/..." {
		t.Fatalf("updatedFields = %#v", client.updatedFields)
	}
	assertJSONNumber(t, stdout.String(), "updated_field_count", 3)
}

func TestUpdateTaskFormRejectsOwnerFieldEvenWhenMappedToJira(t *testing.T) {
	root := t.TempDir()
	t.Setenv("AGENTIC_OPS_WORKSPACE_ROOT", root)
	valuesPath := filepath.Join(root, "values.yaml")
	writeCLITestFile(t, valuesPath, "values:\n  owner: another-user\n")
	issue := realModeIssue()
	issue.Status = "In Progress"
	client := &recordingJiraClient{issue: issue}
	withJiraClientForTest(t, clihandlers.JiraClientSelection{Client: client, Mode: "real"})

	var stdout bytes.Buffer
	var stderr bytes.Buffer
	code := Run([]string{"update-task-form", "TAP-123", "--workspace", "tapdata", "--values-file", valuesPath, "--confirm-real-jira-write"}, &stdout, &stderr)
	if code != 1 {
		t.Fatalf("code = %d stdout = %s stderr = %s", code, stdout.String(), stderr.String())
	}
	assertJSONField(t, stdout.String(), "code", "form_field_not_writable")
	if client.updatedKey != "" {
		t.Fatalf("owner must not be written: %#v", client.updatedFields)
	}
}

func TestUpdateTaskFormRejectsStageOutsideOperationContract(t *testing.T) {
	root := t.TempDir()
	t.Setenv("AGENTIC_OPS_WORKSPACE_ROOT", root)
	valuesPath := filepath.Join(root, "values.yaml")
	writeCLITestFile(t, valuesPath, "values:\n  issue_analysis: 已完成分析\n")
	client := &recordingJiraClient{issue: realModeIssue()}
	withJiraClientForTest(t, clihandlers.JiraClientSelection{Client: client, Mode: "real"})

	var stdout bytes.Buffer
	var stderr bytes.Buffer
	code := Run([]string{"update-task-form", "TAP-123", "--workspace", "tapdata", "--values-file", valuesPath, "--confirm-real-jira-write"}, &stdout, &stderr)
	if code != 1 {
		t.Fatalf("code = %d stdout = %s stderr = %s", code, stdout.String(), stderr.String())
	}
	assertJSONField(t, stdout.String(), "code", "operation_stage_not_allowed")
	if client.updatedKey != "" {
		t.Fatalf("fields must not be written outside allowed stage: %#v", client.updatedFields)
	}
}

func TestUpdateTaskFormRejectsDescriptionSectionMapping(t *testing.T) {
	root := t.TempDir()
	t.Setenv("AGENTIC_OPS_WORKSPACE_ROOT", root)
	valuesPath := filepath.Join(root, "values.yaml")
	writeCLITestFile(t, valuesPath, "values:\n  problem_branch: develop\n")
	issue := realModeIssue()
	issue.Status = "In Progress"
	client := &recordingJiraClient{issue: issue}
	withJiraClientForTest(t, clihandlers.JiraClientSelection{Client: client, Mode: "real"})

	var stdout bytes.Buffer
	var stderr bytes.Buffer
	code := Run([]string{"update-task-form", "TAP-123", "--workspace", "tapdata", "--values-file", valuesPath, "--confirm-real-jira-write"}, &stdout, &stderr)
	if code != 1 {
		t.Fatalf("code = %d stdout = %s stderr = %s", code, stdout.String(), stderr.String())
	}
	assertJSONField(t, stdout.String(), "code", "form_field_not_writable")
	if client.updatedKey != "" {
		t.Fatalf("fields should not be written: %#v", client.updatedFields)
	}
}

func TestTaskWriteRejectsOtherAgentOwnership(t *testing.T) {
	root := t.TempDir()
	t.Setenv("AGENTIC_OPS_WORKSPACE_ROOT", root)
	contentPath := filepath.Join(root, "analysis.md")
	writeCLITestFile(t, contentPath, "分析内容")
	issue := realModeIssue()
	issue.AgenticID = "other-agent"
	client := &recordingJiraClient{issue: issue}
	withJiraClientForTest(t, clihandlers.JiraClientSelection{Client: client, Mode: "real"})

	var stdout bytes.Buffer
	var stderr bytes.Buffer
	code := Run([]string{"add-task-comment", "TAP-123", "--workspace", "tapdata", "--category", "analysis", "--content-file", contentPath, "--confirm-real-jira-write"}, &stdout, &stderr)
	if code != 1 {
		t.Fatalf("code = %d stdout = %s stderr = %s", code, stdout.String(), stderr.String())
	}
	assertJSONField(t, stdout.String(), "code", "agent_ownership_conflict")
}

func TestTaskWriteReportsRemoteCompletionWhenFinalAuditFails(t *testing.T) {
	root := t.TempDir()
	t.Setenv("AGENTIC_OPS_WORKSPACE_ROOT", root)
	contentPath := filepath.Join(root, "analysis.md")
	writeCLITestFile(t, contentPath, "分析内容")
	client := &recordingJiraClient{issue: realModeIssue()}
	withJiraClientForTest(t, clihandlers.JiraClientSelection{Client: client, Mode: "real"})
	callCount := 0
	restore := clihandlers.SetTaskWriteEventAppenderForTest(func(writeContext clihandlers.TaskWriteContext, operation string, stage string, code string, ok bool, requiresHumanAction bool) error {
		callCount++
		if callCount == 2 {
			return errors.New("audit storage unavailable")
		}
		return nil
	})
	t.Cleanup(restore)

	var stdout bytes.Buffer
	var stderr bytes.Buffer
	code := Run([]string{"add-task-comment", "TAP-123", "--workspace", "tapdata", "--category", "analysis", "--content-file", contentPath, "--confirm-real-jira-write"}, &stdout, &stderr)
	if code != 1 {
		t.Fatalf("code = %d stdout = %s stderr = %s", code, stdout.String(), stderr.String())
	}
	assertJSONField(t, stdout.String(), "code", "jira_write_completed_audit_failed")
	assertJSONField(t, stdout.String(), "remote_write_completed", true)
	assertJSONField(t, stdout.String(), "retry_safe", false)
	if client.commentKey != "TAP-123" {
		t.Fatal("Jira write should have completed before final audit failure")
	}
}
