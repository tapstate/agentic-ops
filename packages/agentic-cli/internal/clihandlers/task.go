package clihandlers

import (
	"context"
	"errors"
	"github.com/tapstate/agentic-ops/packages/agentic-cli/internal/admission"
	"github.com/tapstate/agentic-ops/packages/agentic-cli/internal/feedback"
	gitops "github.com/tapstate/agentic-ops/packages/agentic-cli/internal/git"
	"github.com/tapstate/agentic-ops/packages/agentic-cli/internal/jira"
	"github.com/tapstate/agentic-ops/packages/agentic-cli/internal/output"
	"github.com/tapstate/agentic-ops/packages/agentic-cli/internal/profile"
	"io"
	"os"
	"path/filepath"
	"strings"
	"time"
)

func runListTasks(args []string, stdout io.Writer) int {
	workspaceName := workspaceNameFromArgsOrAgentConfig(args, "default")
	workspaceProfile := takeoverProfile(workspaceName)
	selection, err := selectJiraClient(workspaceName, workspaceProfile)
	if err != nil {
		return writeJSON(stdout, jiraAdapterConfigFailure("list_tasks", workspaceName, err, "请检查 Jira adapter 配置"))
	}
	if selection.Mode != "real" && os.Getenv("AGENTIC_OPS_JIRA_ADAPTER") != "fake" {
		return writeJSON(stdout, output.Failure("list_tasks", "jira_adapter_config_failed", "list-tasks 必须读取真实 Jira；未显式启用本地 fake adapter", jiraRealConfigRequiredAction(workspaceName)))
	}
	issues, err := selection.Client.SearchIssues(context.Background(), workspaceName, workspaceProfile.Jira.TaskQuery)
	if err != nil {
		return writeJSON(stdout, output.Failure("list_tasks", "jira_search_failed", err.Error(), "请检查 Jira adapter 配置"))
	}
	return writeJSON(stdout, output.Success("list_tasks", map[string]any{
		"workspace":   workspaceName,
		"tasks":       issues,
		"next_action": "takeover_task",
	}))
}

func jiraRealConfigRequiredAction(workspaceName string) string {
	return "请设置 AGENTIC_OPS_JIRA_ADAPTER=real 并提供 Jira 连接配置，或维护本地配置文件：" + strings.Join(runtimeConfigScope(workspaceName).ConfigPaths(), ", ")
}

