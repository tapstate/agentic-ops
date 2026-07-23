package jira

import "github.com/tapstate/agentic-ops/packages/agentic-cli/internal/profile"

type TakeoverDecision struct {
	OK                  bool
	Code                string
	Message             string
	RequiredHumanAction string
	TaskClass           string
	ProcessID           string
	CurrentStage        string
	NextAction          string
}

func ValidateTakeover(issue Issue, p profile.Profile, currentUser string, agentID string) TakeoverDecision {
	if issue.Owner == "" || issue.Owner != currentUser {
		return blocked("owner_mismatch", "当前研发 owner 与 Jira issue owner 不匹配", "请确认当前用户是否为该 Jira issue 的研发 owner")
	}
	if issue.Assignee == "" || issue.Assignee != currentUser {
		return blocked("assignee_mismatch", "当前 Jira assignee 与当前用户不匹配", "请把 Jira assignee 调整为当前研发 owner 后重试")
	}
	if issue.CurrentAgentID != "" && issue.CurrentAgentID != agentID {
		return blocked("agent_ownership_conflict", "当前 Jira issue 已绑定其他 AIAgent", "请研发 owner 确认是否释放当前代理绑定")
	}
	taskClass := taskClassFor(issue, p)
	if taskClass == "" {
		return blocked("task_class_mapping_gap", "Jira issue 类型或 label 无法映射到标准任务分类", "请维护 workflow profile 的 task_class_mapping")
	}
	processID := p.StandardProcessMapping[taskClass]
	if processID == "" {
		return blocked("standard_process_mapping_gap", "标准任务分类无法映射到标准流程", "请维护 workflow profile 的 standard_process_mapping")
	}
	if _, ok := p.StatusMapping[issue.Status]; !ok {
		return blocked("unknown_jira_status", "当前 Jira 状态未配置映射", "请维护 workflow profile 的 status_mapping")
	}
	if issue.AcceptanceCriteria == "" {
		return blocked("missing_acceptance_criteria", "Jira issue 缺少验收标准", "请在 Jira 卡片补充验收标准")
	}
	if issue.TargetRepo == "" {
		return blocked("missing_target_repo", "Jira issue 缺少目标仓库信息", "请在 Jira 卡片补充目标仓库，或维护 workspace repo 映射")
	}
	if issue.VerificationMethod == "" {
		return blocked("missing_verification_method", "Jira issue 缺少验证方式", "请在 Jira 卡片补充验证方式")
	}
	if issue.RiskLevel == "" {
		return blocked("missing_risk_level", "Jira issue 缺少风险等级", "请在 Jira 卡片补充风险等级")
	}
	return TakeoverDecision{
		OK:           true,
		TaskClass:    taskClass,
		ProcessID:    processID,
		CurrentStage: "takeover_started",
		NextAction:   "proceed",
	}
}

func taskClassFor(issue Issue, p profile.Profile) string {
	if taskClass := p.TaskClassMapping.IssueTypes[issue.IssueType]; taskClass != "" {
		return taskClass
	}
	return ""
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
