package clihandlers

import (
	"encoding/json"
	"errors"
	"fmt"
	"github.com/tapstate/agentic-ops/packages/agentic-cli/internal/output"
	"github.com/tapstate/agentic-ops/packages/agentic-cli/internal/profile"
	"github.com/tapstate/agentic-ops/packages/agentic-cli/internal/workspace"
	"gopkg.in/yaml.v3"
	"io"
	"os"
	"path/filepath"
	"strings"
)

func runWorkspaceInit(args []string, stdout io.Writer) int {
	projectName := readFlag(args, "--project", "")
	workspaceName := readFlag(args, "--workspace", "")
	if projectName != "" && workspaceName != "" && projectName != workspaceName {
		return writeJSON(stdout, output.Failure("workspace_init", "project_workspace_mismatch", "项目配置项与工作空间名称不一致", "请只提供 --project，或让 --project 与 --workspace 使用相同值"))
	}
	if projectName == "" {
		projectName = workspaceName
	}
	if projectName == "" {
		return writeJSON(stdout, output.Failure("workspace_init", "missing_project", "缺少项目配置项", "请提供 --project"))
	}
	jiraUser := readFlag(args, "--jira-user", "")
	jiraProjectOverride := readFlag(args, "--jira-project", "")
	agentType := readFlag(args, "--agent-type", "codex")
	if jiraUser == "" {
		return writeJSON(stdout, output.Failure("workspace_init", "missing_jira_user", "缺少 Jira 用户", "请提供 --jira-user"))
	}
	root, err := workspaceRoot()
	if err != nil {
		return writeJSON(stdout, output.Failure("workspace_init", "workspace_root_failed", "无法读取当前工作目录", "请在项目 AI 工作空间中重试"))
	}
	info, err := workspace.Ensure(root, projectName)
	if err != nil {
		return writeJSON(stdout, output.Failure("workspace_init", "workspace_init_failed", err.Error(), "请检查工作空间目录权限"))
	}
	profilePath, jiraProject, err := materializeWorkspaceProfile(info, jiraUser, jiraProjectOverride)
	if err != nil {
		return writeJSON(stdout, output.FailureWithContext("workspace_init", output.FailureContext{
			Code:                "workspace_profile_failed",
			Message:             err.Error(),
			RequiredHumanAction: "请检查 install-resources/basic/profiles/<project>.yaml，并确认 workspace 与项目配置项一致、jira.project 已配置",
			TaskType:            "workspace_initialization",
			CurrentStage:        "workspace_profile",
			NextAction:          "fix_profile",
		}))
	}
	agentConfigPath, err := writeAgentConfig(info, jiraUser, jiraProject, profilePath, agentType)
	if err != nil {
		return writeJSON(stdout, output.Failure("workspace_init", "agent_config_failed", err.Error(), "请检查工作空间目录权限"))
	}
	agentInstructionsPath, err := writeAgentInstructions(info, jiraUser, jiraProject, agentType)
	if err != nil {
		return writeJSON(stdout, output.Failure("workspace_init", "agent_instructions_failed", err.Error(), "请检查工作空间目录权限"))
	}
	return writeJSON(stdout, output.Success("workspace_init", map[string]any{
		"workspace":          info.Name,
		"workspace_root":     info.Root,
		"jira_user":          jiraUser,
		"jira_project":       jiraProject,
		"profile":            profilePath,
		"agent_config":       agentConfigPath,
		"agent_instructions": agentInstructionsPath,
		"runs_dir":           info.RunsDir,
		"run_logs_dir":       info.RunLogsDir,
		"feedback_dir":       info.FeedbackDir,
		"next_action":        "init_agent_capability",
	}))
}

