package tasklifecycle

import (
	"context"
	"errors"
	"time"

	"github.com/tapstate/agentic-ops/packages/agentic-cli/internal/feedback"
	"github.com/tapstate/agentic-ops/packages/agentic-cli/internal/jira"
	"github.com/tapstate/agentic-ops/packages/agentic-cli/internal/profile"
)

func (runner TaskLifecycleRunner) Run(ctx context.Context, request Request) Result {
	result := Result{
		OK:           false,
		Workspace:    request.Workspace,
		IssueKey:     request.IssueKey,
		AgentID:      runner.AgentID,
		TaskType:     TaskType,
		CurrentStage: "takeover_gate",
		NextAction:   "ask_owner",
	}
	if err := runner.validate(); err != nil {
		result.Code = "task_lifecycle_runner_invalid"
		result.Message = err.Error()
		result.RequiredHumanAction = "请检查 AgenticCLI 运行时配置"
		return result
	}
	issue, ok, err := runner.Client.GetIssueByKey(ctx, request.Workspace, request.IssueKey)
	if err != nil {
		return result.blocked("jira_issue_read_failed", err.Error(), "请检查 Jira 适配器配置和卡片权限")
	}
	if !ok {
		return result.blocked("issue_not_found", "未找到 Jira 卡片", "请检查 Jira 卡片编号")
	}
	currentJiraUser, err := runner.Client.CurrentUser(ctx)
	if err != nil {
		return result.blocked("jira_current_user_failed", err.Error(), "请检查 Jira 适配器登录状态")
	}

	decision := jira.ValidateTakeoverWithProcesses(issue, runner.Profile, currentJiraUser, runner.AgentID, runner.ProcessRegistry)
	result.IssueKey = issue.Key
	result.TaskClass = decision.TaskClass
	result.TaskClassSource = decision.TaskClassSource
	result.ProcessID = decision.ProcessID
	result.TargetRepo = decision.TargetRepo
	if !decision.OK {
		result.CurrentStage = decision.CurrentStage
		result.NextAction = decision.NextAction
		blocked := result.blocked(decision.Code, decision.Message, decision.RequiredHumanAction)
		_ = runner.appendEvent(blocked, "takeover")
		return blocked
	}

	result.RunID = feedback.RunID(issue.Key, "task_run", runner.Now, "a8f3")
	result.TakeoverAt = runner.Now.Format(time.RFC3339)
	result.CurrentAgentID = runner.AgentID
	if runner.Mode == "real" {
		if writeResult := runner.writeRealTakeover(ctx, issue.Key, result); !writeResult.OK {
			return writeResult
		}
	}

	result.OK = true
	result.CurrentStage = "takeover_started"
	result.NextAction = "process"
	if err := runner.appendEvent(result, "takeover"); err != nil {
		return result.failed("event_write_failed", err.Error(), "请检查工作空间目录权限")
	}

	capability, capabilityFailure := resolveCapability(request, decision.TaskClass)
	if capability == nil {
		capabilityFailure.Workspace = request.Workspace
		capabilityFailure.IssueKey = issue.Key
		capabilityFailure.RunID = result.RunID
		capabilityFailure.AgentID = runner.AgentID
		capabilityFailure.CurrentAgentID = runner.AgentID
		capabilityFailure.TakeoverAt = result.TakeoverAt
		capabilityFailure.TaskType = TaskType
		capabilityFailure.TaskClass = decision.TaskClass
		capabilityFailure.TaskClassSource = decision.TaskClassSource
		capabilityFailure.ProcessID = decision.ProcessID
		capabilityFailure.TargetRepo = decision.TargetRepo
		_ = runner.appendEvent(capabilityFailure, "process")
		return capabilityFailure
	}

	capabilityResult := capability.Process(ctx, TaskContext{
		IssueKey:         issue.Key,
		TaskClass:        decision.TaskClass,
		ProcessID:        decision.ProcessID,
		TargetRepo:       decision.TargetRepo,
		Labels:           issue.Labels,
		RequestedProcess: request.Process,
	})
	result.OK = capabilityResult.OK
	result.CapabilityID = capability.ID()
	result.DefectComplexity = capabilityResult.DefectComplexity
	result.CurrentStage = capabilityResult.CurrentStage
	result.NextAction = capabilityResult.NextAction
	result.CurrentAgentIDCleared = capabilityResult.CurrentAgentIDCleared
	result.AuditTarget = capabilityResult.AuditTarget
	result.AuditSubmitted = capabilityResult.AuditSubmitted
	result.AuditReference = capabilityResult.AuditReference
	result.Code = capabilityResult.Code
	result.Message = capabilityResult.Message
	result.RequiredHumanAction = capabilityResult.RequiredHumanAction
	result.HumanGate = capabilityResult.HumanGate
	if err := runner.appendEvent(result, "process"); err != nil {
		return result.failed("event_write_failed", err.Error(), "请检查工作空间目录权限")
	}
	if !capabilityResult.OK {
		return result
	}
	if !capabilityResult.CurrentAgentIDCleared {
		return result
	}
	if runner.Mode == "real" {
		if writeResult := runner.writeRealRelease(ctx, issue.Key, result); !writeResult.OK {
			return writeResult
		}
	}
	if err := runner.appendEvent(result, "writeback"); err != nil {
		return result.failed("event_write_failed", err.Error(), "请检查工作空间目录权限")
	}
	return result
}

