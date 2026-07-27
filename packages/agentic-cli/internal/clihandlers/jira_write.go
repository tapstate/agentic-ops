package clihandlers

import (
	"context"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"sort"
	"strings"

	"github.com/tapstate/agentic-ops/packages/agentic-cli/internal/contract"
	"github.com/tapstate/agentic-ops/packages/agentic-cli/internal/feedback"
	"github.com/tapstate/agentic-ops/packages/agentic-cli/internal/jira"
	"github.com/tapstate/agentic-ops/packages/agentic-cli/internal/output"
	"github.com/tapstate/agentic-ops/packages/agentic-cli/internal/profile"
	"gopkg.in/yaml.v3"
)

var taskCommentCategories = map[string]bool{
	"analysis": true,
	"plan":     true,
	"decision": true,
	"evidence": true,
	"blocked":  true,
}

type descriptionSectionsInput struct {
	Sections map[string]string `yaml:"sections"`
}

type taskFormInput struct {
	Values map[string]any `yaml:"values"`
}

type taskWriteContext struct {
	Workspace string
	IssueKey  string
	RunID     string
	Profile   profile.Profile
	Selection jiraClientSelection
	Issue     jira.Issue
}

type TaskWriteContext = taskWriteContext

var taskWriteEventAppender = appendTaskWriteEvent

func SetTaskWriteEventAppenderForTest(appender func(TaskWriteContext, string, string, string, bool, bool) error) func() {
	previous := taskWriteEventAppender
	taskWriteEventAppender = appender
	return func() {
		taskWriteEventAppender = previous
	}
}

func runAddTaskComment(args []string, stdout io.Writer) int {
	contentFile := readFlag(args, "--content-file", "")
	category := readFlag(args, "--category", "")
	if contentFile == "" {
		return taskWriteInputFailure(stdout, "add_task_comment", "missing_content_file", "缺少评论内容文件", "请提供 --content-file")
	}
	if !taskCommentCategories[category] {
		return taskWriteInputFailure(stdout, "add_task_comment", "invalid_comment_category", "评论分类无效", "请使用 analysis、plan、decision、evidence 或 blocked")
	}
	content, err := os.ReadFile(contentFile)
	if err != nil {
		return taskWriteInputFailure(stdout, "add_task_comment", "content_file_read_failed", err.Error(), "请检查 --content-file 路径和权限")
	}
	if strings.TrimSpace(string(content)) == "" {
		return taskWriteInputFailure(stdout, "add_task_comment", "empty_comment_content", "评论内容不能为空", "请补充评论内容")
	}
	writeContext, code := prepareTaskWrite(args, stdout, "add_task_comment")
	if code != 0 {
		return code
	}
	if code := requireRealJiraWriteConfirmation(args, stdout, writeContext, "add_task_comment"); code != 0 {
		return code
	}
	if code := beginTaskWrite(stdout, writeContext, "add_task_comment"); code != 0 {
		return code
	}
	body := renderTaskComment(writeContext, category, string(content))
	if err := writeContext.Selection.Client.AddComment(context.Background(), writeContext.IssueKey, body); err != nil {
		return taskWriteFailure(stdout, writeContext, "add_task_comment", "jira_comment_write_failed", err.Error())
	}
	if err := taskWriteEventAppender(writeContext, "add_task_comment", "jira_write_completed", "", true, false); err != nil {
		return taskWriteAuditCompletionFailure(stdout, writeContext, "add_task_comment", err)
	}
	return writeJSON(stdout, output.Success("add_task_comment", map[string]any{
		"workspace":     writeContext.Workspace,
		"issue_key":     writeContext.IssueKey,
		"run_id":        writeContext.RunID,
		"category":      category,
		"current_stage": "jira_write_completed",
		"next_action":   "inspect_by_agent",
	}))
}

