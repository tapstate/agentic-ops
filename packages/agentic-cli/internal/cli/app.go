package cli

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"runtime"
	"time"

	"github.com/tapstate/agentic-ops/packages/agentic-cli/internal/assets"
	"github.com/tapstate/agentic-ops/packages/agentic-cli/internal/config"
	"github.com/tapstate/agentic-ops/packages/agentic-cli/internal/contract"
	"github.com/tapstate/agentic-ops/packages/agentic-cli/internal/evidence"
	"github.com/tapstate/agentic-ops/packages/agentic-cli/internal/feedback"
	"github.com/tapstate/agentic-ops/packages/agentic-cli/internal/jira"
	"github.com/tapstate/agentic-ops/packages/agentic-cli/internal/output"
	"github.com/tapstate/agentic-ops/packages/agentic-cli/internal/policy"
	"github.com/tapstate/agentic-ops/packages/agentic-cli/internal/profile"
	"github.com/tapstate/agentic-ops/packages/agentic-cli/internal/update"
	"github.com/tapstate/agentic-ops/packages/agentic-cli/internal/workspace"
)

var Version = "SRC-source"
var VersionState = "SRC"
var IterationVersion = "source"
var CommitIndex = "0"
var Commit = "unknown"
var BuildTime = ""

var runGitHubAuthStatus = func(ctx context.Context) error {
	return exec.CommandContext(ctx, "gh", "auth", "status").Run()
}

func Run(args []string, stdout io.Writer, stderr io.Writer) int {
	if len(args) == 0 {
		return writeJSON(stdout, output.Failure("unknown", "missing_command", "缺少命令", "请提供命令"))
	}

	switch args[0] {
	case "--version", "version":
		return writeJSON(stdout, output.Success("version", map[string]any{
			"version":           Version,
			"version_state":     VersionState,
			"iteration_version": IterationVersion,
			"commit_index":      parseCommitIndex(CommitIndex),
			"commit":            Commit,
			"build_time":        BuildTime,
		}))
	case "doctor":
		return runDoctor(args, stdout)
	case "preflight":
		return runPreflight(args, stdout)
	case "workspace":
		if len(args) >= 2 && args[1] == "init" {
			return runWorkspaceInit(args, stdout)
		}
	case "agent":
		if len(args) >= 2 && args[1] == "init" {
			return runAgentInit(args, stdout)
		}
	case "assets":
		if len(args) >= 2 && args[1] == "install" {
			return runAssetsInstall(args, stdout)
		}
	case "update":
		if len(args) >= 2 && args[1] == "check" {
			return runUpdateCheck(args, stdout)
		}
		if len(args) >= 2 && args[1] == "apply" {
			return runUpdateApply(args, stdout)
		}
	case "contract":
		if len(args) >= 2 && args[1] == "validate" {
			return runContractValidate(args, stdout)
		}
	case "profile":
		if len(args) >= 2 && args[1] == "validate" {
			return runProfileValidate(args, stdout)
		}
		if len(args) >= 2 && args[1] == "update" {
			return runProfileUpdate(args, stdout)
		}
		if len(args) >= 2 && args[1] == "rollback" {
			return runProfileRollback(args, stdout)
		}
	case "policy":
		if len(args) >= 2 && args[1] == "validate" {
			return runPolicyValidate(args, stdout)
		}
		if len(args) >= 2 && args[1] == "update" {
			return runPolicyUpdate(args, stdout)
		}
		if len(args) >= 2 && args[1] == "rollback" {
			return runPolicyRollback(args, stdout)
		}
	case "list-tasks":
		return runListTasks(args, stdout)
	case "takeover-task":
		return runTakeoverTask(args, stdout)
	case "resume-takeover":
		return runResumeTakeover(args, stdout)
	case "write-evidence":
		return runWriteEvidence(args, stdout)
	case "release-agent":
		return runReleaseAgent(args, stdout)
	case "feedback":
		if len(args) >= 2 && args[1] == "report" {
			return runFeedbackReport(args, stdout)
		}
		if len(args) >= 2 && args[1] == "bundle" {
			return runFeedbackBundle(args, stdout)
		}
	}

	fmt.Fprintf(stderr, "unknown command: %s\n", args[0])
	return writeJSON(stdout, output.Failure(args[0], "unknown_command", "未知命令", "请检查命令名称"))
}

func parseCommitIndex(value string) int {
	var index int
	if _, err := fmt.Sscanf(value, "%d", &index); err != nil {
		return 0
	}
	return index
}

func runDoctor(args []string, stdout io.Writer) int {
	workspaceName := readFlag(args, "--workspace", "default")
	checks := map[string]map[string]string{
		"install":      checkInstallDir(readInstallDir(args)),
		"version":      {"status": "ok", "message": Version},
		"workspace":    checkWorkspaceRoot(),
		"profile":      checkProfile(workspaceName),
		"policy":       checkPolicy(),
		"contracts":    checkContracts(),
		"jira_adapter": checkJiraAdapter(workspaceName, hasFlag(args, "--check-real-jira")),
		"github":       checkGitHubAuth(hasFlag(args, "--check-github")),
	}
	status := "ok"
	for _, check := range checks {
		if check["status"] == "failed" {
			status = "failed"
			break
		}
	}
	nextAction := "continue"
	if status != "ok" {
		nextAction = "fix_environment"
	}
	return writeJSON(stdout, output.Success("doctor", map[string]any{
		"workspace":         workspaceName,
		"version":           Version,
		"version_state":     VersionState,
		"iteration_version": IterationVersion,
		"commit":            Commit,
		"status":            status,
		"checks":            checks,
		"next_action":       nextAction,
	}))
}

func checkJiraAdapter(workspaceName string, realCheck bool) map[string]string {
	if !realCheck {
		return map[string]string{"status": "ok", "message": "fake adapter available"}
	}
	workspaceProfile := takeoverProfile(workspaceName)
	selection, err := selectJiraClient(workspaceName, workspaceProfile)
	if err != nil {
		return map[string]string{"status": "failed", "message": err.Error()}
	}
	if selection.Mode != "real" {
		return map[string]string{"status": "failed", "message": "real Jira adapter is not active"}
	}
	currentUser, err := selection.Client.CurrentUser(context.Background())
	if err != nil {
		return map[string]string{"status": "failed", "message": err.Error()}
	}
	return map[string]string{"status": "ok", "message": "real adapter authenticated as " + currentUser}
}

func checkGitHubAuth(realCheck bool) map[string]string {
	if !realCheck {
		return map[string]string{"status": "skipped", "message": "GitHub CLI check requires --check-github"}
	}
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	if err := runGitHubAuthStatus(ctx); err != nil {
		return map[string]string{"status": "failed", "message": err.Error()}
	}
	return map[string]string{"status": "ok", "message": "GitHub CLI authenticated"}
}

func runPreflight(args []string, stdout io.Writer) int {
	return writeJSON(stdout, output.Success("preflight", map[string]any{
		"workspace":   readFlag(args, "--workspace", "default"),
		"install_dir": readInstallDir(args),
		"go_runtime":  "not_required_for_installed_cli",
		"jira":        "fake",
		"github":      "not_used_in_phase_one",
		"next_action": "workspace_init",
	}))
}

