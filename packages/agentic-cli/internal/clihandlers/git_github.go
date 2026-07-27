package clihandlers

import (
	"context"
	"errors"
	"fmt"
	"github.com/tapstate/agentic-ops/packages/agentic-cli/internal/feedback"
	gitops "github.com/tapstate/agentic-ops/packages/agentic-cli/internal/git"
	"github.com/tapstate/agentic-ops/packages/agentic-cli/internal/github"
	"github.com/tapstate/agentic-ops/packages/agentic-cli/internal/output"
	"github.com/tapstate/agentic-ops/packages/agentic-cli/internal/policy"
	"github.com/tapstate/agentic-ops/packages/agentic-cli/internal/profile"
	"io"
	"os"
	"path/filepath"
	"strings"
)

func runInspectWorkspace(args []string, stdout io.Writer) int {
	workspaceName := workspaceNameFromArgsOrAgentConfig(args, "default")
	workspaceProfile := takeoverProfile(workspaceName)
	sourceRoot, err := sourceRootForOperation(args, workspaceProfile)
	if err != nil {
		return writeJSON(stdout, output.FailureWithContext("inspect_workspace", output.FailureContext{
			Code:                "source_root_not_found",
			Message:             err.Error(),
			RequiredHumanAction: "请提供 --source-root，或维护 workflow profile 的 local.source_root",
			TaskType:            "workspace_inspection",
			CurrentStage:        "workspace_inspection",
			NextAction:          "fix_workspace",
		}))
	}
	status, err := inspectGitWorkspace(context.Background(), sourceRoot)
	if err != nil {
		return writeJSON(stdout, output.FailureWithContext("inspect_workspace", output.FailureContext{
			Code:                "git_inspect_failed",
			Message:             err.Error(),
			RequiredHumanAction: "请确认当前目录是 Git 仓库且 Git 可用",
			TaskType:            "workspace_inspection",
			CurrentStage:        "workspace_inspection",
			NextAction:          "fix_workspace",
		}))
	}
	nextAction := "continue_development"
	if status.Dirty {
		nextAction = "prepare_pr"
	}
	return writeJSON(stdout, output.Success("inspect_workspace", map[string]any{
		"workspace":     workspaceName,
		"source_root":   sourceRoot,
		"branch":        status.Branch,
		"commit":        status.Commit,
		"dirty":         status.Dirty,
		"changed_files": status.ChangedFiles,
		"current_stage": "workspace_inspected",
		"next_action":   nextAction,
	}))
}