func (runner TaskLifecycleRunner) validate() error {
	if runner.Client == nil {
		return errors.New("jira client is required")
	}
	if runner.AgentID == "" {
		return errors.New("agent id is required")
	}
	if runner.Now.IsZero() {
		return errors.New("current time is required")
	}
	if runner.AppendEvent == nil {
		return errors.New("event appender is required")
	}
	if runner.TakeoverFields == nil {
		runner.TakeoverFields = func(profile.Profile, string, string) map[string]any { return nil }
	}
	if runner.ReleaseFields == nil {
		runner.ReleaseFields = func(profile.Profile) map[string]any { return nil }
	}
	return nil
}

func (runner TaskLifecycleRunner) writeRealTakeover(ctx context.Context, issueKey string, result Result) Result {
	if !runner.ConfirmRealJiraWrite {
		blocked := result.blocked("real_jira_confirmation_required", "真实 Jira 写入需要显式确认", "请确认 policy/gate 允许写入后添加 --confirm-real-jira-write")
		blocked.CurrentStage = "takeover_gate"
		blocked.NextAction = "ask_owner"
		_ = runner.appendEvent(blocked, "real_jira_write")
		return blocked
	}
	fields := runner.TakeoverFields(runner.Profile, runner.AgentID, result.TakeoverAt)
	if len(fields) < 2 {
		blocked := result.blocked("missing_jira_write_mapping", "缺少 current_agent_id 或 takeover_at 字段映射", "请维护 workflow profile 的所有权字段映射")
		blocked.CurrentStage = "takeover_gate"
		blocked.NextAction = "ask_owner"
		_ = runner.appendEvent(blocked, "real_jira_write")
		return blocked
	}
	if err := runner.Client.UpdateFields(ctx, issueKey, fields); err != nil {
		failed := result.failed("jira_takeover_write_failed", err.Error(), "请检查 Jira 字段权限和 policy gate")
		failed.CurrentStage = "takeover_gate"
		failed.NextAction = "ask_owner"
		_ = runner.appendEvent(failed, "real_jira_write")
		return failed
	}
	passed := result
	passed.OK = true
	passed.CurrentStage = "takeover_started"
	passed.NextAction = "process"
	_ = runner.appendEvent(passed, "real_jira_write")
	return passed
}

func (runner TaskLifecycleRunner) writeRealRelease(ctx context.Context, issueKey string, result Result) Result {
	fields := runner.ReleaseFields(runner.Profile)
	if len(fields) == 0 {
		blocked := result.blocked("missing_jira_write_mapping", "缺少 current_agent_id 字段映射", "请维护 workflow profile 的所有权字段映射")
		blocked.CurrentStage = "completion_cleanup"
		blocked.NextAction = "ask_owner"
		_ = runner.appendEvent(blocked, "real_jira_write")
		return blocked
	}
	if err := runner.Client.UpdateFields(ctx, issueKey, fields); err != nil {
		failed := result.failed("agent_release_failed", err.Error(), "请检查 Jira 字段权限并由研发负责人决策是否人工释放")
		failed.CurrentStage = "completion_cleanup"
		failed.NextAction = "ask_owner"
		_ = runner.appendEvent(failed, "real_jira_write")
		return failed
	}
	passed := result
	passed.OK = true
	_ = runner.appendEvent(passed, "real_jira_write")
	return passed
}

func (runner TaskLifecycleRunner) appendEvent(result Result, gate string) error {
	return runner.AppendEvent(feedback.Event{
		RunID:                 result.RunID,
		IssueKey:              result.IssueKey,
		TaskType:              TaskType,
		Operation:             Operation,
		CurrentStage:          result.CurrentStage,
		NextAction:            result.NextAction,
		AgentID:               result.AgentID,
		CurrentAgentID:        result.CurrentAgentID,
		TakeoverAt:            result.TakeoverAt,
		TargetRepo:            result.TargetRepo,
		TaskClass:             result.TaskClass,
		TaskClassSource:       result.TaskClassSource,
		ProcessID:             result.ProcessID,
		CapabilityID:          result.CapabilityID,
		DefectComplexity:      result.DefectComplexity,
		CurrentAgentIDCleared: result.CurrentAgentIDCleared,
		AuditTarget:           result.AuditTarget,
		AuditSubmitted:        result.AuditSubmitted,
		AuditReference:        result.AuditReference,
		OK:                    result.OK,
		Code:                  result.Code,
		Gate:                  gate,
		GateStatus:            gateStatus(result.OK, result.HumanGate),
		HumanGate:             result.HumanGate,
		RequiresHumanAction:   result.HumanGate,
	})
}

func (result Result) blocked(code string, message string, action string) Result {
	result.OK = false
	result.Code = code
	result.Message = message
	result.RequiredHumanAction = action
	result.HumanGate = true
	return result
}

func (result Result) failed(code string, message string, action string) Result {
	result.OK = false
	result.Code = code
	result.Message = message
	result.RequiredHumanAction = action
	result.HumanGate = false
	return result
}

func gateStatus(ok bool, humanGate bool) string {
	if ok {
		return "passed"
	}
	if humanGate {
		return "blocked"
	}
	return "failed"
}
