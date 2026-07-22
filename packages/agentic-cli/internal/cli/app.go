package cli

import (
	"encoding/json"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"time"

	"github.com/tapstate/agentic-ops/packages/agentic-cli/internal/assets"
	"github.com/tapstate/agentic-ops/packages/agentic-cli/internal/config"
	"github.com/tapstate/agentic-ops/packages/agentic-cli/internal/contract"
	"github.com/tapstate/agentic-ops/packages/agentic-cli/internal/evidence"
	"github.com/tapstate/agentic-ops/packages/agentic-cli/internal/feedback"
	"github.com/tapstate/agentic-ops/packages/agentic-cli/internal/jira"
	"github.com/tapstate/agentic-ops/packages/agentic-cli/internal/output"
	"github.com/tapstate/agentic-ops/packages/agentic-cli/internal/profile"
	"github.com/tapstate/agentic-ops/packages/agentic-cli/internal/workspace"
)

var Version = "SRC-source"
var VersionState = "SRC"
var IterationVersion = "source"
var CommitIndex = "0"
var Commit = "unknown"
var BuildTime = ""

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
	case "list-tasks":
		return runListTasks(args, stdout)
	case "takeover-task":
		return runTakeoverTask(args, stdout)
	case "resume-takeover":
		return runResumeTakeover(args, stdout)
	case "write-evidence":
		return runWriteEvidence(args, stdout)
	case "feedback":
		if len(args) >= 2 && args[1] == "report" {
			return runFeedbackReport(args, stdout)
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
			"assets_install",
			"contract_validate",
			"profile_validate",
			"profile_update",
			"profile_rollback",
			"workspace_init",
			"list_tasks",
			"takeover_task",
			"resume_takeover",
			"write_evidence",
			"feedback_report",
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

func runListTasks(args []string, stdout io.Writer) int {
	workspaceName := readFlag(args, "--workspace", "default")
	issues := jira.FakeClient{}.ListTasks(workspaceName)
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

func runTakeoverTask(args []string, stdout io.Writer) int {
	if len(args) < 2 {
		return writeJSON(stdout, output.Failure("takeover_task", "missing_issue_key", "缺少 issue key", "请提供 Jira issue key"))
	}
	workspaceName := readFlag(args, "--workspace", "default")
	issueKey := args[1]
	issue, ok := jira.FakeClient{}.GetIssue(workspaceName, issueKey)
	if !ok {
		return writeJSON(stdout, output.Failure("takeover_task", "issue_not_found", "未找到 Jira issue", "请检查 issue key"))
	}
	workspaceProfile := takeoverProfile(workspaceName)
	decision := jira.ValidateTakeover(issue, workspaceProfile, currentUser(), agentID())
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
	if err := evidence.Write(path, content); err != nil {
		return writeJSON(stdout, output.Failure("write_evidence", "write_failed", err.Error(), "请检查工作空间目录权限"))
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
	}
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