func runUpdateTaskDescriptionSections(args []string, stdout io.Writer) int {
	sectionsFile := readFlag(args, "--sections-file", "")
	if sectionsFile == "" {
		return taskWriteInputFailure(stdout, "update_task_description_sections", "missing_sections_file", "缺少描述章节文件", "请提供 --sections-file")
	}
	var input descriptionSectionsInput
	if err := readYAMLFile(sectionsFile, &input); err != nil {
		return taskWriteInputFailure(stdout, "update_task_description_sections", "sections_file_read_failed", err.Error(), "请检查 YAML 文件格式和权限")
	}
	if len(input.Sections) == 0 {
		return taskWriteInputFailure(stdout, "update_task_description_sections", "empty_description_sections", "描述章节不能为空", "请在 sections 下提供至少一个章节")
	}
	for title := range input.Sections {
		if strings.TrimSpace(title) == "" {
			return taskWriteInputFailure(stdout, "update_task_description_sections", "invalid_description_section", "描述章节标题不能为空", "请检查 sections 文件")
		}
	}
	writeContext, code := prepareTaskWrite(args, stdout, "update_task_description_sections")
	if code != 0 {
		return code
	}
	if code := requireRealJiraWriteConfirmation(args, stdout, writeContext, "update_task_description_sections"); code != 0 {
		return code
	}
	if code := beginTaskWrite(stdout, writeContext, "update_task_description_sections"); code != 0 {
		return code
	}
	if err := writeContext.Selection.Client.UpdateDescriptionSections(context.Background(), writeContext.IssueKey, input.Sections); err != nil {
		return taskWriteFailure(stdout, writeContext, "update_task_description_sections", "jira_description_write_failed", err.Error())
	}
	if err := taskWriteEventAppender(writeContext, "update_task_description_sections", "jira_write_completed", "", true, false); err != nil {
		return taskWriteAuditCompletionFailure(stdout, writeContext, "update_task_description_sections", err)
	}
	return writeJSON(stdout, output.Success("update_task_description_sections", map[string]any{
		"workspace":             writeContext.Workspace,
		"issue_key":             writeContext.IssueKey,
		"run_id":                writeContext.RunID,
		"updated_section_count": len(input.Sections),
		"current_stage":         "jira_write_completed",
		"next_action":           "inspect_by_agent",
	}))
}

func runUpdateTaskForm(args []string, stdout io.Writer) int {
	valuesFile := readFlag(args, "--values-file", "")
	if valuesFile == "" {
		return taskWriteInputFailure(stdout, "update_task_form", "missing_values_file", "缺少表单值文件", "请提供 --values-file")
	}
	var input taskFormInput
	if err := readYAMLFile(valuesFile, &input); err != nil {
		return taskWriteInputFailure(stdout, "update_task_form", "values_file_read_failed", err.Error(), "请检查 YAML 文件格式和权限")
	}
	if len(input.Values) == 0 {
		return taskWriteInputFailure(stdout, "update_task_form", "empty_form_values", "表单值不能为空", "请在 values 下提供至少一个逻辑字段")
	}
	writeContext, code := prepareTaskWrite(args, stdout, "update_task_form")
	if code != 0 {
		return code
	}
	fields, invalidField := writableJiraFields(writeContext.Profile, input.Values)
	if invalidField != "" {
		return taskWriteInputFailure(stdout, "update_task_form", "form_field_not_writable", "逻辑字段不能通过 update-task-form 写入："+invalidField, "请检查项目 profile 映射，或使用对应的评论、描述章节原子操作")
	}
	if code := requireRealJiraWriteConfirmation(args, stdout, writeContext, "update_task_form"); code != 0 {
		return code
	}
	if code := beginTaskWrite(stdout, writeContext, "update_task_form"); code != 0 {
		return code
	}
	if err := writeContext.Selection.Client.UpdateFields(context.Background(), writeContext.IssueKey, fields); err != nil {
		return taskWriteFailure(stdout, writeContext, "update_task_form", "jira_form_write_failed", err.Error())
	}
	if err := taskWriteEventAppender(writeContext, "update_task_form", "jira_write_completed", "", true, false); err != nil {
		return taskWriteAuditCompletionFailure(stdout, writeContext, "update_task_form", err)
	}
	return writeJSON(stdout, output.Success("update_task_form", map[string]any{
		"workspace":           writeContext.Workspace,
		"issue_key":           writeContext.IssueKey,
		"run_id":              writeContext.RunID,
		"updated_field_count": len(fields),
		"current_stage":       "jira_write_completed",
		"next_action":         "inspect_by_agent",
	}))
}