func runTakeoverTask(args []string, stdout io.Writer) int {
	if len(args) < 2 {
		return writeJSON(stdout, output.Failure("takeover_task", "missing_issue_key", "缺少 Jira 卡片编号", "请提供 Jira 卡片编号"))
	}
	workspaceName := workspaceNameFromArgsOrAgentConfig(args, "default")
	issueKey := args[1]
	workspaceProfile := takeoverProfile(workspaceName)
	selection, err := selectJiraClient(workspaceName, workspaceProfile)
	if err != nil {
		return writeJSON(stdout, output.Failure("takeover_task", "jira_adapter_config_failed", err.Error(), "请检查 Jira 适配器配置"))
	}
	issue, ok, err := selection.Client.GetIssueByKey(context.Background(), workspaceName, issueKey)
	if err != nil {
		return writeJSON(stdout, output.Failure("takeover_task", "jira_issue_read_failed", err.Error(), "请检查 Jira 适配器配置和卡片权限"))
	}
	if !ok {
		return writeJSON(stdout, output.Failure("takeover_task", "issue_not_found", "未找到 Jira 卡片", "请检查 Jira 卡片编号"))
	}
	currentJiraUser, err := selection.Client.CurrentUser(context.Background())
	if err != nil {
		return writeJSON(stdout, output.Failure("takeover_task", "jira_current_user_failed", err.Error(), "请检查 Jira 适配器登录状态"))
	}
	processRegistry, err := repoProcessRegistry()
	if err != nil {
		processRegistry = defaultProcessRegistry()
	}
	decision := jira.ValidateTakeoverEntryWithProcesses(issue, workspaceProfile, currentJiraUser, agentID(), processRegistry)
	if !decision.OK {
		missingFields := missingJiraFieldNames(decision)
		missingField := ""
		if len(missingFields) == 1 {
			missingField = missingFields[0]
		}
		_ = appendWorkspaceEventWithDetails(workspaceName, feedback.Event{
			IssueKey:            issue.Key,
			TaskType:            "task_takeover",
			Operation:           "takeover_task",
			CurrentStage:        decision.CurrentStage,
			NextAction:          decision.NextAction,
			OK:                  false,
			Code:                decision.Code,
			MissingField:        missingField,
			MissingFields:       missingFields,
			Gate:                "takeover_gate",
			GateStatus:          "blocked",
			HumanGate:           true,
			RequiresHumanAction: true,
		})
		result := output.FailureWithContext("takeover_task", output.FailureContext{
			Code:                decision.Code,
			Message:             decision.Message,
			RequiredHumanAction: decision.RequiredHumanAction,
			TaskType:            "task_takeover",
			CurrentStage:        decision.CurrentStage,
			NextAction:          decision.NextAction,
		})
		addMissingJiraFieldTemplate(result, missingFields, "takeover_task")
		return writeJSON(stdout, result)
	}
	standard, standardPath := admissionStandardFor(workspaceName, decision.TaskClass)
	admissionResult := admission.Check(standard, admissionValues(issue, decision.TargetRepo))
	if !admissionResult.OK {
		missingFields := admissionResult.MissingFields
		_ = appendWorkspaceEventWithDetails(workspaceName, feedback.Event{
			IssueKey:            issue.Key,
			TaskType:            "task_takeover",
			Operation:           "takeover_task",
			CurrentStage:        "takeover_gate",
			NextAction:          "ask_owner",
			OK:                  false,
			Code:                "admission_check_failed",
			MissingFields:       missingFields,
			Gate:                "admission_check",
			GateStatus:          "blocked",
			HumanGate:           true,
			RequiresHumanAction: true,
		})
		result := output.FailureWithContext("takeover_task", output.FailureContext{
			Code:                "admission_check_failed",
			Message:             "Jira 卡片未通过项目准入检查",
			RequiredHumanAction: "请按项目准入模板补齐 Jira 卡片，研发负责人确认后再继续执行",
			TaskType:            "task_takeover",
			CurrentStage:        "takeover_gate",
			NextAction:          "ask_owner",
		})
		addAdmissionFailureTemplate(result, workspaceName, standard, standardPath, missingFields, "takeover_task", workspaceProfile, issue, decision.TargetRepo)
		if selection.Mode == "real" {
			if !hasFlag(args, "--confirm-real-jira-write") {
				result["jira_comment_written"] = false
				result["jira_comment_requires_confirmation"] = true
			} else if comment, ok := result["completion_template"].(string); ok && comment != "" {
				if err := selection.Client.AddComment(context.Background(), issue.Key, comment); err != nil {
					result["jira_comment_written"] = false
					result["jira_comment_error"] = err.Error()
				} else {
					result["jira_comment_written"] = true
				}
			}
		}
		return writeJSON(stdout, result)
	}
	runID := feedback.RunID(issue.Key, "task_takeover", fixedNow(), "a8f3")
	takeoverAt := fixedNow().Format(time.RFC3339)
	currentAgentID := agentID()
	if selection.Mode == "real" {
		if !hasFlag(args, "--confirm-real-jira-write") {
			_ = appendWorkspaceEventWithCode(workspaceName, "", issue.Key, "task_takeover", "takeover_task", "takeover_gate", "ask_owner", "real_jira_confirmation_required", "real_jira_write", false, true)
			return writeJSON(stdout, output.FailureWithContext("takeover_task", output.FailureContext{
				Code:                "real_jira_confirmation_required",
				Message:             "真实 Jira 写入需要显式确认",
				RequiredHumanAction: "请确认 policy/gate 允许写入后添加 --confirm-real-jira-write",
				TaskType:            "task_takeover",
				CurrentStage:        "takeover_gate",
				NextAction:          "ask_owner",
			}))
		}
		fields := jiraTakeoverFields(workspaceProfile, currentAgentID, takeoverAt)
		if len(fields) == 0 {
			_ = appendRealJiraWriteGateEvent(workspaceName, runID, issue.Key, "takeover_task", "takeover_gate", "ask_owner", "missing_jira_write_mapping", false, true)
			return writeJSON(stdout, output.Failure("takeover_task", "missing_jira_write_mapping", "缺少 current_agent_id 或 takeover_at 字段映射", "请维护 workflow profile 的所有权字段映射"))
		}
		if err := selection.Client.UpdateFields(context.Background(), issue.Key, fields); err != nil {
			_ = appendRealJiraWriteGateEvent(workspaceName, runID, issue.Key, "takeover_task", "takeover_gate", "ask_owner", "jira_takeover_write_failed", false, false)
			return writeJSON(stdout, output.Failure("takeover_task", "jira_takeover_write_failed", err.Error(), "请检查 Jira 字段权限和 policy gate"))
		}
		if err := appendRealJiraWriteGateEvent(workspaceName, runID, issue.Key, "takeover_task", "takeover_started", "proceed", "", true, false); err != nil {
			return writeJSON(stdout, output.Failure("takeover_task", "event_write_failed", err.Error(), "请检查工作空间目录权限"))
		}
	}
	if err := appendWorkspaceEventWithDetails(workspaceName, feedback.Event{
		RunID:           runID,
		IssueKey:        issue.Key,
		TaskType:        "task_takeover",
		Operation:       "takeover_task",
		CurrentStage:    "takeover_started",
		NextAction:      "proceed",
		AgentID:         currentAgentID,
		CurrentAgentID:  currentAgentID,
		TakeoverAt:      takeoverAt,
		TargetRepo:      decision.TargetRepo,
		TaskClass:       decision.TaskClass,
		TaskClassSource: decision.TaskClassSource,
		ProcessID:       decision.ProcessID,
		OK:              true,
		Gate:            "takeover_task",
		GateStatus:      "passed",
	}); err != nil {
		return writeJSON(stdout, output.Failure("takeover_task", "event_write_failed", err.Error(), "请检查工作空间目录权限"))
	}
	return writeJSON(stdout, output.Success("takeover_task", map[string]any{
		"workspace":         workspaceName,
		"issue_key":         issue.Key,
		"run_id":            runID,
		"agent_id":          currentAgentID,
		"current_agent_id":  currentAgentID,
		"takeover_at":       takeoverAt,
		"task_type":         "task_takeover",
		"task_class":        decision.TaskClass,
		"task_class_source": decision.TaskClassSource,
		"process_id":        decision.ProcessID,
		"current_stage":     "takeover_started",
		"target_repo":       decision.TargetRepo,
		"next_action":       "proceed",
	}))
}