func materializeWorkspaceProfile(info workspace.Info, jiraUser string, jiraProjectOverride string) (string, string, error) {
	sourcePath, err := repoProfilePath(info.Name)
	if err != nil {
		return "", "", err
	}
	loadedProfile, err := profile.LoadFile(sourcePath)
	if err != nil {
		return "", "", err
	}
	if loadedProfile.Workspace != info.Name {
		return "", "", fmt.Errorf("profile workspace %q does not match project %q", loadedProfile.Workspace, info.Name)
	}
	if jiraProjectOverride != "" && loadedProfile.Jira.Project != jiraProjectOverride {
		return "", "", fmt.Errorf("profile jira.project %q does not match %q", loadedProfile.Jira.Project, jiraProjectOverride)
	}
	loadedProfile.Jira.User = jiraUser
	if issues := profile.Validate(loadedProfile); len(issues) > 0 {
		return "", "", fmt.Errorf("workflow profile validation failed: %s", issues[0].Code)
	}
	registry, err := repoProcessRegistry()
	if err != nil {
		return "", "", err
	}
	if issues := profile.ValidateProcesses(loadedProfile, registry); len(issues) > 0 {
		return "", "", fmt.Errorf("workflow profile process validation failed: %s", issues[0].Code)
	}
	data, err := yaml.Marshal(loadedProfile)
	if err != nil {
		return "", "", err
	}
	targetPath := filepath.Join(info.ProfilesDir, info.Name+".yaml")
	if err := os.WriteFile(targetPath, data, 0o644); err != nil {
		return "", "", err
	}
	return targetPath, loadedProfile.Jira.Project, nil
}

func writeAgentConfig(info workspace.Info, jiraUser string, jiraProject string, profilePath string, agentType string) (string, error) {
	configPath := filepath.Join(info.Root, ".agentic-ops", "agent.json")
	data, err := json.Marshal(agentConfig{
		Workspace:   info.Name,
		Project:     info.Name,
		JiraUser:    jiraUser,
		JiraProject: jiraProject,
		Profile:     profilePath,
		AgentType:   agentType,
		AgentID:     agentID(),
	})
	if err != nil {
		return "", err
	}
	if err := os.WriteFile(configPath, data, 0o644); err != nil {
		return "", err
	}
	return configPath, nil
}

func writeAgentInstructions(info workspace.Info, jiraUser string, jiraProject string, agentType string) (string, error) {
	path := filepath.Join(info.Root, "AGENTS.md")
	block := fmt.Sprintf(strings.Join([]string{
		"<!-- BEGIN AGENTICOPS MANAGED BLOCK -->",
		"# AgenticOps workspace instructions",
		"",
		"This directory is an AgenticOps project AI workspace.",
		"",
		"- project: %s",
		"- workspace: %s",
		"- jira_user: %s",
		"- jira_project: %s",
		"- agent_type: %s",
		"- local_config: .agentic-ops/agent.json",
		"",
		"Activation phrase from the development lead:",
		"",
		"```text",
		"按 ~/.agentic-ops/agent-guides.md 启用 AgenticOps。",
		"```",
		"",
		"Do not rely on private wiki, local Obsidian vaults, or prior chat memory to initialize AgenticOps. Initialize from `$HOME/.agentic-ops/agent-guides.md`, this workspace's `.agentic-ops/agent.json`, and the installed AgenticOps assets under `$HOME/.agentic-ops/install-resources/basic/`.",
		"",
		"When starting work in this directory:",
		"",
		"1. Run `agentic-cli agent init` to load AgenticOps capabilities.",
		"2. Run `agentic-cli preflight` before taking over Jira tasks.",
		"3. Read `$HOME/.agentic-ops/agent-guides.md` and `$HOME/.agentic-ops/install-resources/basic/ai-assets/README.md` before executing tasks.",
		"4. Use `agentic-cli list-tasks` to find available Jira tasks.",
		"5. Use `agentic-cli task run <issue-key>` to take over a task and start the matched capability.",
		"6. Use `agentic-cli takeover-task <issue-key>` only when you need the lower-level takeover operation.",
		"7. Use `agentic-cli write-evidence --run-id <run-id>` and `agentic-cli release-agent --run-id <run-id> --issue-key <issue-key> --completion-evidence <file>` to finish or hand off work.",
		"",
		"Do not guess Jira fields, repositories, workflow states, or evidence format. Use AgenticOps profiles, operation contracts, policies, templates, and runbooks.",
		"",
		"Human confirmation is required before real Jira writes, Git push, pull request creation or update, merge, release, or scope changes.",
		"<!-- END AGENTICOPS MANAGED BLOCK -->",
		"",
	}, "\n"), info.Name, info.Name, jiraUser, jiraProject, agentType)
	content := block
	if existing, err := os.ReadFile(path); err == nil {
		content = mergeAgentInstructions(string(existing), block)
	} else if !errors.Is(err, os.ErrNotExist) {
		return "", err
	}
	if err := os.WriteFile(path, []byte(content), 0o644); err != nil {
		return "", err
	}
	return path, nil
}