func prepareTaskWrite(args []string, stdout io.Writer, operation string) (taskWriteContext, int) {
	if len(args) < 2 || strings.HasPrefix(args[1], "--") {
		return taskWriteContext{}, taskWriteInputFailure(stdout, operation, "missing_issue_key", "缺少 Jira 卡片编号", "请提供 Jira 卡片编号")
	}
	workspaceName := workspaceNameFromArgsOrAgentConfig(args, "default")
	issueKey := args[1]
	workspaceProfile := takeoverProfile(workspaceName)
	selection, err := selectJiraClient(workspaceName, workspaceProfile)
	if err != nil {
		return taskWriteContext{}, writeJSON(stdout, output.Failure(operation, "jira_adapter_config_failed", err.Error(), "请检查 Jira 适配器配置"))
	}
	issue, ok, err := selection.Client.GetIssueByKey(context.Background(), workspaceName, issueKey)
	if err != nil {
		return taskWriteContext{}, writeJSON(stdout, output.Failure(operation, "jira_issue_read_failed", err.Error(), "请检查 Jira 适配器配置和卡片权限"))
	}
	if !ok {
		return taskWriteContext{}, writeJSON(stdout, output.Failure(operation, "issue_not_found", "未找到 Jira 卡片", "请检查 Jira 卡片编号"))
	}
	currentJiraUser, err := selection.Client.CurrentUser(context.Background())
	if err != nil {
		return taskWriteContext{}, writeJSON(stdout, output.Failure(operation, "jira_current_user_failed", err.Error(), "请检查 Jira 适配器登录状态"))
	}
	if issue.Owner == "" || issue.Owner != currentJiraUser {
		return taskWriteContext{}, writeJSON(stdout, output.Failure(operation, "owner_mismatch", "当前研发工程师与 Jira 卡片负责人不匹配", "请确认当前 Jira 用户和卡片负责人"))
	}
	if issue.Assignee == "" || issue.Assignee != currentJiraUser {
		return taskWriteContext{}, writeJSON(stdout, output.Failure(operation, "assignee_mismatch", "当前 Jira assignee 与当前用户不匹配", "请把 Jira assignee 调整为当前研发工程师后重试"))
	}
	if issue.CurrentAgentID != "" && issue.CurrentAgentID != agentID() {
		return taskWriteContext{}, writeJSON(stdout, output.Failure(operation, "agent_ownership_conflict", "当前 Jira 卡片已绑定其他 AIAgent", "请研发工程师确认是否释放当前代理绑定"))
	}
	mappedStage, ok := workspaceProfile.StatusMapping[issue.Status]
	if !ok {
		return taskWriteContext{}, writeJSON(stdout, output.Failure(operation, "unknown_jira_status", "当前 Jira 状态未配置阶段映射", "请维护项目 profile 的 status_mapping"))
	}
	if code := validateTaskWriteStage(stdout, operation, mappedStage); code != 0 {
		return taskWriteContext{}, code
	}
	runID := readFlag(args, "--run-id", "")
	if runID != "" {
		root, err := workspaceRoot()
		if err != nil {
			return taskWriteContext{}, writeJSON(stdout, output.Failure(operation, "workspace_root_failed", err.Error(), "请在项目 AI 工作空间中重试"))
		}
		state, err := evidenceRunState(root, workspaceName, runID)
		if err != nil {
			return taskWriteContext{}, writeJSON(stdout, output.Failure(operation, evidenceStateErrorCode(err), err.Error(), "请检查 run_id 是否属于当前任务和 AIAgent"))
		}
		if state.IssueKey != issueKey {
			return taskWriteContext{}, writeJSON(stdout, output.Failure(operation, "run_issue_mismatch", "run_id 与 Jira 卡片不匹配", "请使用该卡片对应的 run_id"))
		}
	}
	return taskWriteContext{
		Workspace: workspaceName,
		IssueKey:  issueKey,
		RunID:     runID,
		Profile:   workspaceProfile,
		Selection: selection,
		Issue:     issue,
	}, 0
}

func requireRealJiraWriteConfirmation(args []string, stdout io.Writer, writeContext taskWriteContext, operation string) int {
	if writeContext.Selection.Mode != "real" || hasFlag(args, "--confirm-real-jira-write") {
		return 0
	}
	_ = taskWriteEventAppender(writeContext, operation, "jira_write_gate", "real_jira_confirmation_required", false, true)
	return writeJSON(stdout, output.FailureWithContext(operation, output.FailureContext{
		Code:                "real_jira_confirmation_required",
		Message:             "真实 Jira 写入需要显式确认",
		RequiredHumanAction: "请研发工程师确认写入内容后添加 --confirm-real-jira-write",
		TaskType:            "jira_write",
		CurrentStage:        "jira_write_gate",
		NextAction:          "ask_owner",
	}))
}

func taskWriteInputFailure(stdout io.Writer, operation string, code string, message string, action string) int {
	return writeJSON(stdout, output.Failure(operation, code, message, action))
}

func taskWriteFailure(stdout io.Writer, writeContext taskWriteContext, operation string, code string, message string) int {
	_ = taskWriteEventAppender(writeContext, operation, "jira_write_failed", code, false, false)
	return writeJSON(stdout, output.Failure(operation, code, message, "请检查 Jira 权限、字段配置和写入内容"))
}

