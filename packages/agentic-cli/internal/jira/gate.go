package jira

import (
	"github.com/tapstate/agentic-ops/packages/agentic-cli/internal/process"
	"github.com/tapstate/agentic-ops/packages/agentic-cli/internal/profile"
)

type TakeoverDecision struct {
	OK                  bool
	Code                string
	Message             string
	RequiredHumanAction string
	TaskClass           string
	TaskClassSource     string
	ProcessID           string
	TargetRepo          string
	CurrentStage        string
	NextAction          string
}

func ValidateTakeover(issue Issue, p profile.Profile, currentUser string, agentID string) TakeoverDecision {
	return validateTakeover(issue, p, currentUser, agentID, nil)
}

func ValidateTakeoverWithProcesses(issue Issue, p profile.Profile, currentUser string, agentID string, registry map[string]process.Process) TakeoverDecision {
	return validateTakeover(issue, p, currentUser, agentID, registry)
}

func ValidateTakeoverEntryWithProcesses(issue Issue, p profile.Profile, currentUser string, agentID string, registry map[string]process.Process) TakeoverDecision {
	return validateTakeover(issue, p, currentUser, agentID, registry)
}

func validateTakeover(issue Issue, p profile.Profile, currentUser string, agentID string, registry map[string]process.Process) TakeoverDecision {
	if issue.Owner == "" || issue.Owner != currentUser {
		return blocked("owner_mismatch", "当前研发负责人与 Jira 卡片负责人不匹配", "请确认当前用户是否为该 Jira 卡片的研发负责人")
	}
	if issue.Assignee == "" || issue.Assignee != currentUser {
		return blocked("assignee_mismatch", "当前 Jira assignee 与当前用户不匹配", "请把 Jira assignee 调整为当前研发负责人后重试")
	}
	if issue.CurrentAgentID != "" && issue.CurrentAgentID != agentID {
		return blocked("agent_ownership_conflict", "当前 Jira 卡片已绑定其他 AIAgent", "请研发负责人确认是否释放当前代理绑定")
	}
	taskClass, taskClassSource := taskClassFor(issue, p)
	if taskClass == "" {
		return blocked("task_class_mapping_gap", "Jira 卡片类型或标签无法映射到标准任务分类", "请维护工作流配置的 task_class_mapping")
	}
	processID := p.StandardProcessMapping[taskClass]
	if processID == "" {
		return blocked("standard_process_mapping_gap", "标准任务分类无法映射到标准流程", "请维护工作流配置的 standard_process_mapping")
	}
	mappedStage, ok := p.StatusMapping[issue.Status]
	if !ok {
		return blocked("unknown_jira_status", "当前 Jira 状态未配置映射", "请维护工作流配置的 status_mapping")
	}
	if registry != nil {
		registeredProcess, ok := registry[processID]
		if !ok {
			return blocked("standard_process_mapping_gap", "标准流程注册处缺少任务分类对应流程", "请维护 install-resources/basic/contracts/processes 中的标准流程定义")
		}
		if mappedStage != registeredProcess.EntryStage {
			return blocked("invalid_takeover_stage", "当前 Jira 状态不允许作为接管入口", "请把 Jira 卡片调整到可接管状态，或维护 workflow profile 和标准流程入口阶段")
		}
	}
	targetRepo := targetRepoFor(issue, p)
	return TakeoverDecision{
		OK:              true,
		TaskClass:       taskClass,
		TaskClassSource: taskClassSource,
		ProcessID:       processID,
		TargetRepo:      targetRepo,
		CurrentStage:    "takeover_started",
		NextAction:      "proceed",
	}
}

func taskClassFor(issue Issue, p profile.Profile) (string, string) {
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

func targetRepoFor(issue Issue, p profile.Profile) string {
	if issue.TargetRepo != "" {
		return issue.TargetRepo
	}
	if issue.FormValues != nil && issue.FormValues["target_repo"] != "" {
		return issue.FormValues["target_repo"]
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

func blocked(code string, message string, requiredHumanAction string) TakeoverDecision {
	return TakeoverDecision{
		OK:                  false,
		Code:                code,
		Message:             message,
		RequiredHumanAction: requiredHumanAction,
		CurrentStage:        "takeover_gate",
		NextAction:          "ask_owner",
	}
}