func runBranchAlign(args []string, stdout io.Writer) int {
	workspaceName := workspaceNameFromArgsOrAgentConfig(args, "tapdata")
	if workspaceName != "tapdata" {
		return writeJSON(stdout, output.FailureWithContext("branch_align", output.FailureContext{
			Code:                "workspace_not_supported",
			Message:             "branch-align 当前只支持 tapdata 工作区",
			RequiredHumanAction: "请确认目标工作区，或先为该工作区补充项目级分支规范",
			TaskType:            "branch_switch",
			CurrentStage:        "branch_switch_gate",
			NextAction:          "ask_owner",
		}))
	}
	mode := positionalArg(args, "branch-align")
	if mode == "" {
		return writeJSON(stdout, output.FailureWithContext("branch_align", output.FailureContext{
			Code:                "missing_mode",
			Message:             "缺少分支对齐动作",
			RequiredHumanAction: "请按 agentic-cli tapdata branch-align plan develop 重试，或使用 list/status/apply",
			TaskType:            "branch_switch",
			CurrentStage:        "branch_switch_gate",
			NextAction:          "ask_owner",
		}))
	}
	workspaceProfile := takeoverProfile(workspaceName)
	workRoot, err := tapdataWorkRootForOperation(args, workspaceProfile)
	if err != nil {
		return writeJSON(stdout, output.FailureWithContext("branch_align", output.FailureContext{
			Code:                "work_root_not_found",
			Message:             err.Error(),
			RequiredHumanAction: "请提供 --work-root，设置 TAPDATA_WORK_ROOT，或维护 tapdata profile 的 local.source_root",
			TaskType:            "branch_switch",
			CurrentStage:        "branch_switch_gate",
			NextAction:          "fix_workspace",
		}))
	}
	remote := readFlag(args, "--remote", os.Getenv("TAPDATA_GIT_REMOTE"))
	if remote == "" {
		remote = "origin"
	}
	ctx := context.Background()
	switch mode {
	case "list":
		filter := positionalArg(args[1:], "list")
		if !hasFlag(args, "--no-fetch") {
			if err := gitops.FetchTapdataAlignmentRepos(ctx, workRoot, remote, false); err != nil {
				return writeJSON(stdout, branchAlignmentFailure("list", err))
			}
		}
		branches, err := gitops.ListTapdataBranches(ctx, workRoot, remote, filter)
		if err != nil {
			return writeJSON(stdout, branchAlignmentFailure("list", err))
		}
		return writeJSON(stdout, output.Success("branch_align", map[string]any{
			"workspace":     workspaceName,
			"mode":          "list",
			"work_root":     workRoot,
			"remote":        remote,
			"filter":        filter,
			"branches":      branches,
			"match_count":   len(branches),
			"current_stage": "branch_candidates_listed",
			"next_action":   "plan_branch_alignment",
		}))
	case "status":
		rows := gitops.TapdataAlignmentStatus(ctx, workRoot)
		return writeJSON(stdout, output.Success("branch_align", map[string]any{
			"workspace":     workspaceName,
			"mode":          "status",
			"work_root":     workRoot,
			"rows":          rows,
			"rows_count":    len(rows),
			"current_stage": "branch_status_inspected",
			"next_action":   "plan_branch_alignment",
		}))
	case "plan", "apply":
		branchSpec := positionalArg(args[1:], mode)
		if branchSpec == "" {
			return writeJSON(stdout, output.FailureWithContext("branch_align", output.FailureContext{
				Code:                "missing_branch_spec",
				Message:             "缺少 TapData 主仓分支或分支组",
				RequiredHumanAction: "请提供 develop、main、release-vX.Y.Z，或 <tapdata>,<enterprise>,<web> 格式",
				TaskType:            "branch_switch",
				CurrentStage:        "branch_switch_gate",
				NextAction:          "ask_owner",
			}))
		}
		if !hasFlag(args, "--no-fetch") {
			if err := gitops.FetchTapdataAlignmentRepos(ctx, workRoot, remote, true); err != nil {
				return writeJSON(stdout, branchAlignmentFailure(mode, err))
			}
		}
		plan, err := gitops.PlanTapdataBranchAlignment(ctx, gitops.BranchAlignmentRequest{
			WorkRoot:   workRoot,
			Remote:     remote,
			BranchSpec: branchSpec,
			NoFetch:    hasFlag(args, "--no-fetch"),
		})
		if err != nil {
			return writeJSON(stdout, branchAlignmentFailure(mode, err))
		}
		if mode == "plan" {
			nextAction := "apply_branch_alignment"
			if plan.Blocked {
				nextAction = "resolve_branch_alignment"
			}
			return writeJSON(stdout, output.Success("branch_align", map[string]any{
				"workspace":     workspaceName,
				"mode":          "plan",
				"work_root":     workRoot,
				"remote":        remote,
				"branch_spec":   branchSpec,
				"tap_branch":    plan.TapBranch,
				"ent_branch":    plan.EntBranch,
				"web_branch":    plan.WebBranch,
				"blocked":       plan.Blocked,
				"rows":          plan.Rows,
				"rows_count":    len(plan.Rows),
				"current_stage": "branch_alignment_planned",
				"next_action":   nextAction,
			}))
		}
		if plan.Blocked {
			return writeJSON(stdout, output.FailureWithContext("branch_align", output.FailureContext{
				Code:                "branch_alignment_blocked",
				Message:             "分支对齐计划存在 UNRESOLVED 或缺失仓库，未执行切换",
				RequiredHumanAction: "请补齐缺失仓库或在分支组中显式指定 enterprise/web 分支后重试",
				TaskType:            "branch_switch",
				CurrentStage:        "branch_alignment_gate",
				NextAction:          "resolve_branch_alignment",
			}))
		}
		switched, err := gitops.ApplyTapdataBranchAlignment(ctx, plan)
		if err != nil {
			return writeJSON(stdout, branchAlignmentFailure(mode, err))
		}
		return writeJSON(stdout, output.Success("branch_align", map[string]any{
			"workspace":      workspaceName,
			"mode":           "apply",
			"work_root":      workRoot,
			"remote":         remote,
			"branch_spec":    branchSpec,
			"tap_branch":     plan.TapBranch,
			"switched_rows":  switched,
			"switched_count": len(switched),
			"current_stage":  "branch_alignment_applied",
			"next_action":    "continue_development",
		}))
	default:
		return writeJSON(stdout, output.FailureWithContext("branch_align", output.FailureContext{
			Code:                "unsupported_mode",
			Message:             "不支持的分支对齐动作: " + mode,
			RequiredHumanAction: "请使用 list、status、plan 或 apply",
			TaskType:            "branch_switch",
			CurrentStage:        "branch_switch_gate",
			NextAction:          "ask_owner",
		}))
	}
}