func appendTaskWriteEvent(writeContext taskWriteContext, operation string, stage string, code string, ok bool, requiresHumanAction bool) error {
	return appendWorkspaceEventWithDetails(writeContext.Workspace, feedback.Event{
		RunID:               writeContext.RunID,
		IssueKey:            writeContext.IssueKey,
		TaskType:            "jira_write",
		Operation:           operation,
		CurrentStage:        stage,
		NextAction:          taskWriteNextAction(stage, ok, requiresHumanAction),
		AgentID:             agentID(),
		CurrentAgentID:      writeContext.Issue.CurrentAgentID,
		OK:                  ok,
		Code:                code,
		Gate:                "real_jira_write",
		GateStatus:          gateStatus(ok, requiresHumanAction),
		HumanGate:           true,
		RequiresHumanAction: requiresHumanAction,
	})
}

func taskWriteNextAction(stage string, ok bool, requiresHumanAction bool) string {
	if stage == "jira_write_started" {
		return "write_jira"
	}
	if ok {
		return "inspect_by_agent"
	}
	if requiresHumanAction {
		return "ask_owner"
	}
	return "blocked"
}

func beginTaskWrite(stdout io.Writer, writeContext taskWriteContext, operation string) int {
	if err := taskWriteEventAppender(writeContext, operation, "jira_write_started", "", true, false); err != nil {
		return writeJSON(stdout, output.Failure(operation, "event_write_failed", err.Error(), "本地审计未就绪，未执行 Jira 写入；请检查工作空间目录权限"))
	}
	return 0
}

func taskWriteAuditCompletionFailure(stdout io.Writer, writeContext taskWriteContext, operation string, err error) int {
	result := output.FailureWithContext(operation, output.FailureContext{
		Code:                "jira_write_completed_audit_failed",
		Message:             "Jira 写入已完成，但本地完成审计记录失败：" + err.Error(),
		RequiredHumanAction: "不要盲目重试 Jira 写入；请先执行 inspect-task 核对远端结果并修复本地审计目录",
		TaskType:            "jira_write",
		CurrentStage:        "jira_write_audit_failed",
		NextAction:          "inspect_by_agent",
	})
	result["workspace"] = writeContext.Workspace
	result["issue_key"] = writeContext.IssueKey
	result["run_id"] = writeContext.RunID
	result["remote_write_completed"] = true
	result["retry_safe"] = false
	return writeJSON(stdout, result)
}

func validateTaskWriteStage(stdout io.Writer, operation string, mappedStage string) int {
	root, err := repoRoot()
	if err != nil {
		return writeJSON(stdout, output.Failure(operation, "operation_contract_not_found", "无法定位操作合同", "请检查 AgenticOps 安装资源"))
	}
	path := filepath.Join(repoBasicResourcesPath(root), "contracts", "operations", strings.ReplaceAll(operation, "_", "-")+".yaml")
	operationContract, err := contract.LoadFile(path)
	if err != nil {
		return writeJSON(stdout, output.Failure(operation, "operation_contract_load_failed", err.Error(), "请检查操作合同资源"))
	}
	for _, allowedStage := range operationContract.AllowedStages {
		if allowedStage == mappedStage {
			return 0
		}
	}
	return writeJSON(stdout, output.FailureWithContext(operation, output.FailureContext{
		Code:                "operation_stage_not_allowed",
		Message:             "当前 Jira 阶段不允许执行该操作：" + mappedStage,
		RequiredHumanAction: "请按操作合同确认 Jira 状态和允许阶段",
		TaskType:            "jira_write",
		CurrentStage:        mappedStage,
		NextAction:          "inspect_by_agent",
	}))
}

func renderTaskComment(writeContext taskWriteContext, category string, content string) string {
	lines := []string{
		"AgenticOps 任务记录",
		"分类: " + category,
		"工作空间: " + writeContext.Workspace,
		"Jira 卡片: " + writeContext.IssueKey,
	}
	if writeContext.RunID != "" {
		lines = append(lines, "run_id: "+writeContext.RunID)
	}
	return strings.Join(lines, "\n") + "\n\n" + strings.TrimSpace(content)
}

func writableJiraFields(workspaceProfile profile.Profile, values map[string]any) (map[string]any, string) {
	names := make([]string, 0, len(values))
	for name := range values {
		names = append(names, name)
	}
	sort.Strings(names)
	fields := make(map[string]any, len(values))
	for _, name := range names {
		mapping, ok := workspaceProfile.JiraFormMapping.Fields[name]
		if !ok || mapping.Source != "jira_field" || mapping.JiraField == "" || !mapping.Writable {
			return nil, name
		}
		fields[mapping.JiraField] = values[name]
	}
	return fields, ""
}

func readYAMLFile(path string, target any) error {
	data, err := os.ReadFile(path)
	if err != nil {
		return err
	}
	if err := yaml.Unmarshal(data, target); err != nil {
		return fmt.Errorf("parse YAML: %w", err)
	}
	return nil
}
