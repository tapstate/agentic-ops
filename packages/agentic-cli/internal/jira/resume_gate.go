package jira

import (
	"github.com/tapstate/agentic-ops/packages/agentic-cli/internal/contract"
	"github.com/tapstate/agentic-ops/packages/agentic-cli/internal/process"
	"github.com/tapstate/agentic-ops/packages/agentic-cli/internal/profile"
	"github.com/tapstate/agentic-ops/packages/agentic-cli/internal/runcontext"
)

type ResumeInput struct {
	Context         runcontext.Context
	Issue           Issue
	CurrentUser     string
	AgentID         string
	AdapterMode     string
	Profile         profile.Profile
	Contract        contract.Operation
	ProcessRegistry map[string]process.Process
}

type ResumeDecision struct {
	OK                       bool
	Code                     string
	Message                  string
	RequiredHumanAction      string
	StandardProcessStage     string
	TargetRepo               string
	JiraFeedbackRequired     bool
	JiraFeedbackWriteAllowed bool
}

func ValidateResume(input ResumeInput) ResumeDecision {
	if input.Context.Terminal {
		return blockedResume(
			"terminal_run",
			"当前 run 已经完成",
			"请检查任务审计记录，不要恢复已完成的 run",
			false,
			false,
		)
	}
	if input.Context.HumanGatePending {
		return blockedResume(
			"human_gate_pending",
			"最近任务状态仍在等待人工确认",
			"请先完成对应人工确认并记录结论，再继续任务",
			true,
			resumeFeedbackWriteAllowed(input),
		)
	}
	if !containsResumeValue(input.Contract.AllowedStages, input.Context.CurrentStage) {
		return blockedResume(
			"resume_stage_not_allowed",
			"最近操作阶段不允许执行恢复："+input.Context.CurrentStage,
			"请按 resume-takeover 操作契约确认允许恢复的阶段",
			true,
			resumeFeedbackWriteAllowed(input),
		)
	}
	if input.Issue.Key != input.Context.IssueKey {
		return blockedResume(
			"issue_mismatch",
			"当前 Jira 卡片与 run_id 中的卡片不一致",
			"请检查 run_id 和 Jira 卡片是否来自同一次接管",
			false,
			false,
		)
	}
	if input.AdapterMode == "real" {
		if input.Issue.Assignee != input.CurrentUser {
			return blockedResume(
				"assignee_changed",
				"当前 Jira assignee 已不是当前用户",
				"请研发工程师确认任务当前负责人，并由当前负责人决定是否继续",
				true,
				false,
			)
		}
		if input.Issue.CurrentAgentID == "" {
			return blockedResume(
				"agent_binding_lost",
				"Jira 上的 AIAgent 绑定已丢失",
				"请研发工程师确认绑定为何被清空，再决定是否重新接管",
				true,
				true,
			)
		}
		if input.Issue.CurrentAgentID != input.AgentID {
			return blockedResume(
				"agent_ownership_conflict",
				"当前 Jira 卡片已绑定其他 AIAgent",
				"请研发工程师确认当前代理所有权，当前 AIAgent 不得自动抢回绑定",
				true,
				false,
			)
		}
	}
	targetRepo := targetRepoFor(input.Issue, input.Profile)
	if targetRepo == "" {
		return blockedResume(
			"target_repo_missing",
			"无法从当前 Jira 卡片或项目 profile 确定目标仓库",
			"请补充 Jira 目标仓库信息或维护项目仓库映射",
			true,
			true,
		)
	}
	if input.Context.TargetRepo != "" && input.Context.TargetRepo != targetRepo {
		return blockedResume(
			"target_repo_changed",
			"当前目标仓库与接管时不一致",
			"请研发工程师确认范围变化，结束旧接管后按新目标重新接管",
			true,
			true,
		)
	}
	registeredProcess, ok := input.ProcessRegistry[input.Context.ProcessID]
	if !ok {
		return blockedResume(
			"standard_process_not_found",
			"Standard Process Registry 中不存在历史流程："+input.Context.ProcessID,
			"请维护标准流程注册处，或由流程负责人确认正确流程",
			true,
			true,
		)
	}
	if !containsResumeValue(registeredProcess.TaskClasses, input.Context.TaskClass) {
		return blockedResume(
			"task_class_process_mismatch",
			"历史任务分类不属于对应标准流程",
			"请流程负责人核对 task_class 和 process_id 映射",
			true,
			true,
		)
	}
	standardProcessStage, ok := input.Profile.StatusMapping[input.Issue.Status]
	if !ok || standardProcessStage == "" {
		return blockedResume(
			"lifecycle_mapping_gap",
			"当前 Jira 状态无法映射为标准流程阶段",
			"请维护项目 profile 的 status_mapping",
			true,
			true,
		)
	}
	if !registeredProcess.HasStage(standardProcessStage) {
		return blockedResume(
			"invalid_process_stage",
			"当前 Jira 映射阶段不属于历史标准流程："+standardProcessStage,
			"请核对 Jira 状态映射和 Standard Process Registry",
			true,
			true,
		)
	}
	if standardProcessStage == "completed" {
		return blockedResume(
			"terminal_run",
			"当前 Jira 卡片已经进入完成阶段",
			"请检查任务审计记录，不要恢复已完成的任务",
			false,
			false,
		)
	}
	return ResumeDecision{
		OK:                   true,
		StandardProcessStage: standardProcessStage,
		TargetRepo:           targetRepo,
	}
}

func blockedResume(code string, message string, action string, feedbackRequired bool, feedbackWriteAllowed bool) ResumeDecision {
	return ResumeDecision{
		Code:                     code,
		Message:                  message,
		RequiredHumanAction:      action,
		JiraFeedbackRequired:     feedbackRequired,
		JiraFeedbackWriteAllowed: feedbackWriteAllowed,
	}
}

func containsResumeValue(values []string, want string) bool {
	for _, value := range values {
		if value == want {
			return true
		}
	}
	return false
}

func resumeFeedbackWriteAllowed(input ResumeInput) bool {
	if input.AdapterMode != "real" {
		return true
	}
	return input.Issue.Assignee == input.CurrentUser &&
		(input.Issue.CurrentAgentID == "" || input.Issue.CurrentAgentID == input.AgentID)
}