func addMissingJiraFieldTemplate(result map[string]any, missingFields []string, operation string) {
	if len(missingFields) == 0 {
		return
	}
	templatePath, template := jiraMissingFieldTemplate()
	template = strings.ReplaceAll(template, "<missing_field>", strings.Join(missingFields, ", "))
	template = strings.ReplaceAll(template, "<missing_fields>", markdownFieldList(missingFields))
	template = strings.ReplaceAll(template, "<missing_field_guidance>", missingFieldGuidance(missingFields))
	template = strings.ReplaceAll(template, "<admission_suggestions>", "- 暂无可自动推断的项目建议，请研发负责人按模板补齐。")
	template = strings.ReplaceAll(template, "<operation>", operation)
	if len(missingFields) == 1 {
		result["missing_field"] = missingFields[0]
	}
	result["missing_fields"] = missingFields
	result["missing_field_guidance"] = fieldGuidancePayload(missingFields)
	result["completion_template"] = template
	if templatePath != "" {
		result["completion_template_path"] = templatePath
	}
}

func addAdmissionFailureTemplate(result map[string]any, workspaceName string, standard admission.Standard, standardPath string, missingFields []string, operation string, p profile.Profile, issue jira.Issue, targetRepo string) {
	templatePath, template := admissionTemplate(workspaceName, standard)
	template = strings.ReplaceAll(template, "<missing_field>", strings.Join(missingFields, ", "))
	template = strings.ReplaceAll(template, "<missing_fields>", markdownAdmissionFieldList(standard, missingFields))
	template = strings.ReplaceAll(template, "<missing_field_guidance>", admissionFieldGuidance(standard, missingFields))
	template = strings.ReplaceAll(template, "<operation>", operation)
	result["missing_fields"] = missingFields
	result["missing_field_guidance"] = admissionGuidancePayload(standard, missingFields)
	result["admission_standard_path"] = standardPath
	result["admission_template_path"] = templatePath
	suggestions := admissionSuggestions(missingFields, p, issue, targetRepo)
	template = strings.ReplaceAll(template, "<admission_suggestions>", markdownAdmissionSuggestions(suggestions))
	result["suggestions"] = suggestions
	result["completion_template"] = template
	if len(missingFields) == 1 {
		result["missing_field"] = missingFields[0]
	}
}