func mergeAgentInstructions(existing string, block string) string {
	const begin = "<!-- BEGIN AGENTICOPS MANAGED BLOCK -->"
	const end = "<!-- END AGENTICOPS MANAGED BLOCK -->"
	start := strings.Index(existing, begin)
	if start >= 0 {
		finish := strings.Index(existing[start:], end)
		if finish >= 0 {
			finish += start + len(end)
			merged := existing[:start] + strings.TrimRight(block, "\n") + existing[finish:]
			return strings.TrimRight(merged, "\n") + "\n"
		}
	}
	trimmed := strings.TrimRight(existing, "\n")
	if trimmed == "" {
		return block
	}
	return trimmed + "\n\n" + block
}

func workspaceNameFromArgsOrAgentConfig(args []string, fallback string) string {
	if workspaceName := readFlag(args, "--workspace", ""); workspaceName != "" {
		return workspaceName
	}
	if projectName := readFlag(args, "--project", ""); projectName != "" {
		return projectName
	}
	root, err := workspaceRoot()
	if err != nil {
		return fallback
	}
	data, err := os.ReadFile(filepath.Join(root, ".agentic-ops", "agent.json"))
	if err != nil {
		return fallback
	}
	var config agentConfig
	if err := json.Unmarshal(data, &config); err != nil {
		return fallback
	}
	if strings.TrimSpace(config.Workspace) != "" {
		return config.Workspace
	}
	if strings.TrimSpace(config.Project) != "" {
		return config.Project
	}
	return fallback
}

func runAgentInit(args []string, stdout io.Writer) int {
	workspaceName := workspaceNameFromArgsOrAgentConfig(args, "default")
	return writeJSON(stdout, output.Success("agent_init", map[string]any{
		"workspace":          workspaceName,
		"task_type":          "capability_initialization",
		"current_stage":      "agent_capability_initialized",
		"next_action":        "list_tasks",
		"activation_phrase":  "按 ~/.agentic-ops/agent-guides.md 启用 AgenticOps。",
		"guide_entry":        "$HOME/.agentic-ops/agent-guides.md",
		"asset_entry":        "$HOME/.agentic-ops/install-resources/basic/ai-assets/README.md",
		"instruction_source": "agent_guides_and_workspace_state",
		"memory_dependency":  false,
		"human_gates": []string{
			"real_jira_write",
			"git_push",
			"create_pr",
			"update_pr",
			"merge",
			"release",
			"scope_change",
		},
		"next_steps": []string{
			"read_guide_entry",
			"read_asset_entry",
			"run_preflight",
			"list_tasks",
		},
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
			"task_run",
			"takeover_task",
			"resume_takeover",
			"write_evidence",
			"release_agent",
			"inspect_workspace",
			"prepare_pr",
			"read_pr_comments",
			"check_ci_status",
			"fix_pr_comments",
			"feedback_report",
			"feedback_bundle",
		},
	}))
}

type agentConfig struct {
	Workspace   string `json:"workspace"`
	Project     string `json:"project"`
	JiraUser    string `json:"jira_user"`
	JiraProject string `json:"jira_project"`
	Profile     string `json:"profile"`
	AgentType   string `json:"agent_type"`
	AgentID     string `json:"agent_id"`
}