func runWorkspaceInit(args []string, stdout io.Writer) int {
	workspaceName := readFlag(args, "--workspace", "default")
	root, err := workspaceRoot()
	if err != nil {
		return writeJSON(stdout, output.Failure("workspace_init", "workspace_root_failed", "无法读取当前工作目录", "请在项目 AI 工作空间中重试"))
	}
	info, err := workspace.Ensure(root, workspaceName)
	if err != nil {
		return writeJSON(stdout, output.Failure("workspace_init", "workspace_init_failed", err.Error(), "请检查工作空间目录权限"))
	}
	return writeJSON(stdout, output.Success("workspace_init", map[string]any{
		"workspace":    info.Name,
		"profile":      info.Name,
		"runs_dir":     info.RunsDir,
		"feedback_dir": info.FeedbackDir,
		"next_action":  "init_agent_capability",
	}))
}

func runAgentInit(args []string, stdout io.Writer) int {
	return writeJSON(stdout, output.Success("agent_init", map[string]any{
		"workspace":     readFlag(args, "--workspace", "default"),
		"task_type":     "capability_initialization",
		"current_stage": "agent_capability_initialized",
		"next_action":   "list_tasks",
		"capabilities": []string{
			"preflight",
			"doctor",
			"assets_install",
			"update_check",
			"update_apply",
			"contract_validate",
			"profile_validate",
			"profile_update",
			"profile_rollback",
			"policy_validate",
			"policy_update",
			"policy_rollback",
			"workspace_init",
			"list_tasks",
			"takeover_task",
			"resume_takeover",
			"write_evidence",
			"release_agent",
			"feedback_report",
			"feedback_bundle",
		},
	}))
}

func runAssetsInstall(args []string, stdout io.Writer) int {
	source := readFlag(args, "--source", "")
	if source == "" {
		return writeJSON(stdout, output.Failure("assets_install", "missing_source", "缺少资产源目录", "请提供 --source"))
	}
	version := readFlag(args, "--version", "")
	if version == "" {
		return writeJSON(stdout, output.Failure("assets_install", "missing_asset_version", "缺少资产版本", "请提供 --version"))
	}
	installDir := readInstallDir(args)
	result, err := assets.Install(source, installDir, version)
	if err != nil {
		return writeJSON(stdout, output.Failure("assets_install", "assets_install_failed", err.Error(), "请检查资产源目录和安装目录权限"))
	}
	return writeJSON(stdout, output.Success("assets_install", map[string]any{
		"asset_version": result.AssetVersion,
		"assets_dir":    result.AssetsDir,
		"current":       result.CurrentPath,
		"next_action":   "agent_init",
	}))
}

func runUpdateCheck(args []string, stdout io.Writer) int {
	manifestPath := readFlag(args, "--manifest", "")
	manifestURL := readFlag(args, "--manifest-url", "")
	if manifestPath == "" && manifestURL == "" {
		return writeJSON(stdout, output.FailureWithContext("update_check", output.FailureContext{
			Code:                "missing_manifest",
			Message:             "缺少 release manifest",
			RequiredHumanAction: "请提供 --manifest 或 --manifest-url",
			TaskType:            "update",
			CurrentStage:        "update_check",
			NextAction:          "ask_owner",
		}))
	}
	source := "local"
	var result update.CheckResult
	var err error
	if manifestURL != "" {
		source = "remote"
		result, err = update.CheckRemote(manifestURL, Version)
	} else {
		result, err = update.Check(manifestPath, Version)
	}
	if err != nil {
		return writeJSON(stdout, output.FailureWithContext("update_check", output.FailureContext{
			Code:                "update_manifest_invalid",
			Message:             err.Error(),
			RequiredHumanAction: "请检查 release manifest 路径、URL 和格式",
			TaskType:            "update",
			CurrentStage:        "update_check",
			NextAction:          "fix_manifest",
		}))
	}
	return writeJSON(stdout, output.Success("update_check", map[string]any{
		"source":             source,
		"current_version":    result.CurrentVersion,
		"latest_version":     result.LatestVersion,
		"asset_version":      result.AssetVersion,
		"update_available":   result.UpdateAvailable,
		"severity":           result.Severity,
		"reason":             result.Reason,
		"blocked_operations": result.BlockedOperations,
		"next_action":        result.NextAction,
	}))
}

func runUpdateApply(args []string, stdout io.Writer) int {
	manifestPath := readFlag(args, "--manifest", "")
	manifestURL := readFlag(args, "--manifest-url", "")
	if manifestPath == "" && manifestURL == "" {
		return writeJSON(stdout, output.FailureWithContext("update_apply", output.FailureContext{
			Code:                "missing_manifest",
			Message:             "缺少 release manifest",
			RequiredHumanAction: "请提供 --manifest 或 --manifest-url",
			TaskType:            "update",
			CurrentStage:        "update_apply",
			NextAction:          "ask_owner",
		}))
	}
	installDir := readInstallDir(args)
	target := readFlag(args, "--target", runtime.GOOS+"-"+runtime.GOARCH)
	source := "local"
	var result update.ApplyResult
	var err error
	if manifestURL != "" {
		source = "remote"
		result, err = update.ApplyRemote(manifestURL, installDir, target)
	} else {
		result, err = update.Apply(manifestPath, installDir)
	}
	if err != nil {
		return writeJSON(stdout, output.FailureWithContext("update_apply", output.FailureContext{
			Code:                "update_apply_failed",
			Message:             err.Error(),
			RequiredHumanAction: "请检查 release manifest、artifact checksum 和安装目录权限",
			TaskType:            "update",
			CurrentStage:        "update_apply",
			NextAction:          "fix_update_source",
		}))
	}
	downloadedArtifacts := result.DownloadedArtifacts
	if downloadedArtifacts == nil {
		downloadedArtifacts = []string{}
	}
	return writeJSON(stdout, output.Success("update_apply", map[string]any{
		"source":                 source,
		"version":                result.AgenticCLIVersion,
		"asset_version":          result.AssetVersion,
		"previous_version":       result.PreviousAgenticCLIVersion,
		"previous_asset_version": result.PreviousAssetVersion,
		"current":                result.CurrentPath,
		"downloaded_artifacts":   downloadedArtifacts,
		"activated_binary":       result.ActivatedBinary,
		"next_action":            "doctor",
	}))
}