func admissionStandardFor(workspaceName string, taskClass string) (admission.Standard, string) {
	projectPath, err := repoProjectPath(workspaceName)
	if err == nil {
		path := filepath.Join(projectPath, "admission", admissionFileName(taskClass))
		if standard, loadErr := admission.LoadFile(path); loadErr == nil {
			return standard, path
		}
	}
	return admission.DefaultStandard(taskClass), "agentic-cli default admission standard"
}

func admissionFileName(taskClass string) string {
	switch taskClass {
	case "bug_fix":
		return "defect-fix.yaml"
	case "feature_change":
		return "feature-change.yaml"
	case "technical_task":
		return "technical-task.yaml"
	default:
		return taskClass + ".yaml"
	}
}

func admissionValues(issue jira.Issue, targetRepo string) map[string]string {
	return map[string]string{
		"problem_branch":      issue.ProblemBranch,
		"target_branch":       issue.TargetBranch,
		"problem_summary":     firstNonEmpty(issue.ProblemSummary, issue.Summary),
		"reproduction_path":   issue.ReproductionPath,
		"acceptance_criteria": issue.AcceptanceCriteria,
		"target_repo":         targetRepo,
		"verification_method": issue.VerificationMethod,
		"risk_level":          issue.RiskLevel,
	}
}

func admissionTemplate(workspaceName string, standard admission.Standard) (string, string) {
	projectPath, err := repoProjectPath(workspaceName)
	if err == nil && standard.Template != "" {
		path := filepath.Join(projectPath, standard.Template)
		if data, readErr := os.ReadFile(path); readErr == nil {
			return path, string(data)
		}
	}
	templatePath, template := jiraMissingFieldTemplate()
	return templatePath, template
}

func markdownAdmissionFieldList(standard admission.Standard, fields []string) string {
	var lines []string
	for _, field := range fields {
		guidance := admissionGuidanceForField(standard, field)
		lines = append(lines, "- `"+field+"`："+guidance.Label)
	}
	return strings.Join(lines, "\n")
}

func admissionFieldGuidance(standard admission.Standard, fields []string) string {
	var lines []string
	for _, field := range fields {
		guidance := admissionGuidanceForField(standard, field)
		lines = append(lines, "- "+guidance.Label+"：建议填写在 "+guidance.Location+"。示例："+guidance.Example)
	}
	return strings.Join(lines, "\n")
}

func admissionGuidancePayload(standard admission.Standard, fields []string) []fieldGuidance {
	guidance := make([]fieldGuidance, 0, len(fields))
	for _, field := range fields {
		standardGuidance := admissionGuidanceForField(standard, field)
		guidance = append(guidance, fieldGuidance{
			Field:       field,
			Label:       standardGuidance.Label,
			Location:    standardGuidance.Location,
			Example:     standardGuidance.Example,
			Description: standardGuidance.Description,
		})
	}
	return guidance
}