func runPreparePR(args []string, stdout io.Writer) int {
	workspaceName := workspaceNameFromArgsOrAgentConfig(args, "default")
	runID := readFlag(args, "--run-id", "")
	if runID == "" {
		return writeJSON(stdout, output.FailureWithContext("prepare_pr", output.FailureContext{
			Code:                "missing_run_id",
			Message:             "缺少 run_id",
			RequiredHumanAction: "请提供 --run-id",
			TaskType:            "pr_preparation",
			CurrentStage:        "pr_plan_preparation",
			NextAction:          "ask_owner",
		}))
	}
	root, err := workspaceRoot()
	if err != nil {
		return writeJSON(stdout, output.Failure("prepare_pr", "workspace_root_failed", "无法读取当前工作目录", "请在项目 AI 工作空间中重试"))
	}
	state, err := evidenceRunState(root, workspaceName, runID)
	if err != nil {
		return writeJSON(stdout, output.FailureWithContext("prepare_pr", output.FailureContext{
			Code:                evidenceStateErrorCode(err),
			Message:             err.Error(),
			RequiredHumanAction: "请检查 run_id 是否存在有效接管事件，且仍属于当前 AIAgent",
			TaskType:            "pr_preparation",
			CurrentStage:        "pr_plan_preparation",
			NextAction:          "ask_owner",
		}))
	}
	workspaceProfile := takeoverProfile(workspaceName)
	sourceRoot, err := sourceRootForOperation(args, workspaceProfile)
	if err != nil {
		return writeJSON(stdout, output.FailureWithContext("prepare_pr", output.FailureContext{
			Code:                "source_root_not_found",
			Message:             err.Error(),
			RequiredHumanAction: "请提供 --source-root，或维护 workflow profile 的 local.source_root",
			TaskType:            "pr_preparation",
			CurrentStage:        "pr_plan_preparation",
			NextAction:          "fix_workspace",
		}))
	}
	status, err := inspectGitWorkspace(context.Background(), sourceRoot)
	if err != nil {
		return writeJSON(stdout, output.FailureWithContext("prepare_pr", output.FailureContext{
			Code:                "git_inspect_failed",
			Message:             err.Error(),
			RequiredHumanAction: "请确认当前目录是 Git 仓库且 Git 可用",
			TaskType:            "pr_preparation",
			CurrentStage:        "pr_plan_preparation",
			NextAction:          "fix_workspace",
		}))
	}
	policyPath, gateInfo, err := prPolicyGateInfo()
	if err != nil {
		return writeJSON(stdout, output.FailureWithContext("prepare_pr", output.FailureContext{
			Code:                "policy_not_found",
			Message:             err.Error(),
			RequiredHumanAction: "请检查 install-resources/basic/policies/default.yaml 是否存在且通过校验",
			TaskType:            "pr_preparation",
			CurrentStage:        "pr_plan_preparation",
			NextAction:          "fix_policy",
		}))
	}
	base := readFlag(args, "--base", "main")
	title := readFlag(args, "--title", state.IssueKey+" task update")
	body := readFlag(args, "--body", "由 AgenticOps 生成的拉取请求计划，等待负责人确认后再推送和创建 PR。")
	targetRepo := state.TargetRepo
	if targetRepo == "" {
		targetRepo = workspaceProfile.GitHub.Repositories.Default
	}
	_ = appendWorkspaceEventWithDetails(workspaceName, feedback.Event{
		RunID:          runID,
		IssueKey:       state.IssueKey,
		TaskType:       "pr_preparation",
		Operation:      "prepare_pr",
		CurrentStage:   "pr_plan_prepared",
		NextAction:     "ask_owner_to_push_and_create_pr",
		AgentID:        state.AgentID,
		CurrentAgentID: state.CurrentAgentID,
		TargetRepo:     targetRepo,
		TaskClass:      state.TaskClass,
		ProcessID:      state.ProcessID,
		OK:             true,
		Gate:           "prepare_pr",
		GateStatus:     "passed",
	})
	return writeJSON(stdout, output.Success("prepare_pr", map[string]any{
		"workspace":               workspaceName,
		"run_id":                  runID,
		"issue_key":               state.IssueKey,
		"task_class":              state.TaskClass,
		"process_id":              state.ProcessID,
		"target_repo":             targetRepo,
		"source_root":             sourceRoot,
		"branch":                  status.Branch,
		"commit":                  status.Commit,
		"dirty":                   status.Dirty,
		"changed_files":           status.ChangedFiles,
		"base":                    base,
		"title":                   title,
		"body":                    body,
		"policy":                  policyPath,
		"policy_gates":            gateInfo,
		"policy_gate_code":        "policy_gate_required",
		"git_push_gate_required":  gateInfo["git_push"],
		"create_pr_gate_required": gateInfo["create_pr"],
		"blocked_operations":      gatedOperations(gateInfo),
		"current_stage":           "pr_plan_prepared",
		"next_action":             "ask_owner_to_push_and_create_pr",
	}))
}

