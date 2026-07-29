package clihandlers

import (
	"context"
	"github.com/tapstate/agentic-ops/packages/agentic-cli/internal/feedback"
	"github.com/tapstate/agentic-ops/packages/agentic-cli/internal/jira"
	"github.com/tapstate/agentic-ops/packages/agentic-cli/internal/output"
	"github.com/tapstate/agentic-ops/packages/agentic-cli/internal/process"
	"github.com/tapstate/agentic-ops/packages/agentic-cli/internal/profile"
	"github.com/tapstate/agentic-ops/packages/agentic-cli/internal/runcontext"
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

func runInspectTask(args []string, stdout io.Writer) int {
	if len(args) < 2 {
		return writeJSON(stdout, output.Failure("inspect_task", "missing_issue_key", "缺少 Jira 卡片编号", "请提供 Jira 卡片编号"))
	}
	workspaceName := workspaceNameFromArgsOrAgentConfig(args, "default")
	issueKey := args[1]
	workspaceProfile := takeoverProfile(workspaceName)
	selection, err := selectJiraClient(workspaceName, workspaceProfile)
	if err != nil {
		return writeJSON(stdout, output.Failure("inspect_task", "jira_adapter_config_failed", err.Error(), "请检查 Jira 适配器配置"))
	}
	issue, ok, err := selection.Client.GetIssueByKey(context.Background(), workspaceName, issueKey)
	if err != nil {
		return writeJSON(stdout, output.Failure("inspect_task", "jira_issue_read_failed", err.Error(), "请检查 Jira 适配器配置和卡片权限"))
	}
	if !ok {
		return writeJSON(stdout, output.Failure("inspect_task", "issue_not_found", "未找到 Jira 卡片", "请检查 Jira 卡片编号"))
	}
	currentJiraUser, err := selection.Client.CurrentUser(context.Background())
	if err != nil {
		return writeJSON(stdout, output.Failure("inspect_task", "jira_current_user_failed", err.Error(), "请检查 Jira 适配器登录状态"))
	}
	processRegistry, err := repoProcessRegistry()
	if err != nil {
		processRegistry = defaultProcessRegistry()
	}
	gateFacts := inspectTaskGateFacts(issue, workspaceProfile, currentJiraUser, agentID(), processRegistry)
	comments := issue.Comments
	if comments == nil {
		comments = []jira.Comment{}
	}
	return writeJSON(stdout, output.Success("inspect_task", map[string]any{
		"workspace":               workspaceName,
		"issue_key":               issue.Key,
		"issue":                   issue,
		"current_jira_user":       currentJiraUser,
		"target_repo":             gateFacts["target_repo"],
		"form_values":             issueFormValues(issue, workspaceProfile),
		"comments":                comments,
		"gate_facts":              gateFacts,
		"asset_refs":              projectAssetRefs(workspaceName, workspaceProfile),
		"recommended_next_action": "inspect_by_agent",
	}))
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
		_ = appendWorkspaceEventWithDetails(workspaceName, feedback.Event{
			IssueKey:            issue.Key,
			TaskType:            "task_takeover",
			Operation:           "takeover_task",
			CurrentStage:        decision.CurrentStage,
			NextAction:          decision.NextAction,
			OK:                  false,
			Code:                decision.Code,
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
		ownershipComment := jiraTakeoverComment(workspaceProfile, runID, currentAgentID, takeoverAt)
		if len(fields) == 0 && ownershipComment == "" {
			_ = appendRealJiraWriteGateEvent(workspaceName, runID, issue.Key, "takeover_task", "takeover_gate", "ask_owner", "missing_jira_write_mapping", false, true)
			return writeJSON(stdout, output.Failure("takeover_task", "missing_jira_write_mapping", "缺少 current_agent_id 或 takeover_at 字段映射", "请维护 workflow profile 的所有权字段映射"))
		}
		if len(fields) > 0 {
			if err := selection.Client.UpdateFields(context.Background(), issue.Key, fields); err != nil {
				_ = appendRealJiraWriteGateEvent(workspaceName, runID, issue.Key, "takeover_task", "takeover_gate", "ask_owner", "jira_takeover_write_failed", false, false)
				return writeJSON(stdout, output.Failure("takeover_task", "jira_takeover_write_failed", err.Error(), "请检查 Jira 字段权限和 policy gate"))
			}
		}
		if ownershipComment != "" {
			if err := selection.Client.AddComment(context.Background(), issue.Key, ownershipComment); err != nil {
				_ = appendRealJiraWriteGateEvent(workspaceName, runID, issue.Key, "takeover_task", "takeover_gate", "ask_owner", "jira_takeover_write_failed", false, false)
				return writeJSON(stdout, output.Failure("takeover_task", "jira_takeover_write_failed", err.Error(), "请检查 Jira 评论权限和 policy gate"))
			}
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

func inspectTaskGateFacts(issue jira.Issue, p profile.Profile, currentUser string, currentAgentID string, registry map[string]process.Process) map[string]any {
	taskClass, taskClassSource := inspectTaskClass(issue, p)
	processID := p.StandardProcessMapping[taskClass]
	mappedStage, statusMapped := p.StatusMapping[issue.Status]
	processEntryStage := ""
	processRegistered := false
	if process, ok := registry[processID]; ok {
		processRegistered = true
		processEntryStage = process.EntryStage
	}
	targetRepo := inspectTargetRepo(issue, p)
	return map[string]any{
		"owner":                                 issue.Owner,
		"assignee":                              issue.Assignee,
		"current_agent_id":                      issue.CurrentAgentID,
		"owner_matches_current_user":            issue.Owner != "" && issue.Owner == currentUser,
		"assignee_matches_current_user":         issue.Assignee != "" && issue.Assignee == currentUser,
		"current_agent_id_empty_or_match":       issue.CurrentAgentID == "" || issue.CurrentAgentID == currentAgentID,
		"task_class":                            taskClass,
		"task_class_source":                     taskClassSource,
		"standard_process_id":                   processID,
		"standard_process_registered":           processRegistered,
		"status":                                issue.Status,
		"status_mapped":                         statusMapped,
		"mapped_stage":                          mappedStage,
		"process_entry_stage":                   processEntryStage,
		"mapped_stage_matches_process_entry":    mappedStage != "" && processEntryStage != "" && mappedStage == processEntryStage,
		"target_repo":                           targetRepo,
		"recommended_takeover_operation":        "takeover_task",
		"requires_real_jira_write_confirmation": true,
	}
}

func inspectTaskClass(issue jira.Issue, p profile.Profile) (string, string) {
	if taskClass := p.TaskClassMapping.IssueTypes[issue.IssueType]; taskClass != "" {
		return taskClass, "issue_type:" + issue.IssueType
	}
	for _, label := range issue.Labels {
		if taskClass := p.TaskClassMapping.Labels[label]; taskClass != "" {
			return taskClass, "label:" + label
		}
	}
	for _, component := range issue.Components {
		if taskClass := p.TaskClassMapping.Components[component]; taskClass != "" {
			return taskClass, "component:" + component
		}
	}
	return "", ""
}

func inspectTargetRepo(issue jira.Issue, p profile.Profile) string {
	if issue.TargetRepo != "" {
		return issue.TargetRepo
	}
	if value := issue.FormValues["target_repo"]; value != "" {
		return value
	}
	for _, component := range issue.Components {
		if repo := p.GitHub.Repositories.ByComponent[component]; repo != "" {
			return repo
		}
	}
	for _, label := range issue.Labels {
		if repo := p.GitHub.Repositories.ByLabel[label]; repo != "" {
			return repo
		}
	}
	if repo := p.GitHub.Repositories.ByIssueType[issue.IssueType]; repo != "" {
		return repo
	}
	return p.GitHub.Repositories.Default
}

func issueFormValues(issue jira.Issue, p profile.Profile) map[string]string {
	values := map[string]string{}
	for name := range p.JiraFormMapping.Fields {
		values[name] = issue.FormValues[name]
	}
	return values
}

func projectAssetRefs(workspaceName string, p profile.Profile) map[string]any {
	projectBase := filepath.ToSlash(filepath.Join("install-resources", "basic", "projects", workspaceName))
	standards := make([]string, 0, len(p.Standards))
	for _, standard := range p.Standards {
		standards = append(standards, filepath.ToSlash(filepath.Join("install-resources", "basic", standard)))
	}
	return map[string]any{
		"project_profile": projectBase + "/profile.yaml",
		"standards":       standards,
		"admission_dir":   projectBase + "/admission",
		"runbooks_dir":    projectBase + "/runbooks",
		"templates_dir":   projectBase + "/templates",
		"tools":           projectBase + "/tools.yaml",
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
	state, err := runcontext.ReadFile(
		filepath.Join(root, ".agentic-ops", "feedback", "events.ndjson"),
		runcontext.Query{RunID: runID, Workspace: workspaceName, AgentID: agentID()},
	)
	if err != nil {
		return writeJSON(stdout, output.FailureWithContext("resume_takeover", output.FailureContext{
			Code:                runcontext.ErrorCode(err),
			Message:             err.Error(),
			RequiredHumanAction: "请检查 run_id、workspace 和本地事件日志是否对应同一次有效接管",
			TaskType:            "task_takeover",
			CurrentStage:        "resume_gate",
			NextAction:          "ask_owner",
		}))
	}
	workspaceProfile := takeoverProfile(workspaceName)
	operationContract, err := repoOperationContract("resume_takeover")
	if err != nil {
		return writeJSON(stdout, output.FailureWithContext("resume_takeover", output.FailureContext{
			Code:                operationContractErrorCode(err),
			Message:             err.Error(),
			RequiredHumanAction: "请检查 resume-takeover 操作契约资源",
			TaskType:            "task_takeover",
			CurrentStage:        "resume_gate",
			NextAction:          "ask_owner",
		}))
	}
	processRegistry, err := repoProcessRegistry()
	if err != nil {
		processRegistry = defaultProcessRegistry()
	}
	selection, err := selectJiraClient(workspaceName, workspaceProfile)
	if err != nil {
		return writeJSON(stdout, output.Failure("resume_takeover", "jira_adapter_config_failed", err.Error(), "请检查 Jira adapter 配置"))
	}
	issue, ok, err := selection.Client.GetIssueByKey(context.Background(), workspaceName, state.IssueKey)
	if err != nil {
		return writeJSON(stdout, output.Failure("resume_takeover", "jira_issue_read_failed", err.Error(), "请检查 Jira adapter 配置和卡片权限"))
	}
	if !ok {
		return writeJSON(stdout, output.Failure("resume_takeover", "issue_not_found", "未找到 Jira 卡片", "请检查 run_id 对应的 Jira 卡片"))
	}
	currentJiraUser, err := selection.Client.CurrentUser(context.Background())
	if err != nil {
		return writeJSON(stdout, output.Failure("resume_takeover", "jira_current_user_failed", err.Error(), "请检查 Jira adapter 登录状态"))
	}
	decision := jira.ValidateResume(jira.ResumeInput{
		Context:         state,
		Issue:           issue,
		CurrentUser:     currentJiraUser,
		AgentID:         agentID(),
		AdapterMode:     selection.Mode,
		Profile:         workspaceProfile,
		Contract:        operationContract,
		ProcessRegistry: processRegistry,
	})
	if !decision.OK {
		jiraFeedback, feedbackErr := writeResumeFeedback(root, state, decision)
		if feedbackErr != nil {
			return writeJSON(stdout, output.FailureWithContext("resume_takeover", output.FailureContext{
				Code:                "feedback_write_failed",
				Message:             feedbackErr.Error(),
				RequiredHumanAction: "请检查工作空间 runs 目录权限",
				TaskType:            "task_takeover",
				CurrentStage:        state.CurrentStage,
				NextAction:          "ask_owner",
			}))
		}
		nextAction := "ask_owner"
		if jiraFeedback.Required {
			nextAction = jiraFeedback.NextAction
		}
		_ = appendWorkspaceEventWithDetails(workspaceName, feedback.Event{
			RunID:               runID,
			IssueKey:            state.IssueKey,
			TaskType:            "task_takeover",
			Operation:           "resume_takeover",
			CurrentStage:        "resume_gate",
			NextAction:          nextAction,
			AgentID:             state.AgentID,
			CurrentAgentID:      state.CurrentAgentID,
			TargetRepo:          state.TargetRepo,
			TaskClass:           state.TaskClass,
			ProcessID:           state.ProcessID,
			OK:                  false,
			Code:                decision.Code,
			Gate:                "resume_takeover",
			GateStatus:          "blocked",
			HumanGate:           true,
			RequiresHumanAction: true,
		})
		result := output.FailureWithContext("resume_takeover", output.FailureContext{
			Code:                decision.Code,
			Message:             decision.Message,
			RequiredHumanAction: decision.RequiredHumanAction,
			TaskType:            "task_takeover",
			CurrentStage:        state.CurrentStage,
			NextAction:          nextAction,
		})
		result["workspace"] = workspaceName
		result["run_id"] = runID
		result["issue_key"] = state.IssueKey
		result["task_class"] = state.TaskClass
		result["process_id"] = state.ProcessID
		result["target_repo"] = state.TargetRepo
		if decision.StandardProcessStage != "" {
			result["standard_process_stage"] = decision.StandardProcessStage
		}
		if jiraFeedback.Required {
			result["jira_feedback_required"] = true
			result["jira_feedback_write_allowed"] = jiraFeedback.WriteAllowed
			result["jira_feedback_file"] = jiraFeedback.File
			result["jira_feedback_category"] = jiraFeedback.Category
		}
		return writeJSON(stdout, result)
	}
	if err := appendWorkspaceEventWithDetails(workspaceName, feedback.Event{
		RunID:          runID,
		IssueKey:       state.IssueKey,
		TaskType:       "task_takeover",
		Operation:      "resume_takeover",
		CurrentStage:   state.CurrentStage,
		NextAction:     state.NextAction,
		AgentID:        state.AgentID,
		CurrentAgentID: state.CurrentAgentID,
		TargetRepo:     decision.TargetRepo,
		TaskClass:      state.TaskClass,
		ProcessID:      state.ProcessID,
		OK:             true,
		Gate:           "resume_takeover",
		GateStatus:     "passed",
	}); err != nil {
		return writeJSON(stdout, output.Failure("resume_takeover", "event_write_failed", err.Error(), "请检查工作空间目录权限"))
	}
	return writeJSON(stdout, output.Success("resume_takeover", map[string]any{
		"workspace":              workspaceName,
		"run_id":                 runID,
		"issue_key":              state.IssueKey,
		"task_type":              "task_takeover",
		"agent_id":               state.AgentID,
		"current_agent_id":       state.CurrentAgentID,
		"task_class":             state.TaskClass,
		"process_id":             state.ProcessID,
		"target_repo":            decision.TargetRepo,
		"previous_stage":         state.CurrentStage,
		"current_stage":          state.CurrentStage,
		"standard_process_stage": decision.StandardProcessStage,
		"next_action":            state.NextAction,
	}))
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