func admissionGuidanceForField(standard admission.Standard, field string) admission.FieldGuidance {
	if guidance, ok := standard.Guidance[field]; ok && guidance.Label != "" {
		return guidance
	}
	fallback := guidanceForField(field)
	return admission.FieldGuidance{
		Label:       fallback.Label,
		Location:    fallback.Location,
		Example:     fallback.Example,
		Description: fallback.Description,
	}
}

func admissionSuggestions(fields []string, p profile.Profile, issue jira.Issue, targetRepo string) []string {
	var suggestions []string
	fieldSet := map[string]bool{}
	for _, field := range fields {
		fieldSet[field] = true
	}
	if (fieldSet["problem_branch"] || fieldSet["target_branch"]) && p.Local.SourceRoot != "" {
		if status, err := gitops.InspectWorkspace(context.Background(), p.Local.SourceRoot); err == nil && status.Branch != "" {
			suggestions = append(suggestions, "当前 source_root 分支是 "+status.Branch+"，可作为问题分支或修复分支候选，但必须由研发负责人确认。")
		}
	}
	if fieldSet["target_repo"] && targetRepo != "" {
		suggestions = append(suggestions, "根据项目仓库映射可推断目标仓库为 "+targetRepo+"，请研发负责人确认 Jira 卡片是否按该仓库执行。")
	}
	if issue.IssueType != "" {
		suggestions = append(suggestions, "当前 Jira 类型为 "+issue.IssueType+"，准入标准按任务分类执行；如分类不符合预期，请维护 task_class_mapping。")
	}
	suggestions = append(suggestions, projectCodeSuggestions(p.Local.SourceRoot, issue)...)
	return suggestions
}

func projectCodeSuggestions(sourceRoot string, issue jira.Issue) []string {
	if sourceRoot == "" {
		return nil
	}
	terms := codeSearchTerms(firstNonEmpty(issue.ProblemSummary, issue.Summary))
	if len(terms) == 0 {
		return nil
	}
	matches := searchSourceFiles(sourceRoot, terms, 5)
	if len(matches) == 0 {
		return []string{"已基于 Jira 摘要提取关键词，但未在 source_root 中找到明显命中的代码文件；后续分析需要结合更完整的问题现象或复现路径。"}
	}
	return []string{"基于 Jira 摘要在项目代码中命中候选文件：" + strings.Join(matches, "、") + "；这些只是初步分析线索，修复范围仍需研发负责人确认。"}
}

func codeSearchTerms(text string) []string {
	var terms []string
	seen := map[string]bool{}
	for _, raw := range strings.FieldsFunc(text, func(r rune) bool {
		return !(r >= 'A' && r <= 'Z') && !(r >= 'a' && r <= 'z') && !(r >= '0' && r <= '9')
	}) {
		term := strings.TrimSpace(raw)
		if len(term) < 4 {
			continue
		}
		key := strings.ToLower(term)
		if seen[key] {
			continue
		}
		seen[key] = true
		terms = append(terms, term)
		if len(terms) >= 4 {
			break
		}
	}
	return terms
}

func searchSourceFiles(root string, terms []string, limit int) []string {
	if matches := collectSearchMatches(root, terms, limit, true); len(matches) > 0 {
		return matches
	}
	return collectSearchMatches(root, terms, limit, false)
}

func collectSearchMatches(root string, terms []string, limit int, primaryOnly bool) []string {
	var matches []string
	_ = filepath.WalkDir(root, func(path string, entry os.DirEntry, err error) error {
		if err != nil || len(matches) >= limit {
			return nil
		}
		if entry.IsDir() {
			if strings.HasPrefix(entry.Name(), ".") {
				return filepath.SkipDir
			}
			switch entry.Name() {
			case "target", "node_modules", "build", "dist", "out":
				return filepath.SkipDir
			default:
				return nil
			}
		}
		if !isSearchableSourceFile(path) {
			return nil
		}
		if primaryOnly && !isPrimarySourceFile(path) {
			return nil
		}
		data, err := os.ReadFile(path)
		if err != nil || len(data) > 512*1024 {
			return nil
		}
		content := strings.ToLower(string(data))
		for _, term := range terms {
			if strings.Contains(content, strings.ToLower(term)) {
				if rel, relErr := filepath.Rel(root, path); relErr == nil {
					matches = append(matches, rel)
				}
				break
			}
		}
		return nil
	})
	return matches
}

