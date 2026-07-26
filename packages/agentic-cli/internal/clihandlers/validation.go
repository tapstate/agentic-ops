package clihandlers

import (
	"github.com/tapstate/agentic-ops/packages/agentic-cli/internal/contract"
	"github.com/tapstate/agentic-ops/packages/agentic-cli/internal/output"
	"github.com/tapstate/agentic-ops/packages/agentic-cli/internal/policy"
	"github.com/tapstate/agentic-ops/packages/agentic-cli/internal/profile"
	"io"
	"path/filepath"
)

func runContractValidate(args []string, stdout io.Writer) int {
	root, err := repoRoot()
	if err != nil {
		return writeJSON(stdout, output.Failure("contract_validate", "repo_root_not_found", "未找到仓库根目录", "请在 AgenticOps 仓库内运行"))
	}
	paths, err := filepath.Glob(filepath.Join(repoBasicResourcesPath(root), "contracts", "operations", "*.yaml"))
	if err != nil {
		return writeJSON(stdout, output.Failure("contract_validate", "contract_glob_failed", err.Error(), "请检查 install-resources/basic/contracts/operations 目录"))
	}
	if len(paths) == 0 {
		return writeJSON(stdout, output.Failure("contract_validate", "contract_not_found", "未找到 operation contract", "请检查 install-resources/basic/contracts/operations 目录"))
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
			RequiredHumanAction: "请修复 install-resources/basic/contracts/operations 中的契约字段",
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
	workspaceName := workspaceNameFromArgsOrAgentConfig(args, "")
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
	profilePath, err := repoProjectProfilePath(workspaceName)
	if err != nil {
		return writeJSON(stdout, output.Failure("profile_validate", "repo_root_not_found", "未找到仓库根目录", "请在 AgenticOps 仓库内运行"))
	}
	loadedProfile, err := profile.LoadFile(profilePath)
	if err != nil {
		return writeJSON(stdout, output.FailureWithContext("profile_validate", output.FailureContext{
			Code:                "profile_not_found",
			Message:             err.Error(),
			RequiredHumanAction: "请检查 install-resources/basic/profiles 目录中的 workspace 配置",
			TaskType:            "profile_validation",
			CurrentStage:        "profile_validation",
			NextAction:          "fix_profile",
		}))
	}
	issues := profile.Validate(loadedProfile)
	if len(issues) == 0 {
		registry, err := repoProcessRegistry()
		if err != nil {
			issues = append(issues, profile.ValidationIssue{Code: "standard_process_missing", Message: err.Error()})
		} else {
			issues = append(issues, profile.ValidateProcesses(loadedProfile, registry)...)
		}
	}
	if len(issues) > 0 {
		return writeJSON(stdout, output.FailureWithContext("profile_validate", output.FailureContext{
			Code:                "profile_validation_failed",
			Message:             "workflow profile validation failed: " + issues[0].Code,
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

func runProfileResolve(args []string, stdout io.Writer) int {
	workspaceName := workspaceNameFromArgsOrAgentConfig(args, "")
	if workspaceName == "" {
		return writeJSON(stdout, output.FailureWithContext("profile_resolve", output.FailureContext{
			Code:                "missing_workspace",
			Message:             "缺少 workspace",
			RequiredHumanAction: "请提供 --workspace 或 --project",
			TaskType:            "profile_resolution",
			CurrentStage:        "profile_resolution",
			NextAction:          "ask_owner",
		}))
	}
	workspaceRoot, err := workspaceRoot()
	if err != nil {
		return writeJSON(stdout, output.Failure("profile_resolve", "workspace_root_failed", "无法读取当前工作目录", "请在项目 AI 工作空间中重试"))
	}
	resolution, err := resolveProfileWithLayers(workspaceName, workspaceRoot)
	if err != nil {
		return writeJSON(stdout, output.FailureWithContext("profile_resolve", output.FailureContext{
			Code:                "profile_resolve_failed",
			Message:             err.Error(),
			RequiredHumanAction: "请检查公司层、项目层、个人层和工作空间 overlay 是否存在且格式正确",
			TaskType:            "profile_resolution",
			CurrentStage:        "profile_resolution",
			NextAction:          "fix_profile_layers",
		}))
	}
	issues := profile.Validate(resolution.Effective)
	if len(issues) == 0 {
		registry, err := repoProcessRegistry()
		if err != nil {
			issues = append(issues, profile.ValidationIssue{Code: "standard_process_missing", Message: err.Error()})
		} else {
			issues = append(issues, profile.ValidateProcesses(resolution.Effective, registry)...)
		}
	}
	if len(issues) > 0 {
		return writeJSON(stdout, output.FailureWithContext("profile_resolve", output.FailureContext{
			Code:                "profile_resolve_validation_failed",
			Message:             "effective profile validation failed: " + issues[0].Code,
			RequiredHumanAction: "请修复 profile 分层配置或 overlay 覆盖字段",
			TaskType:            "profile_resolution",
			CurrentStage:        "profile_resolution",
			NextAction:          "fix_profile_layers",
		}))
	}
	return writeJSON(stdout, output.Success("profile_resolve", map[string]any{
		"workspace":    resolution.Effective.Workspace,
		"jira_user":    resolution.Effective.Jira.User,
		"jira_project": resolution.Effective.Jira.Project,
		"source_root":  resolution.Effective.Local.SourceRoot,
		"layers":       resolution.Layers,
		"next_action":  "continue",
	}))
}

func runProfileUpdate(args []string, stdout io.Writer) int {
	workspaceName := workspaceNameFromArgsOrAgentConfig(args, "")
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
	workspaceName := workspaceNameFromArgsOrAgentConfig(args, "")
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
	workspaceName := workspaceNameFromArgsOrAgentConfig(args, "")
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
			RequiredHumanAction: "请检查 install-resources/basic/policies/default.yaml",
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
	workspaceName := workspaceNameFromArgsOrAgentConfig(args, "")
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
	workspaceName := workspaceNameFromArgsOrAgentConfig(args, "")
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