func runListTasks(args []string, stdout io.Writer) int {
	workspaceName := readFlag(args, "--workspace", "default")
	workspaceProfile := takeoverProfile(workspaceName)
	selection, err := selectJiraClient(workspaceName, workspaceProfile)
	if err != nil {
		return writeJSON(stdout, output.Failure("list_tasks", "jira_adapter_config_failed", err.Error(), "请检查 Jira adapter 配置"))
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

func runContractValidate(args []string, stdout io.Writer) int {
	root, err := repoRoot()
	if err != nil {
		return writeJSON(stdout, output.Failure("contract_validate", "repo_root_not_found", "未找到仓库根目录", "请在 AgenticOps 仓库内运行"))
	}
	paths, err := filepath.Glob(filepath.Join(root, "contracts", "operations", "*.yaml"))
	if err != nil {
		return writeJSON(stdout, output.Failure("contract_validate", "contract_glob_failed", err.Error(), "请检查 contracts/operations 目录"))
	}
	if len(paths) == 0 {
		return writeJSON(stdout, output.Failure("contract_validate", "contract_not_found", "未找到 operation contract", "请检查 contracts/operations 目录"))
	}
	var allIssues []map[string]any
	for _, path := range paths {
		op, err := contract.LoadFile(path)
		if err != nil {
			allIssues = append(allIssues, map[string]any{
				"path":    path,
				"code":    "contract_load_failed",
				"message": err.Error(),
			})
			continue
		}
		for _, issue := range contract.Validate(op) {
			allIssues = append(allIssues, map[string]any{
				"path":      path,
				"operation": op.Operation,
				"code":      issue.Code,
				"message":   issue.Message,
			})
		}
	}
	if len(allIssues) > 0 {
		return writeJSON(stdout, output.FailureWithContext("contract_validate", output.FailureContext{
			Code:                "contract_validation_failed",
			Message:             "operation contract validation failed",
			RequiredHumanAction: "请修复 contracts/operations 中的契约字段",
			TaskType:            "contract_validation",
			CurrentStage:        "contract_validation",
			NextAction:          "fix_contracts",
		}))
	}
	return writeJSON(stdout, output.Success("contract_validate", map[string]any{
		"contracts":   len(paths),
		"issues":      0,
		"next_action": "continue",
	}))
}

func runProfileValidate(args []string, stdout io.Writer) int {
	workspaceName := readFlag(args, "--workspace", "")
	if workspaceName == "" {
		return writeJSON(stdout, output.FailureWithContext("profile_validate", output.FailureContext{
			Code:                "missing_workspace",
			Message:             "缺少 workspace",
			RequiredHumanAction: "请提供 --workspace",
			TaskType:            "profile_validation",
			CurrentStage:        "profile_validation",
			NextAction:          "ask_owner",
		}))
	}
	root, err := repoRoot()
	if err != nil {
		return writeJSON(stdout, output.Failure("profile_validate", "repo_root_not_found", "未找到仓库根目录", "请在 AgenticOps 仓库内运行"))
	}
	profilePath := filepath.Join(root, "profiles", workspaceName+".yaml")
	loadedProfile, err := profile.LoadFile(profilePath)
	if err != nil {
		return writeJSON(stdout, output.FailureWithContext("profile_validate", output.FailureContext{
			Code:                "profile_not_found",
			Message:             err.Error(),
			RequiredHumanAction: "请检查 profiles 目录中的 workspace 配置",
			TaskType:            "profile_validation",
			CurrentStage:        "profile_validation",
			NextAction:          "fix_profile",
		}))
	}
	issues := profile.Validate(loadedProfile)
	if len(issues) > 0 {
		return writeJSON(stdout, output.FailureWithContext("profile_validate", output.FailureContext{
			Code:                "profile_validation_failed",
			Message:             "workflow profile validation failed",
			RequiredHumanAction: "请修复 workflow profile 中的字段、分类、流程、状态或 transition 映射",
			TaskType:            "profile_validation",
			CurrentStage:        "profile_validation",
			NextAction:          "fix_profile",
		}))
	}
	return writeJSON(stdout, output.Success("profile_validate", map[string]any{
		"workspace":   loadedProfile.Workspace,
		"issues":      0,
		"next_action": "continue",
	}))
}

func runProfileUpdate(args []string, stdout io.Writer) int {
	workspaceName := readFlag(args, "--workspace", "")
	if workspaceName == "" {
		return writeJSON(stdout, output.FailureWithContext("profile_update", output.FailureContext{
			Code:                "missing_workspace",
			Message:             "缺少 workspace",
			RequiredHumanAction: "请提供 --workspace",
			TaskType:            "profile_update",
			CurrentStage:        "input_validation",
			NextAction:          "ask_owner",
		}))
	}
	sourcePath := readFlag(args, "--source", "")
	if sourcePath == "" {
		return writeJSON(stdout, output.FailureWithContext("profile_update", output.FailureContext{
			Code:                "missing_source",
			Message:             "缺少 profile source",
			RequiredHumanAction: "请提供 --source",
			TaskType:            "profile_update",
			CurrentStage:        "input_validation",
			NextAction:          "ask_owner",
		}))
	}
	targetPath, err := repoProfilePath(workspaceName)
	if err != nil {
		return writeJSON(stdout, output.Failure("profile_update", "repo_root_not_found", "未找到仓库根目录", "请在 AgenticOps 仓库内运行"))
	}
	result, err := profile.UpdateFile(targetPath, sourcePath, workspaceName)
	if err != nil {
		return writeJSON(stdout, output.FailureWithContext("profile_update", output.FailureContext{
			Code:                "profile_update_failed",
			Message:             err.Error(),
			RequiredHumanAction: "请检查 source profile 是否存在、workspace 是否匹配且能通过校验",
			TaskType:            "profile_update",
			CurrentStage:        "profile_update",
			NextAction:          "fix_profile",
		}))
	}
	return writeJSON(stdout, output.Success("profile_update", map[string]any{
		"workspace":   result.Workspace,
		"profile":     result.TargetPath,
		"backup":      result.BackupPath,
		"source":      result.SourcePath,
		"next_action": "profile_validate",
	}))
}

func runProfileRollback(args []string, stdout io.Writer) int {
	workspaceName := readFlag(args, "--workspace", "")
	if workspaceName == "" {
		return writeJSON(stdout, output.FailureWithContext("profile_rollback", output.FailureContext{
			Code:                "missing_workspace",
			Message:             "缺少 workspace",
			RequiredHumanAction: "请提供 --workspace",
			TaskType:            "profile_rollback",
			CurrentStage:        "input_validation",
			NextAction:          "ask_owner",
		}))
	}
	targetPath, err := repoProfilePath(workspaceName)
	if err != nil {
		return writeJSON(stdout, output.Failure("profile_rollback", "repo_root_not_found", "未找到仓库根目录", "请在 AgenticOps 仓库内运行"))
	}
	result, err := profile.RollbackFile(targetPath, workspaceName)
	if err != nil {
		return writeJSON(stdout, output.FailureWithContext("profile_rollback", output.FailureContext{
			Code:                "profile_rollback_failed",
			Message:             err.Error(),
			RequiredHumanAction: "请检查 profile 备份是否存在且能通过校验",
			TaskType:            "profile_rollback",
			CurrentStage:        "profile_rollback",
			NextAction:          "fix_profile",
		}))
	}
	return writeJSON(stdout, output.Success("profile_rollback", map[string]any{
		"workspace":     result.Workspace,
		"profile":       result.TargetPath,
		"restored_from": result.RestoredFrom,
		"next_action":   "profile_validate",
	}))
}

func runPolicyValidate(args []string, stdout io.Writer) int {
	workspaceName := readFlag(args, "--workspace", "")
	if workspaceName == "" {
		return writeJSON(stdout, output.FailureWithContext("policy_validate", output.FailureContext{
			Code:                "missing_workspace",
			Message:             "缺少 workspace",
			RequiredHumanAction: "请提供 --workspace",
			TaskType:            "policy_validation",
			CurrentStage:        "policy_validation",
			NextAction:          "ask_owner",
		}))
	}
	policyPath, err := repoPolicyPath()
	if err != nil {
		return writeJSON(stdout, output.Failure("policy_validate", "repo_root_not_found", "未找到仓库根目录", "请在 AgenticOps 仓库内运行"))
	}
	loadedPolicy, err := policy.LoadFile(policyPath)
	if err != nil {
		return writeJSON(stdout, output.FailureWithContext("policy_validate", output.FailureContext{
			Code:                "policy_not_found",
			Message:             err.Error(),
			RequiredHumanAction: "请检查 assets/policies/default.yaml",
			TaskType:            "policy_validation",
			CurrentStage:        "policy_validation",
			NextAction:          "fix_policy",
		}))
	}
	issues := policy.Validate(loadedPolicy)
	if len(issues) > 0 {
		return writeJSON(stdout, output.FailureWithContext("policy_validate", output.FailureContext{
			Code:                "policy_validation_failed",
			Message:             "policy validation failed",
			RequiredHumanAction: "请修复 policy 中的名称、版本和关键 gate 配置",
			TaskType:            "policy_validation",
			CurrentStage:        "policy_validation",
			NextAction:          "fix_policy",
		}))
	}
	return writeJSON(stdout, output.Success("policy_validate", map[string]any{
		"workspace":   workspaceName,
		"policy":      loadedPolicy.Policy,
		"issues":      0,
		"next_action": "continue",
	}))
}

func runPolicyUpdate(args []string, stdout io.Writer) int {
	workspaceName := readFlag(args, "--workspace", "")
	if workspaceName == "" {
		return writeJSON(stdout, output.FailureWithContext("policy_update", output.FailureContext{
			Code:                "missing_workspace",
			Message:             "缺少 workspace",
			RequiredHumanAction: "请提供 --workspace",
			TaskType:            "policy_update",
			CurrentStage:        "input_validation",
			NextAction:          "ask_owner",
		}))
	}
	sourcePath := readFlag(args, "--source", "")
	if sourcePath == "" {
		return writeJSON(stdout, output.FailureWithContext("policy_update", output.FailureContext{
			Code:                "missing_source",
			Message:             "缺少 policy source",
			RequiredHumanAction: "请提供 --source",
			TaskType:            "policy_update",
			CurrentStage:        "input_validation",
			NextAction:          "ask_owner",
		}))
	}
	targetPath, err := repoPolicyPath()
	if err != nil {
		return writeJSON(stdout, output.Failure("policy_update", "repo_root_not_found", "未找到仓库根目录", "请在 AgenticOps 仓库内运行"))
	}
	result, err := policy.UpdateFile(targetPath, sourcePath, "default")
	if err != nil {
		return writeJSON(stdout, output.FailureWithContext("policy_update", output.FailureContext{
			Code:                "policy_update_failed",
			Message:             err.Error(),
			RequiredHumanAction: "请检查 source policy 是否存在、名称是否匹配且能通过校验",
			TaskType:            "policy_update",
			CurrentStage:        "policy_update",
			NextAction:          "fix_policy",
		}))
	}
	return writeJSON(stdout, output.Success("policy_update", map[string]any{
		"workspace":   workspaceName,
		"policy":      result.Policy,
		"path":        result.TargetPath,
		"backup":      result.BackupPath,
		"source":      result.SourcePath,
		"next_action": "policy_validate",
	}))
}

func runPolicyRollback(args []string, stdout io.Writer) int {
	workspaceName := readFlag(args, "--workspace", "")
	if workspaceName == "" {
		return writeJSON(stdout, output.FailureWithContext("policy_rollback", output.FailureContext{
			Code:                "missing_workspace",
			Message:             "缺少 workspace",
			RequiredHumanAction: "请提供 --workspace",
			TaskType:            "policy_rollback",
			CurrentStage:        "input_validation",
			NextAction:          "ask_owner",
		}))
	}
	targetPath, err := repoPolicyPath()
	if err != nil {
		return writeJSON(stdout, output.Failure("policy_rollback", "repo_root_not_found", "未找到仓库根目录", "请在 AgenticOps 仓库内运行"))
	}
	result, err := policy.RollbackFile(targetPath, "default")
	if err != nil {
		return writeJSON(stdout, output.FailureWithContext("policy_rollback", output.FailureContext{
			Code:                "policy_rollback_failed",
			Message:             err.Error(),
			RequiredHumanAction: "请检查 policy 备份是否存在且能通过校验",
			TaskType:            "policy_rollback",
			CurrentStage:        "policy_rollback",
			NextAction:          "fix_policy",
		}))
	}
	return writeJSON(stdout, output.Success("policy_rollback", map[string]any{
		"workspace":     workspaceName,
		"policy":        result.Policy,
		"path":          result.TargetPath,
		"restored_from": result.RestoredFrom,
		"next_action":   "policy_validate",
	}))
}

func runTakeoverTask(args []string, stdout io.Writer) int {
	if len(args) < 2 {
		return writeJSON(stdout, output.Failure("takeover_task", "missing_issue_key", "缺少 issue key", "请提供 Jira issue key"))
	}
	workspaceName := readFlag(args, "--workspace", "default")
	issueKey := args[1]
	workspaceProfile := takeoverProfile(workspaceName)
	selection, err := selectJiraClient(workspaceName, workspaceProfile)
	if err != nil {
		return writeJSON(stdout, output.Failure("takeover_task", "jira_adapter_config_failed", err.Error(), "请检查 Jira adapter 配置"))
	}
	issue, ok, err := selection.Client.GetIssueByKey(context.Background(), workspaceName, issueKey)
	if err != nil {
		return writeJSON(stdout, output.Failure("takeover_task", "jira_issue_read_failed", err.Error(), "请检查 Jira adapter 配置和 issue 权限"))
	}
	if !ok {
		return writeJSON(stdout, output.Failure("takeover_task", "issue_not_found", "未找到 Jira issue", "请检查 issue key"))
	}
	currentJiraUser, err := selection.Client.CurrentUser(context.Background())
	if err != nil {
		return writeJSON(stdout, output.Failure("takeover_task", "jira_current_user_failed", err.Error(), "请检查 Jira adapter 登录状态"))
	}
	decision := jira.ValidateTakeover(issue, workspaceProfile, currentJiraUser, agentID())
	if !decision.OK {
		_ = appendWorkspaceEventWithCode(workspaceName, "", issue.Key, "task_takeover", "takeover_task", decision.CurrentStage, decision.NextAction, decision.Code, "takeover_gate", false, true)
		return writeJSON(stdout, output.FailureWithContext("takeover_task", output.FailureContext{
			Code:                decision.Code,
			Message:             decision.Message,
			RequiredHumanAction: decision.RequiredHumanAction,
			TaskType:            "task_takeover",
			CurrentStage:        decision.CurrentStage,
			NextAction:          decision.NextAction,
		}))
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
		RunID:          runID,
		IssueKey:       issue.Key,
		TaskType:       "task_takeover",
		Operation:      "takeover_task",
		CurrentStage:   "takeover_started",
		NextAction:     "proceed",
		AgentID:        currentAgentID,
		CurrentAgentID: currentAgentID,
		TakeoverAt:     takeoverAt,
		TaskClass:      decision.TaskClass,
		ProcessID:      decision.ProcessID,
		OK:             true,
		Gate:           "takeover_task",
		GateStatus:     "passed",
	}); err != nil {
		return writeJSON(stdout, output.Failure("takeover_task", "event_write_failed", err.Error(), "请检查工作空间目录权限"))
	}
	return writeJSON(stdout, output.Success("takeover_task", map[string]any{
		"workspace":        workspaceName,
		"issue_key":        issue.Key,
		"run_id":           runID,
		"agent_id":         currentAgentID,
		"current_agent_id": currentAgentID,
		"takeover_at":      takeoverAt,
		"task_type":        "task_takeover",
		"task_class":       decision.TaskClass,
		"process_id":       decision.ProcessID,
		"current_stage":    "takeover_started",
		"target_repo":      issue.TargetRepo,
		"next_action":      "proceed",
	}))
}

func runResumeTakeover(args []string, stdout io.Writer) int {
	runID := readFlag(args, "--run-id", "")
	if runID == "" {
		return writeJSON(stdout, output.Failure("resume_takeover", "missing_run_id", "缺少 run_id", "请提供 --run-id"))
	}
	workspaceName := readFlag(args, "--workspace", "default")
	if err := appendWorkspaceEvent(workspaceName, runID, "", "task_takeover", "resume_takeover", "takeover_resumed", "continue_development", true, false); err != nil {
		return writeJSON(stdout, output.Failure("resume_takeover", "event_write_failed", err.Error(), "请检查工作空间目录权限"))
	}
	return writeJSON(stdout, output.Success("resume_takeover", map[string]any{
		"workspace":     workspaceName,
		"run_id":        runID,
		"task_type":     "task_takeover",
		"current_stage": "takeover_resumed",
		"next_action":   "continue_development",
	}))
}

func runWriteEvidence(args []string, stdout io.Writer) int {
	workspaceName := readFlag(args, "--workspace", "default")
	runID := readFlag(args, "--run-id", "")
	if runID == "" {
		_ = appendWorkspaceEventWithCode(workspaceName, "", "", "evidence_write", "write_evidence", "input_validation", "ask_owner", "missing_run_id", "input_validation", false, true)
		return writeJSON(stdout, output.FailureWithContext("write_evidence", output.FailureContext{
			Code:                "missing_run_id",
			Message:             "缺少 run_id",
			RequiredHumanAction: "请提供 --run-id",
			TaskType:            "evidence_write",
			CurrentStage:        "input_validation",
			NextAction:          "ask_owner",
		}))
	}
	root, err := workspaceRoot()
	if err != nil {
		return writeJSON(stdout, output.Failure("write_evidence", "workspace_root_failed", "无法读取当前工作目录", "请在项目 AI 工作空间中重试"))
	}
	path := filepath.Join(root, ".agentic-ops", "runs", runID, "evidence.md")
	content := fmt.Sprintf("# Evidence\n\n- workspace: %s\n- run_id: %s\n- status: evidence_written\n", workspaceName, runID)
	workspaceProfile := takeoverProfile(workspaceName)
	selection, err := selectJiraClient(workspaceName, workspaceProfile)
	if err != nil {
		return writeJSON(stdout, output.Failure("write_evidence", "jira_adapter_config_failed", err.Error(), "请检查 Jira adapter 配置"))
	}
	issueKey := readFlag(args, "--issue-key", "")
	if selection.Mode == "real" {
		if issueKey == "" {
			issueKey, err = issueKeyForRun(root, runID)
			if err != nil {
				return writeJSON(stdout, output.Failure("write_evidence", "run_not_found", err.Error(), "请检查 run_id 是否存在有效接管事件"))
			}
		}
		if issueKey == "" {
			return writeJSON(stdout, output.Failure("write_evidence", "run_not_found", "未找到 run_id 对应的 Jira issue", "请检查 run_id 是否存在有效接管事件"))
		}
		if !hasFlag(args, "--confirm-real-jira-write") {
			_ = appendRealJiraWriteGateEvent(workspaceName, runID, issueKey, "write_evidence", "evidence_write_gate", "ask_owner", "real_jira_confirmation_required", false, true)
			return writeJSON(stdout, output.FailureWithContext("write_evidence", output.FailureContext{
				Code:                "real_jira_confirmation_required",
				Message:             "真实 Jira comment 写入需要显式确认",
				RequiredHumanAction: "请确认 evidence 内容和 policy/gate 后添加 --confirm-real-jira-write",
				TaskType:            "evidence_write",
				CurrentStage:        "evidence_write_gate",
				NextAction:          "ask_owner",
			}))
		}
	}
	if err := evidence.Write(path, content); err != nil {
		return writeJSON(stdout, output.Failure("write_evidence", "write_failed", err.Error(), "请检查工作空间目录权限"))
	}
	if selection.Mode == "real" {
		if err := selection.Client.AddComment(context.Background(), issueKey, content); err != nil {
			_ = appendRealJiraWriteGateEvent(workspaceName, runID, issueKey, "write_evidence", "evidence_write_gate", "ask_owner", "jira_comment_write_failed", false, false)
			return writeJSON(stdout, output.Failure("write_evidence", "jira_comment_write_failed", err.Error(), "请检查 Jira comment 权限和 policy gate"))
		}
		if err := appendRealJiraWriteGateEvent(workspaceName, runID, issueKey, "write_evidence", "evidence_written", "request_owner_confirmation", "", true, false); err != nil {
			return writeJSON(stdout, output.Failure("write_evidence", "event_write_failed", err.Error(), "请检查工作空间目录权限"))
		}
	}
	if err := appendWorkspaceEvent(workspaceName, runID, "", "evidence_write", "write_evidence", "evidence_written", "request_owner_confirmation", true, true); err != nil {
		return writeJSON(stdout, output.Failure("write_evidence", "event_write_failed", err.Error(), "请检查工作空间目录权限"))
	}
	return writeJSON(stdout, output.Success("write_evidence", map[string]any{
		"workspace":     workspaceName,
		"run_id":        runID,
		"evidence":      path,
		"current_stage": "evidence_written",
		"next_action":   "request_owner_confirmation",
	}))
}

func runReleaseAgent(args []string, stdout io.Writer) int {
	workspaceName := readFlag(args, "--workspace", "default")
	runID := readFlag(args, "--run-id", "")
	if runID == "" {
		return writeJSON(stdout, output.FailureWithContext("release_agent", output.FailureContext{
			Code:                "missing_run_id",
			Message:             "缺少 run_id",
			RequiredHumanAction: "请提供 --run-id",
			TaskType:            "task_takeover",
			CurrentStage:        "completion_cleanup",
			NextAction:          "ask_owner",
		}))
	}
	issueKey := readFlag(args, "--issue-key", "")
	jiraTransitionID := readFlag(args, "--jira-transition-id", "")
	if issueKey == "" {
		return writeJSON(stdout, output.FailureWithContext("release_agent", output.FailureContext{
			Code:                "missing_issue_key",
			Message:             "缺少 issue key",
			RequiredHumanAction: "请提供 --issue-key",
			TaskType:            "task_takeover",
			CurrentStage:        "completion_cleanup",
			NextAction:          "ask_owner",
		}))
	}
	completionEvidence := readFlag(args, "--completion-evidence", "")
	if completionEvidence == "" {
		return writeJSON(stdout, output.FailureWithContext("release_agent", output.FailureContext{
			Code:                "missing_completion_evidence",
			Message:             "缺少完成证据",
			RequiredHumanAction: "请提供 --completion-evidence",
			TaskType:            "task_takeover",
			CurrentStage:        "completion_cleanup",
			NextAction:          "ask_owner",
		}))
	}
	currentAgentID := agentID()
	completedAt := fixedNow().Format(time.RFC3339)
	workspaceProfile := takeoverProfile(workspaceName)
	selection, err := selectJiraClient(workspaceName, workspaceProfile)
	if err != nil {
		return writeJSON(stdout, output.Failure("release_agent", "jira_adapter_config_failed", err.Error(), "请检查 Jira adapter 配置"))
	}
	if selection.Mode == "real" {
		if !hasFlag(args, "--confirm-real-jira-write") {
			_ = appendRealJiraWriteGateEvent(workspaceName, runID, issueKey, "release_agent", "completion_cleanup", "ask_owner", "real_jira_confirmation_required", false, true)
			return writeJSON(stdout, output.FailureWithContext("release_agent", output.FailureContext{
				Code:                "real_jira_confirmation_required",
				Message:             "真实 Jira 写入需要显式确认",
				RequiredHumanAction: "请确认完成证据和 policy/gate 后添加 --confirm-real-jira-write",
				TaskType:            "task_takeover",
				CurrentStage:        "completion_cleanup",
				NextAction:          "ask_owner",
			}))
		}
		issue, ok, err := selection.Client.GetIssueByKey(context.Background(), workspaceName, issueKey)
		if err != nil {
			return writeJSON(stdout, output.Failure("release_agent", "jira_issue_read_failed", err.Error(), "请检查 Jira adapter 配置和 issue 权限"))
		}
		if !ok {
			return writeJSON(stdout, output.Failure("release_agent", "issue_not_found", "未找到 Jira issue", "请检查 issue key"))
		}
		currentJiraUser, err := selection.Client.CurrentUser(context.Background())
		if err != nil {
			return writeJSON(stdout, output.Failure("release_agent", "jira_current_user_failed", err.Error(), "请检查 Jira adapter 登录状态"))
		}
		if issue.Assignee != currentJiraUser {
			return writeJSON(stdout, output.FailureWithContext("release_agent", output.FailureContext{
				Code:                "assignee_changed",
				Message:             "当前 Jira assignee 已不是当前用户",
				RequiredHumanAction: "请研发 owner 确认是否继续释放代理绑定",
				TaskType:            "task_takeover",
				CurrentStage:        "completion_cleanup",
				NextAction:          "ask_owner",
			}))
		}
		if issue.CurrentAgentID != currentAgentID {
			return writeJSON(stdout, output.FailureWithContext("release_agent", output.FailureContext{
				Code:                "agent_ownership_conflict",
				Message:             "当前 Jira issue 未绑定当前 AIAgent",
				RequiredHumanAction: "请研发 owner 确认是否释放当前代理绑定",
				TaskType:            "task_takeover",
				CurrentStage:        "completion_cleanup",
				NextAction:          "ask_owner",
			}))
		}
		if jiraTransitionID == "" {
			resolvedTransitionID, err := resolveJiraTransitionID(context.Background(), selection.Client, issueKey, workspaceProfile, "complete")
			if err != nil {
				_ = appendRealJiraWriteGateEvent(workspaceName, runID, issueKey, "release_agent", "jira_transition", "ask_owner", "jira_transition_mapping_gap", false, true)
				return writeJSON(stdout, output.FailureWithContext("release_agent", output.FailureContext{
					Code:                "jira_transition_mapping_gap",
					Message:             err.Error(),
					RequiredHumanAction: "请维护 workflow profile 的 jira_transition_mapping，或显式提供 --jira-transition-id",
					TaskType:            "task_takeover",
					CurrentStage:        "jira_transition",
					NextAction:          "ask_owner",
				}))
			}
			jiraTransitionID = resolvedTransitionID
		}
		fields := jiraReleaseFields(workspaceProfile)
		if len(fields) == 0 {
			_ = appendRealJiraWriteGateEvent(workspaceName, runID, issueKey, "release_agent", "completion_cleanup", "ask_owner", "missing_jira_write_mapping", false, true)
			return writeJSON(stdout, output.Failure("release_agent", "missing_jira_write_mapping", "缺少 current_agent_id 字段映射", "请维护 workflow profile 的所有权字段映射"))
		}
		if err := selection.Client.UpdateFields(context.Background(), issueKey, fields); err != nil {
			_ = appendRealJiraWriteGateEvent(workspaceName, runID, issueKey, "release_agent", "completion_cleanup", "ask_owner", "agent_release_failed", false, false)
			return writeJSON(stdout, output.Failure("release_agent", "agent_release_failed", err.Error(), "请检查 Jira 字段权限并由研发 owner 决策是否人工释放"))
		}
		if jiraTransitionID != "" {
			if err := selection.Client.TransitionIssue(context.Background(), issueKey, jiraTransitionID); err != nil {
				_ = appendRealJiraWriteGateEvent(workspaceName, runID, issueKey, "release_agent", "jira_transition", "ask_owner", "jira_transition_failed", false, false)
				return writeJSON(stdout, output.FailureWithContext("release_agent", output.FailureContext{
					Code:                "jira_transition_failed",
					Message:             err.Error(),
					RequiredHumanAction: "请检查 Jira transition 权限、transition id 和 workflow profile 映射",
					TaskType:            "task_takeover",
					CurrentStage:        "jira_transition",
					NextAction:          "ask_owner",
				}))
			}
			if err := appendRealJiraWriteGateEvent(workspaceName, runID, issueKey, "release_agent", "jira_transition", "completion_cleanup", "", true, false); err != nil {
				return writeJSON(stdout, output.Failure("release_agent", "event_write_failed", err.Error(), "请检查工作空间目录权限"))
			}
		}
		if err := appendRealJiraWriteGateEvent(workspaceName, runID, issueKey, "release_agent", "completed", "feedback_report", "", true, false); err != nil {
			return writeJSON(stdout, output.Failure("release_agent", "event_write_failed", err.Error(), "请检查工作空间目录权限"))
		}
	}
	if err := appendWorkspaceEventWithDetails(workspaceName, feedback.Event{
		RunID:                 runID,
		IssueKey:              issueKey,
		TaskType:              "task_takeover",
		Operation:             "release_agent",
		CurrentStage:          "completed",
		NextAction:            "feedback_report",
		AgentID:               currentAgentID,
		CurrentAgentID:        currentAgentID,
		CompletedAt:           completedAt,
		CompletionEvidence:    completionEvidence,
		CurrentAgentIDCleared: true,
		OK:                    true,
		Gate:                  "release_agent",
		GateStatus:            "passed",
	}); err != nil {
		return writeJSON(stdout, output.Failure("release_agent", "event_write_failed", err.Error(), "请检查工作空间目录权限"))
	}
	return writeJSON(stdout, output.Success("release_agent", map[string]any{
		"workspace":                workspaceName,
		"issue_key":                issueKey,
		"run_id":                   runID,
		"agent_id":                 currentAgentID,
		"current_agent_id_cleared": true,
		"jira_transition_id":       jiraTransitionID,
		"completed_at":             completedAt,
		"completion_evidence":      completionEvidence,
		"current_stage":            "completed",
		"next_action":              "feedback_report",
	}))
}

func resolveJiraTransitionID(ctx context.Context, client jira.Client, issueKey string, workspaceProfile profile.Profile, action string) (string, error) {
	transition, ok := workspaceProfile.JiraTransitionMapping[action]
	if !ok {
		return "", fmt.Errorf("jira transition mapping missing for %s", action)
	}
	if transition.ID != "" {
		return transition.ID, nil
	}
	if transition.Name == "" {
		return "", fmt.Errorf("jira transition id or name is required for %s", action)
	}
	transitions, err := client.Transitions(ctx, issueKey)
	if err != nil {
		return "", err
	}
	for _, candidate := range transitions {
		if candidate.Name == transition.Name {
			return candidate.ID, nil
		}
	}
	return "", fmt.Errorf("jira transition %q not found for %s", transition.Name, action)
}

func runFeedbackReport(args []string, stdout io.Writer) int {
	workspaceName := readFlag(args, "--workspace", "default")
	date := readFlag(args, "--date", time.Now().Format("2006-01-02"))
	root, err := workspaceRoot()
	if err != nil {
		return writeJSON(stdout, output.Failure("feedback_report", "workspace_root_failed", "无法读取当前工作目录", "请在项目 AI 工作空间中重试"))
	}
	events, err := feedback.ReadEvents(filepath.Join(root, ".agentic-ops", "feedback", "events.ndjson"))
	if err != nil {
		return writeJSON(stdout, output.Failure("feedback_report", "event_read_failed", err.Error(), "请检查工作空间反馈日志"))
	}
	report := feedback.Summarize(events)
	reportPath := filepath.Join(root, ".agentic-ops", "feedback", "daily", date+".md")
	if err := feedback.WriteMarkdown(reportPath, workspaceName, date, report); err != nil {
		return writeJSON(stdout, output.Failure("feedback_report", "report_write_failed", err.Error(), "请检查工作空间目录权限"))
	}
	return writeJSON(stdout, output.Success("feedback_report", map[string]any{
		"workspace":   workspaceName,
		"date":        date,
		"runs":        report.Runs,
		"succeeded":   report.Succeeded,
		"blocked":     report.Blocked,
		"failed":      report.Failed,
		"report":      reportPath,
		"next_action": "review_proposals",
	}))
}

func runFeedbackBundle(args []string, stdout io.Writer) int {
	workspaceName := readFlag(args, "--workspace", "default")
	runID := readFlag(args, "--run-id", "")
	if runID == "" {
		return writeJSON(stdout, output.FailureWithContext("feedback_bundle", output.FailureContext{
			Code:                "missing_run_id",
			Message:             "缺少 run_id",
			RequiredHumanAction: "请提供 --run-id",
			TaskType:            "diagnosis",
			CurrentStage:        "feedback_bundle",
			NextAction:          "ask_owner",
		}))
	}
	redact := hasFlag(args, "--redact")
	root, err := workspaceRoot()
	if err != nil {
		return writeJSON(stdout, output.Failure("feedback_bundle", "workspace_root_failed", "无法读取当前工作目录", "请在项目 AI 工作空间中重试"))
	}
	eventsPath := filepath.Join(root, ".agentic-ops", "feedback", "events.ndjson")
	rawEvents, err := os.ReadFile(eventsPath)
	if err != nil {
		return writeJSON(stdout, output.Failure("feedback_bundle", "event_read_failed", err.Error(), "请检查工作空间反馈日志"))
	}
	content := string(rawEvents)
	if redact {
		content = redactSensitive(content)
	}
	bundlePath := filepath.Join(root, ".agentic-ops", "feedback", "bundles", runID+".md")
	bundle := fmt.Sprintf("# Feedback Bundle\n\n- workspace: %s\n- run_id: %s\n- redacted: %t\n\n## Events\n\n```json\n%s\n```\n", workspaceName, runID, redact, content)
	if err := evidence.Write(bundlePath, bundle); err != nil {
		return writeJSON(stdout, output.Failure("feedback_bundle", "bundle_write_failed", err.Error(), "请检查工作空间目录权限"))
	}
	return writeJSON(stdout, output.Success("feedback_bundle", map[string]any{
		"workspace":   workspaceName,
		"run_id":      runID,
		"bundle":      bundlePath,
		"redacted":    redact,
		"next_action": "share_bundle_with_maintainer",
	}))
}

func writeJSON(stdout io.Writer, payload map[string]any) int {
	encoded, err := json.Marshal(payload)
	if err != nil {
		fmt.Fprintln(stdout, `{"ok":false,"operation":"internal","code":"json_encode_failed","message":"JSON 编码失败"}`)
		return 1
	}
	fmt.Fprintln(stdout, string(encoded))
	if ok, _ := payload["ok"].(bool); ok {
		return 0
	}
	return 1
}

func readFlag(args []string, name string, fallback string) string {
	for i := 0; i < len(args)-1; i++ {
		if args[i] == name {
			return args[i+1]
		}
	}
	return fallback
}

func hasFlag(args []string, name string) bool {
	for _, arg := range args {
		if arg == name {
			return true
		}
	}
	return false
}

func redactSensitive(value string) string {
	keyValuePattern := regexp.MustCompile(`(?i)(token|password|secret|authorization)=([^\s,"}]+)`)
	jsonPattern := regexp.MustCompile(`(?i)("(?:token|password|secret|authorization)"\s*:\s*")([^"]+)(")`)
	redacted := keyValuePattern.ReplaceAllString(value, `${1}=[REDACTED]`)
	redacted = jsonPattern.ReplaceAllString(redacted, `${1}[REDACTED]${3}`)
	return redacted
}

func readInstallDir(args []string) string {
	if installDir := readFlag(args, "--install-dir", ""); installDir != "" {
		return installDir
	}
	if installDir := os.Getenv("AGENTIC_OPS_HOME"); installDir != "" {
		return installDir
	}
	home, _ := os.UserHomeDir()
	return config.DefaultInstallDir(home)
}

func fixedNow() time.Time {
	return time.Date(2026, 7, 21, 10, 30, 12, 0, time.UTC)
}

func appendWorkspaceEvent(workspaceName string, runID string, issueKey string, taskType string, operation string, currentStage string, nextAction string, ok bool, requiresHumanAction bool) error {
	return appendWorkspaceEventWithCode(workspaceName, runID, issueKey, taskType, operation, currentStage, nextAction, "", operation, ok, requiresHumanAction)
}

func appendWorkspaceEventWithCode(workspaceName string, runID string, issueKey string, taskType string, operation string, currentStage string, nextAction string, code string, gate string, ok bool, requiresHumanAction bool) error {
	return appendWorkspaceEventWithDetails(workspaceName, feedback.Event{
		RunID:               runID,
		IssueKey:            issueKey,
		TaskType:            taskType,
		Operation:           operation,
		CurrentStage:        currentStage,
		NextAction:          nextAction,
		OK:                  ok,
		Code:                code,
		Gate:                gate,
		GateStatus:          gateStatus(ok, requiresHumanAction),
		HumanGate:           requiresHumanAction,
		RequiresHumanAction: requiresHumanAction,
	})
}

func appendWorkspaceEventWithDetails(workspaceName string, event feedback.Event) error {
	root, err := workspaceRoot()
	if err != nil {
		return err
	}
	event.Timestamp = fixedNow().Format(time.RFC3339)
	event.Workspace = workspaceName
	event.AgentTaskOpsVersion = Version
	event.VersionState = VersionState
	event.AssetVersion = readAssetVersion()
	return feedback.AppendEvent(filepath.Join(root, ".agentic-ops", "feedback", "events.ndjson"), event)
}

func appendRealJiraWriteGateEvent(workspaceName string, runID string, issueKey string, operation string, currentStage string, nextAction string, code string, ok bool, requiresHumanAction bool) error {
	taskType := "task_takeover"
	if operation == "write_evidence" {
		taskType = "evidence_write"
	}
	return appendWorkspaceEventWithDetails(workspaceName, feedback.Event{
		RunID:               runID,
		IssueKey:            issueKey,
		TaskType:            taskType,
		Operation:           operation,
		CurrentStage:        currentStage,
		NextAction:          nextAction,
		AgentID:             agentID(),
		CurrentAgentID:      agentID(),
		OK:                  ok,
		Code:                code,
		Gate:                "real_jira_write",
		GateStatus:          gateStatus(ok, requiresHumanAction),
		HumanGate:           true,
		RequiresHumanAction: requiresHumanAction,
	})
}

func issueKeyForRun(root string, runID string) (string, error) {
	events, err := feedback.ReadEvents(filepath.Join(root, ".agentic-ops", "feedback", "events.ndjson"))
	if err != nil {
		return "", err
	}
	for _, event := range events {
		if event.RunID == runID && event.IssueKey != "" {
			return event.IssueKey, nil
		}
	}
	return "", nil
}

func readAssetVersion() string {
	if version := os.Getenv("AGENTIC_OPS_ASSET_VERSION"); version != "" {
		return version
	}
	return "unknown"
}

func gateStatus(ok bool, requiresHumanAction bool) string {
	if ok {
		return "passed"
	}
	if requiresHumanAction {
		return "blocked"
	}
	return "failed"
}

func workspaceRoot() (string, error) {
	if root := os.Getenv("AGENTIC_OPS_WORKSPACE_ROOT"); root != "" {
		return root, nil
	}
	return os.Getwd()
}

func repoRoot() (string, error) {
	dir, err := os.Getwd()
	if err != nil {
		return "", err
	}
	for {
		if _, err := os.Stat(filepath.Join(dir, "go.mod")); err == nil {
			if _, err := os.Stat(filepath.Join(dir, "contracts", "operations")); err == nil {
				return dir, nil
			}
		}
		parent := filepath.Dir(dir)
		if parent == dir {
			return "", os.ErrNotExist
		}
		dir = parent
	}
}

func repoProfilePath(workspaceName string) (string, error) {
	root, err := repoRoot()
	if err != nil {
		return "", err
	}
	return filepath.Join(root, "profiles", workspaceName+".yaml"), nil
}

func repoPolicyPath() (string, error) {
	root, err := repoRoot()
	if err != nil {
		return "", err
	}
	return filepath.Join(root, "assets", "policies", "default.yaml"), nil
}

func checkInstallDir(installDir string) map[string]string {
	if installDir == "" {
		return map[string]string{"status": "failed", "message": "install dir is empty"}
	}
	if _, err := os.Stat(installDir); err != nil {
		return map[string]string{"status": "ok", "message": "install dir will be created when needed"}
	}
	return map[string]string{"status": "ok", "message": installDir}
}

func checkWorkspaceRoot() map[string]string {
	root, err := workspaceRoot()
	if err != nil {
		return map[string]string{"status": "failed", "message": err.Error()}
	}
	if _, err := os.Stat(root); err != nil {
		return map[string]string{"status": "failed", "message": err.Error()}
	}
	return map[string]string{"status": "ok", "message": root}
}

func checkProfile(workspaceName string) map[string]string {
	path, err := repoProfilePath(workspaceName)
	if err != nil {
		return map[string]string{"status": "failed", "message": "repo root not found"}
	}
	loadedProfile, err := profile.LoadFile(path)
	if err != nil {
		return map[string]string{"status": "failed", "message": err.Error()}
	}
	if issues := profile.Validate(loadedProfile); len(issues) > 0 {
		return map[string]string{"status": "failed", "message": issues[0].Code}
	}
	return map[string]string{"status": "ok", "message": path}
}

func checkPolicy() map[string]string {
	path, err := repoPolicyPath()
	if err != nil {
		return map[string]string{"status": "failed", "message": "repo root not found"}
	}
	loadedPolicy, err := policy.LoadFile(path)
	if err != nil {
		return map[string]string{"status": "failed", "message": err.Error()}
	}
	if issues := policy.Validate(loadedPolicy); len(issues) > 0 {
		return map[string]string{"status": "failed", "message": issues[0].Code}
	}
	return map[string]string{"status": "ok", "message": path}
}

func checkContracts() map[string]string {
	root, err := repoRoot()
	if err != nil {
		return map[string]string{"status": "failed", "message": "repo root not found"}
	}
	paths, err := filepath.Glob(filepath.Join(root, "contracts", "operations", "*.yaml"))
	if err != nil {
		return map[string]string{"status": "failed", "message": err.Error()}
	}
	if len(paths) == 0 {
		return map[string]string{"status": "failed", "message": "operation contracts not found"}
	}
	for _, path := range paths {
		op, err := contract.LoadFile(path)
		if err != nil {
			return map[string]string{"status": "failed", "message": err.Error()}
		}
		if issues := contract.Validate(op); len(issues) > 0 {
			return map[string]string{"status": "failed", "message": issues[0].Code}
		}
	}
	return map[string]string{"status": "ok", "message": fmt.Sprintf("%d operation contracts", len(paths))}
}

func takeoverProfile(workspaceName string) profile.Profile {
	if path, err := repoProfilePath(workspaceName); err == nil {
		if loadedProfile, err := profile.LoadFile(path); err == nil {
			return loadedProfile
		}
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

type jiraClientSelection struct {
	Client jira.Client
	Mode   string
}

var selectJiraClient = defaultJiraClient

func defaultJiraClient(workspaceName string, workspaceProfile profile.Profile) (jiraClientSelection, error) {
	if os.Getenv("AGENTIC_OPS_JIRA_ADAPTER") == "real" {
		client, err := jira.NewRealClient(jira.RealClientConfig{
			BaseURL:  os.Getenv("AGENTIC_OPS_JIRA_BASE_URL"),
			Email:    os.Getenv("AGENTIC_OPS_JIRA_EMAIL"),
			APIToken: os.Getenv("AGENTIC_OPS_JIRA_API_TOKEN"),
			Profile:  workspaceProfile,
		})
		if err != nil {
			return jiraClientSelection{}, err
		}
		return jiraClientSelection{Client: client, Mode: "real"}, nil
	}
	return jiraClientSelection{Client: jira.FakeClient{}, Mode: "fake"}, nil
}

func jiraTakeoverFields(workspaceProfile profile.Profile, currentAgentID string, takeoverAt string) map[string]any {
	fields := map[string]any{}
	if field := workspaceProfile.JiraFormMapping.Fields["current_agent_id"].JiraField; field != "" {
		fields[field] = currentAgentID
	}
	if field := workspaceProfile.JiraFormMapping.Fields["takeover_at"].JiraField; field != "" {
		fields[field] = takeoverAt
	}
	return fields
}

func jiraReleaseFields(workspaceProfile profile.Profile) map[string]any {
	fields := map[string]any{}
	if field := workspaceProfile.JiraFormMapping.Fields["current_agent_id"].JiraField; field != "" {
		fields[field] = nil
	}
	return fields
}

func currentUser() string {
	if value := os.Getenv("AGENTIC_OPS_CURRENT_USER"); value != "" {
		return value
	}
	return "current-user"
}

func agentID() string {
	if value := os.Getenv("AGENTIC_OPS_AGENT_ID"); value != "" {
		return value
	}
	return "agentic-cli-local-agent"
}