func isPrimarySourceFile(path string) bool {
	switch strings.ToLower(filepath.Ext(path)) {
	case ".go", ".java", ".kt", ".js", ".ts", ".tsx", ".vue":
		return true
	default:
		return false
	}
}

func isSearchableSourceFile(path string) bool {
	switch strings.ToLower(filepath.Ext(path)) {
	case ".go", ".java", ".kt", ".js", ".ts", ".tsx", ".vue", ".xml", ".yaml", ".yml", ".properties", ".md":
		return true
	default:
		return false
	}
}

func markdownAdmissionSuggestions(suggestions []string) string {
	if len(suggestions) == 0 {
		return "- 暂无可自动推断的项目建议，请研发负责人按模板补齐。"
	}
	var lines []string
	for _, suggestion := range suggestions {
		lines = append(lines, "- "+suggestion)
	}
	return strings.Join(lines, "\n")
}

func missingJiraFieldNames(decision jira.TakeoverDecision) []string {
	if len(decision.MissingFields) > 0 {
		return decision.MissingFields
	}
	if field := missingJiraFieldName(decision.Code); field != "" {
		return []string{field}
	}
	return nil
}

func missingJiraFieldName(code string) string {
	switch code {
	case "missing_acceptance_criteria":
		return "acceptance_criteria"
	case "missing_target_repo":
		return "target_repo"
	case "missing_verification_method":
		return "verification_method"
	case "missing_risk_level":
		return "risk_level"
	case "missing_takeover_fields":
		return "takeover_fields"
	default:
		return ""
	}
}

func jiraMissingFieldTemplate() (string, string) {
	const fallback = "# Jira 卡片信息缺失\n\nAgenticOps 无法继续接管该任务，因为 Jira 卡片缺少必要信息。\n\n- 当前操作：`<operation>`\n- 缺失字段：\n<missing_fields>\n\n## 补充建议\n\n<missing_field_guidance>\n\n## AIAgent 项目分析建议\n\n<admission_suggestions>\n\n请一次性补齐以上字段，或维护工作流配置中的字段映射。\n"
	root, err := repoRoot()
	if err != nil {
		return "", fallback
	}
	path := filepath.Join(repoBasicResourcesPath(root), "templates", "jira-missing-field.md")
	data, err := os.ReadFile(path)
	if err != nil {
		return "", fallback
	}
	return path, string(data)
}

type fieldGuidance struct {
	Field       string `json:"field"`
	Label       string `json:"label"`
	Location    string `json:"location"`
	Example     string `json:"example"`
	Description string `json:"description"`
}

func fieldGuidancePayload(fields []string) []fieldGuidance {
	guidance := make([]fieldGuidance, 0, len(fields))
	for _, field := range fields {
		guidance = append(guidance, guidanceForField(field))
	}
	return guidance
}

func markdownFieldList(fields []string) string {
	var lines []string
	for _, field := range fields {
		lines = append(lines, "- `"+field+"`："+guidanceForField(field).Label)
	}
	return strings.Join(lines, "\n")
}

func missingFieldGuidance(fields []string) string {
	var sections []string
	for _, field := range fields {
		guidance := guidanceForField(field)
		sections = append(sections, "- "+guidance.Label+"：建议填写在 "+guidance.Location+"。示例："+guidance.Example)
	}
	return strings.Join(sections, "\n")
}