func branchAlignmentFailure(mode string, err error) map[string]any {
	context := output.FailureContext{
		Message:      err.Error(),
		TaskType:     "branch_switch",
		CurrentStage: "branch_switch_gate",
		NextAction:   "ask_owner",
	}
	switch {
	case errors.Is(err, gitops.ErrInvalidBranch):
		context.Code = "invalid_branch"
		context.RequiredHumanAction = "请确认分支名只包含安全字符，或使用 <tapdata>,<enterprise>,<web> 格式指定分支组"
	case errors.Is(err, gitops.ErrMissingTapdataBranch):
		context.Code = "tapdata_branch_not_found"
		context.RequiredHumanAction = "请先确认 tapdata 主仓存在该本地或远程分支"
	case errors.Is(err, gitops.ErrBranchAlignmentBlocked):
		context.Code = "branch_alignment_blocked"
		context.RequiredHumanAction = "请先解决分支计划中的 blocked 行"
	default:
		context.Code = "git_branch_alignment_failed"
		context.RequiredHumanAction = "请检查 work root 下 TapData 多仓是否存在、Git 是否可用，以及远程分支是否已 fetch"
	}
	return output.FailureWithContext("branch_align", context)
}

func tapdataWorkRootForOperation(args []string, workspaceProfile profile.Profile) (string, error) {
	if workRoot := readFlag(args, "--work-root", ""); workRoot != "" {
		return filepath.Clean(workRoot), nil
	}
	if workRoot := os.Getenv("TAPDATA_WORK_ROOT"); strings.TrimSpace(workRoot) != "" {
		return filepath.Clean(workRoot), nil
	}
	if sourceRoot := readFlag(args, "--source-root", ""); sourceRoot != "" {
		return filepath.Dir(filepath.Clean(sourceRoot)), nil
	}
	if sourceRoot := strings.TrimSpace(workspaceProfile.Local.SourceRoot); sourceRoot != "" {
		return filepath.Dir(filepath.Clean(sourceRoot)), nil
	}
	if workspaceProfile.Local.WorkspaceRoot != "" {
		return filepath.Join(workspaceProfile.Local.WorkspaceRoot, "repos"), nil
	}
	return "", fmt.Errorf("tapdata work root not configured")
}

func runReadPRComments(args []string, stdout io.Writer) int {
	workspaceName := workspaceNameFromArgsOrAgentConfig(args, "default")
	repo := readFlag(args, "--repo", "")
	pr := readFlag(args, "--pr", "")
	if repo == "" || pr == "" {
		return writeJSON(stdout, output.FailureWithContext("read_pr_comments", output.FailureContext{
			Code:                "missing_pr_reference",
			Message:             "缺少 repo 或 pr",
			RequiredHumanAction: "请提供 --repo 和 --pr",
			TaskType:            "pr_review",
			CurrentStage:        "pr_comment_read",
			NextAction:          "ask_owner",
		}))
	}
	comments, err := gitHubClient.ReadPRComments(context.Background(), repo, pr)
	if err != nil {
		return writeJSON(stdout, output.FailureWithContext("read_pr_comments", output.FailureContext{
			Code:                "github_pr_read_failed",
			Message:             err.Error(),
			RequiredHumanAction: "请检查 GitHub CLI 登录状态、仓库权限和 PR 编号",
			TaskType:            "pr_review",
			CurrentStage:        "pr_comment_read",
			NextAction:          "fix_environment",
		}))
	}
	return writeJSON(stdout, output.Success("read_pr_comments", map[string]any{
		"workspace":      workspaceName,
		"repo":           repo,
		"pr":             pr,
		"comments_count": len(comments),
		"comments":       comments,
		"current_stage":  "pr_comments_read",
		"next_action":    "classify_or_fix_pr_comments",
	}))
}

func runCheckCIStatus(args []string, stdout io.Writer) int {
	workspaceName := workspaceNameFromArgsOrAgentConfig(args, "default")
	repo := readFlag(args, "--repo", "")
	pr := readFlag(args, "--pr", "")
	if repo == "" || pr == "" {
		return writeJSON(stdout, output.FailureWithContext("check_ci_status", output.FailureContext{
			Code:                "missing_pr_reference",
			Message:             "缺少 repo 或 pr",
			RequiredHumanAction: "请提供 --repo 和 --pr",
			TaskType:            "ci_check",
			CurrentStage:        "ci_status_check",
			NextAction:          "ask_owner",
		}))
	}
	status, err := gitHubClient.CheckCIStatus(context.Background(), repo, pr)
	if err != nil {
		return writeJSON(stdout, output.FailureWithContext("check_ci_status", output.FailureContext{
			Code:                "github_ci_read_failed",
			Message:             err.Error(),
			RequiredHumanAction: "请检查 GitHub CLI 登录状态、仓库权限和 PR 编号",
			TaskType:            "ci_check",
			CurrentStage:        "ci_status_check",
			NextAction:          "fix_environment",
		}))
	}
	nextAction := "continue_review"
	if status.Status == "failed" {
		nextAction = "fix_ci_failures"
	}
	if status.Status == "pending" {
		nextAction = "wait_ci"
	}
	return writeJSON(stdout, output.Success("check_ci_status", map[string]any{
		"workspace":            workspaceName,
		"repo":                 repo,
		"pr":                   pr,
		"status":               status.Status,
		"checks":               status.Checks,
		"failing_checks":       status.FailingChecks,
		"failing_checks_count": len(status.FailingChecks),
		"current_stage":        "ci_status_checked",
		"next_action":          nextAction,
	}))
}