func guidanceForField(field string) fieldGuidance {
	switch field {
	case "problem_branch":
		return fieldGuidance{Field: field, Label: "问题分支", Location: "Jira 描述 `问题分支` 章节或对应项目字段", Example: "develop", Description: "说明缺陷在哪个分支可以复现或被发现"}
	case "target_branch":
		return fieldGuidance{Field: field, Label: "修复分支", Location: "Jira 描述 `修复分支` 章节或对应项目字段", Example: "develop", Description: "说明本次修复应在哪个分支完成并验证"}
	case "problem_summary":
		return fieldGuidance{Field: field, Label: "问题现象", Location: "Jira 摘要、Jira 描述 `问题现象` 章节或对应项目字段", Example: "TM 启动时持续输出 Elasticsearch health check refused 告警", Description: "说明用户、日志或系统实际观察到的问题现象"}
	case "reproduction_path":
		return fieldGuidance{Field: field, Label: "复现路径", Location: "Jira 描述 `复现路径` 章节或对应项目字段", Example: "启动 TM，观察启动日志", Description: "可选；说明问题如何复现，无法稳定复现时写明已知触发条件"}
	case "acceptance_criteria":
		return fieldGuidance{Field: field, Label: "验收标准", Location: "Jira 描述 `验收标准` 章节或对应项目字段", Example: "启动 TM 不再持续输出 Elasticsearch health check refused 告警，相关回归验证通过", Description: "说明缺陷修复后怎样判断可以验收"}
	case "target_repo":
		return fieldGuidance{Field: field, Label: "目标仓库", Location: "Jira 组件、标签、项目仓库映射或对应项目字段", Example: "tapdata/tapdata", Description: "说明本次任务应修改或检查的代码仓库"}
	case "verification_method":
		return fieldGuidance{Field: field, Label: "验证方式", Location: "Jira 验证方式字段或对应项目字段", Example: "go test ./... 或指定模块测试命令", Description: "说明 AIAgent 完成修复后必须执行的验证命令或人工验证方式"}
	case "risk_level":
		return fieldGuidance{Field: field, Label: "风险等级", Location: "Jira 风险字段或风险标签", Example: "T3", Description: "说明任务风险级别，便于决定人工门禁和验证强度"}
	default:
		return fieldGuidance{Field: field, Label: field, Location: "Jira 卡片对应字段", Example: "<请按项目规范补充>", Description: "接管所需字段"}
	}
}

func runResumeTakeover(args []string, stdout io.Writer) int {
	runID := readFlag(args, "--run-id", "")
	if runID == "" {
		return writeJSON(stdout, output.Failure("resume_takeover", "missing_run_id", "缺少 run_id", "请提供 --run-id"))
	}
	workspaceName := workspaceNameFromArgsOrAgentConfig(args, "default")
	root, err := workspaceRoot()
	if err != nil {
		return writeJSON(stdout, output.Failure("resume_takeover", "workspace_root_failed", "无法读取当前工作目录", "请在项目 AI 工作空间中重试"))
	}
	state, err := resumableRunState(root, workspaceName, runID)
	if err != nil {
		return writeJSON(stdout, output.FailureWithContext("resume_takeover", output.FailureContext{
			Code:                resumeErrorCode(err),
			Message:             err.Error(),
			RequiredHumanAction: "请检查 run_id、workspace 和本地事件日志是否对应同一次有效接管",
			TaskType:            "task_takeover",
			CurrentStage:        "resume_gate",
			NextAction:          "ask_owner",
		}))
	}
	if err := appendWorkspaceEventWithDetails(workspaceName, feedback.Event{
		RunID:          runID,
		IssueKey:       state.IssueKey,
		TaskType:       "task_takeover",
		Operation:      "resume_takeover",
		CurrentStage:   "takeover_resumed",
		NextAction:     "continue_development",
		AgentID:        state.AgentID,
		CurrentAgentID: state.CurrentAgentID,
		TaskClass:      state.TaskClass,
		ProcessID:      state.ProcessID,
		OK:             true,
		Gate:           "resume_takeover",
		GateStatus:     "passed",
	}); err != nil {
		return writeJSON(stdout, output.Failure("resume_takeover", "event_write_failed", err.Error(), "请检查工作空间目录权限"))
	}
	return writeJSON(stdout, output.Success("resume_takeover", map[string]any{
		"workspace":        workspaceName,
		"run_id":           runID,
		"issue_key":        state.IssueKey,
		"task_type":        "task_takeover",
		"agent_id":         state.AgentID,
		"current_agent_id": state.CurrentAgentID,
		"task_class":       state.TaskClass,
		"process_id":       state.ProcessID,
		"previous_stage":   state.PreviousStage,
		"current_stage":    "takeover_resumed",
		"next_action":      "continue_development",
	}))
}