func runFixPRComments(args []string, stdout io.Writer) int {
	workspaceName := workspaceNameFromArgsOrAgentConfig(args, "default")
	repo := readFlag(args, "--repo", "")
	pr := readFlag(args, "--pr", "")
	if repo == "" || pr == "" {
		return writeJSON(stdout, output.FailureWithContext("fix_pr_comments", output.FailureContext{
			Code:                "missing_pr_reference",
			Message:             "缺少 repo 或 pr",
			RequiredHumanAction: "请提供 --repo 和 --pr",
			TaskType:            "pr_comment_fix",
			CurrentStage:        "pr_comment_fix_gate",
			NextAction:          "ask_owner",
		}))
	}
	comments, err := gitHubClient.ReadPRComments(context.Background(), repo, pr)
	if err != nil {
		return writeJSON(stdout, output.FailureWithContext("fix_pr_comments", output.FailureContext{
			Code:                "github_pr_read_failed",
			Message:             err.Error(),
			RequiredHumanAction: "请检查 GitHub CLI 登录状态、仓库权限和 PR 编号",
			TaskType:            "pr_comment_fix",
			CurrentStage:        "pr_comment_fix_gate",
			NextAction:          "fix_environment",
		}))
	}
	policyPath, gateInfo, err := prPolicyGateInfo()
	if err != nil {
		return writeJSON(stdout, output.FailureWithContext("fix_pr_comments", output.FailureContext{
			Code:                "policy_not_found",
			Message:             err.Error(),
			RequiredHumanAction: "请检查 install-resources/basic/policies/default.yaml 是否存在且通过校验",
			TaskType:            "pr_comment_fix",
			CurrentStage:        "pr_comment_fix_gate",
			NextAction:          "fix_policy",
		}))
	}
	result := output.FailureWithContext("fix_pr_comments", output.FailureContext{
		Code:                "policy_gate_required",
		Message:             "PR review fix requires human gate before code changes or resubmission",
		RequiredHumanAction: "请由研发工程师确认评论取舍、修改范围，以及后续 git_commit/git_push 策略门禁",
		TaskType:            "pr_comment_fix",
		CurrentStage:        "pr_comment_fix_gate",
		NextAction:          "ask_owner",
	})
	result["workspace"] = workspaceName
	result["repo"] = repo
	result["pr"] = pr
	result["comments_count"] = len(comments)
	result["fix_plan"] = classifyPRComments(comments)
	result["policy"] = policyPath
	result["policy_gates"] = gateInfo
	result["blocked_operations"] = gatedOperations(gateInfo)
	return writeJSON(stdout, result)
}

func sourceRootForOperation(args []string, workspaceProfile profile.Profile) (string, error) {
	if sourceRoot := readFlag(args, "--source-root", ""); sourceRoot != "" {
		return sourceRoot, nil
	}
	if strings.TrimSpace(workspaceProfile.Local.SourceRoot) != "" {
		return workspaceProfile.Local.SourceRoot, nil
	}
	root, err := workspaceRoot()
	if err != nil {
		return "", err
	}
	return root, nil
}

func prPolicyGateInfo() (string, map[string]bool, error) {
	policyPath, err := repoPolicyPath()
	if err != nil {
		return "", nil, err
	}
	loadedPolicy, err := policy.LoadFile(policyPath)
	if err != nil {
		return policyPath, nil, err
	}
	if issues := policy.Validate(loadedPolicy); len(issues) > 0 {
		return policyPath, nil, fmt.Errorf("policy validation failed: %s", issues[0].Code)
	}
	return policyPath, map[string]bool{
		"git_commit":      policy.RequiresHumanGate(loadedPolicy, "git_commit"),
		"git_push":        policy.RequiresHumanGate(loadedPolicy, "git_push"),
		"create_pr":       policy.RequiresHumanGate(loadedPolicy, "create_pr"),
		"fix_pr_comments": policy.RequiresHumanGate(loadedPolicy, "fix_pr_comments"),
	}, nil
}

func gatedOperations(gates map[string]bool) []string {
	var operations []string
	for _, operation := range []string{"git_commit", "git_push", "create_pr", "fix_pr_comments"} {
		if gates[operation] {
			operations = append(operations, operation)
		}
	}
	return operations
}

func classifyPRComments(comments []github.PRComment) []map[string]string {
	plan := make([]map[string]string, 0, len(comments))
	for _, comment := range comments {
		plan = append(plan, map[string]string{
			"category": classifyPRComment(comment.Body),
			"author":   comment.Author,
			"url":      comment.URL,
			"summary":  firstLine(comment.Body),
		})
	}
	return plan
}

func classifyPRComment(body string) string {
	lower := strings.ToLower(body)
	switch {
	case strings.Contains(lower, "test") || strings.Contains(body, "测试"):
		return "test"
	case strings.Contains(lower, "doc") || strings.Contains(body, "文档"):
		return "docs"
	default:
		return "code"
	}
}

func firstLine(value string) string {
	value = strings.TrimSpace(value)
	if index := strings.IndexAny(value, "\r\n"); index >= 0 {
		return value[:index]
	}
	return value
}