type resumeRunState struct {
	IssueKey       string
	AgentID        string
	CurrentAgentID string
	TaskClass      string
	ProcessID      string
	PreviousStage  string
}

var errResumeRunNotFound = errors.New("run_not_found")

var errResumeWorkspaceMismatch = errors.New("workspace_mismatch")

var errResumeLocalStateMismatch = errors.New("local_state_mismatch")

func resumableRunState(root string, workspaceName string, runID string) (resumeRunState, error) {
	events, err := feedback.ReadEvents(filepath.Join(root, ".agentic-ops", "feedback", "events.ndjson"))
	if err != nil {
		return resumeRunState{}, err
	}
	var latest *feedback.Event
	for i := range events {
		if events[i].RunID == runID && (events[i].Operation == "takeover_task" || events[i].Operation == "resume_takeover") {
			latest = &events[i]
		}
	}
	if latest == nil {
		return resumeRunState{}, errResumeRunNotFound
	}
	if latest.Workspace != workspaceName {
		return resumeRunState{}, errResumeWorkspaceMismatch
	}
	if latest.IssueKey == "" ||
		latest.AgentID == "" ||
		latest.CurrentAgentID == "" ||
		latest.CurrentAgentID != latest.AgentID ||
		latest.CurrentAgentID != agentID() ||
		latest.TaskClass == "" ||
		latest.ProcessID == "" ||
		latest.CurrentStage == "" ||
		!latest.OK ||
		latest.CurrentStage == "completed" ||
		latest.NextAction == "task_audit_submitted" {
		return resumeRunState{}, errResumeLocalStateMismatch
	}
	return resumeRunState{
		IssueKey:       latest.IssueKey,
		AgentID:        latest.AgentID,
		CurrentAgentID: latest.CurrentAgentID,
		TaskClass:      latest.TaskClass,
		ProcessID:      latest.ProcessID,
		PreviousStage:  latest.CurrentStage,
	}, nil
}

func resumeErrorCode(err error) string {
	switch err {
	case errResumeRunNotFound:
		return "run_not_found"
	case errResumeWorkspaceMismatch:
		return "workspace_mismatch"
	case errResumeLocalStateMismatch:
		return "local_state_mismatch"
	default:
		return "event_read_failed"
	}
}

func takeoverProfile(workspaceName string) profile.Profile {
	root, _ := workspaceRoot()
	if loadedProfile, err := resolveEffectiveProfile(workspaceName, root); err == nil {
		return loadedProfile
	}
	return profile.Profile{
		Workspace: workspaceName,
		TaskClassMapping: profile.TaskClassMapping{
			IssueTypes: map[string]string{
				"Story": "feature_change",
				"Bug":   "bug_fix",
				"Task":  "technical_task",
			},
		},
		StandardProcessMapping: map[string]string{
			"feature_change":      "development_change_v1",
			"bug_fix":             "development_change_v1",
			"technical_task":      "development_change_v1",
			"investigation":       "investigation_v1",
			"process_improvement": "agenticops_improvement_v1",
		},
		StatusMapping: map[string]string{
			"To Do":       "waiting_takeover",
			"In Progress": "implementation",
			"Done":        "completed",
		},
		TransitionMapping: map[string]string{
			"complete": "completed",
		},
		JiraTransitionMapping: map[string]profile.JiraTransition{
			"complete": {Name: "Done"},
		},
	}
}
