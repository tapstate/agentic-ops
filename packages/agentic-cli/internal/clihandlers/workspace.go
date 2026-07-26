package clihandlers

import (
	"bufio"
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

func runWorkspaceInit(args []string, stdin io.Reader, stdout io.Writer, stderr io.Writer, interactiveAvailable bool) int {
	interactive := hasFlag(args, "--interactive")
	if interactive && !interactiveAvailable {
		return writeJSON(stdout, output.Failure("workspace_init", "interactive_terminal_required", "交互式初始化需要终端输入", "请在终端中运行，或改用完整参数形式"))
	}
	projectName := readFlag(args, "--project", "")
	workspaceName := readFlag(args, "--workspace", "")
	if projectName != "" && workspaceName != "" && projectName != workspaceName {
		return writeJSON(stdout, output.Failure("workspace_init", "project_workspace_mismatch", "项目配置项与工作空间名称不一致", "请只提供 --project，或让 --project 与 --workspace 使用相同值"))
	}
	if projectName == "" {
		projectName = workspaceName
	}
	prompter := workspaceInitPrompter{reader: bufio.NewReader(stdinOrEmpty(stdin)), writer: stderr}
	if interactive && projectName == "" {
		var err error
		projectName, err = prompter.promptRequired("Project", projectName)
		if err != nil {
			return writeJSON(stdout, output.Failure("workspace_init", "interactive_input_required", err.Error(), "请补齐交互式输入，或改用完整参数形式"))
		}
	}
	if projectName == "" {
		return writeJSON(stdout, output.Failure("workspace_init", "missing_project", "缺少项目配置项", "请提供 --project"))
	}
	jiraUser := readFlag(args, "--jira-user", "")
	jiraProjectOverride := readFlag(args, "--jira-project", "")
	agentType := readFlag(args, "--agent-type", "codex")
	confirmExistingConfig := hasFlag(args, "--confirm-existing-config")
	sourceRoot := readFlag(args, "--source-root", "")
	jiraBaseURL := readFlag(args, "--jira-base-url", "")
	jiraTokenEnv := readFlag(args, "--jira-token-env", "")
	if interactive {
		defaults := workspaceJiraPromptDefaults(projectName)
		var err error
		if jiraUser == "" {
			jiraUser, err = prompter.promptRequired("Jira user", defaults.Email)
			if err != nil {
				return writeJSON(stdout, output.Failure("workspace_init", "interactive_input_required", err.Error(), "请补齐交互式输入，或改用完整参数形式"))
			}
		}
		if jiraBaseURL == "" {
			jiraBaseURL, err = prompter.promptOptional("Jira base URL", defaults.BaseURL)
			if err != nil {
				return writeJSON(stdout, output.Failure("workspace_init", "interactive_input_failed", err.Error(), "请重新运行交互式初始化"))
			}
		}
		if jiraTokenEnv == "" && (jiraBaseURL != "" || defaults.APITokenEnv != "") {
			jiraTokenEnv, err = prompter.promptOptional("Jira token env", firstNonEmpty(defaults.APITokenEnv, "AGENTIC_OPS_JIRA_API_TOKEN"))
			if err != nil {
				return writeJSON(stdout, output.Failure("workspace_init", "interactive_input_failed", err.Error(), "请重新运行交互式初始化"))
			}
		}
	}
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
	if existing := existingWorkspaceConfigPaths(info); len(existing) > 0 && !confirmExistingConfig {
		return writeJSON(stdout, output.FailureWithContext("workspace_init", output.FailureContext{
			Code:                "existing_config_confirmation_required",
			Message:             "工作空间已有 AgenticOps 本地配置",
			RequiredHumanAction: "请确认是否覆盖已有配置；确认后使用 --confirm-existing-config 重新执行 workspace init",
			TaskType:            "workspace_initialization",
			CurrentStage:        "config_confirmation",
			NextAction:          "confirm_existing_config",
		}))
	}
	profileRef, profileOverlayPath, jiraProject, materializedSourceRoot, err := materializeWorkspaceProfile(info, jiraUser, jiraProjectOverride, sourceRoot)
	if err != nil {
		return writeJSON(stdout, output.FailureWithContext("workspace_init", output.FailureContext{
			Code:                "workspace_profile_failed",
			Message:             err.Error(),
			RequiredHumanAction: "请检查 install-resources/basic/projects/<project>/profile.yaml，并确认 workspace 与项目配置项一致、jira.project 已配置",
			TaskType:            "workspace_initialization",
			CurrentStage:        "workspace_profile",
			NextAction:          "fix_profile",
		}))
	}
	agentConfigPath, err := writeAgentConfig(info, jiraUser, jiraProject, profileRef, profileOverlayPath, agentType)
	if err != nil {
		return writeJSON(stdout, output.Failure("workspace_init", "agent_config_failed", err.Error(), "请检查工作空间目录权限"))
	}
	agentInstructionsPath, err := writeAgentInstructions(info, jiraUser, jiraProject, agentType)
	if err != nil {
		return writeJSON(stdout, output.Failure("workspace_init", "agent_instructions_failed", err.Error(), "请检查工作空间目录权限"))
	}
	jiraConfig, err := prepareWorkspaceJiraConfig(info, jiraUser, jiraBaseURL, jiraTokenEnv)
	if err != nil {
		return writeJSON(stdout, output.Failure("workspace_init", "jira_config_failed", err.Error(), "请检查个人配置目录权限"))
	}
	return writeJSON(stdout, output.Success("workspace_init", map[string]any{
		"workspace":               info.Name,
		"workspace_root":          info.Root,
		"source_root":             materializedSourceRoot,
		"jira_user":               jiraUser,
		"jira_project":            jiraProject,
		"profile_ref":             "$HOME/.agentic-ops/install-resources/basic/projects/" + info.Name + "/profile.yaml",
		"profile_overlay":         profileOverlayPath,
		"agent_config":            agentConfigPath,
		"agent_instructions":      agentInstructionsPath,
		"runs_dir":                info.RunsDir,
		"run_logs_dir":            info.RunLogsDir,
		"feedback_dir":            info.FeedbackDir,
		"jira_config_status":      jiraConfig.Status,
		"jira_config_path":        jiraConfig.Path,
		"jira_token_env":          jiraConfig.TokenEnv,
		"jira_config_next_action": jiraConfig.NextAction,
		"next_action":             "init_agent_capability",
	}))
}

type workspaceInitPrompter struct {
	reader *bufio.Reader
	writer io.Writer
}

func stdinOrEmpty(stdin io.Reader) io.Reader {
	if stdin != nil {
		return stdin
	}
	return strings.NewReader("")
}

func (prompter workspaceInitPrompter) promptRequired(label string, fallback string) (string, error) {
	value, err := prompter.promptOptional(label, fallback)
	if err != nil {
		return "", err
	}
	if value == "" {
		return "", fmt.Errorf("%s is required", label)
	}
	return value, nil
}

func (prompter workspaceInitPrompter) promptOptional(label string, fallback string) (string, error) {
	if fallback != "" {
		fmt.Fprintf(prompter.writer, "%s [%s]: ", label, fallback)
	} else {
		fmt.Fprintf(prompter.writer, "%s: ", label)
	}
	line, err := prompter.reader.ReadString('\n')
	if err != nil && len(line) == 0 {
		return "", err
	}
	value := strings.TrimSpace(line)
	if value == "" {
		return fallback, nil
	}
	return value, nil
}

func workspaceJiraPromptDefaults(workspaceName string) jiraRuntimeConfig {
	envDefaults := jiraRuntimeConfig{
		BaseURL:     os.Getenv("AGENTIC_OPS_JIRA_BASE_URL"),
		Email:       os.Getenv("AGENTIC_OPS_JIRA_EMAIL"),
		APITokenEnv: "AGENTIC_OPS_JIRA_API_TOKEN",
	}
	config, err := resolveJiraRuntimeConfig(workspaceName)
	if err != nil || config.Adapter != "real" {
		if envDefaults.BaseURL != "" || envDefaults.Email != "" {
			return envDefaults
		}
		return jiraRuntimeConfig{}
	}
	if config.BaseURL == "" {
		config.BaseURL = envDefaults.BaseURL
	}
	if config.Email == "" {
		config.Email = envDefaults.Email
	}
	if config.APITokenEnv == "" {
		config.APITokenEnv = envDefaults.APITokenEnv
	}
	return config
}

func firstNonEmpty(values ...string) string {
	for _, value := range values {
		if strings.TrimSpace(value) != "" {
			return strings.TrimSpace(value)
		}
	}
	return ""
}

type workspaceJiraConfigGuide struct {
	Status     string
	Path       string
	TokenEnv   string
	NextAction string
}

type jiraLocalConfigFile struct {
	Adapter     string `yaml:"adapter"`
	BaseURL     string `yaml:"base_url"`
	Email       string `yaml:"email"`
	APITokenEnv string `yaml:"api_token_env"`
}

func prepareWorkspaceJiraConfig(info workspace.Info, jiraUser string, jiraBaseURL string, jiraTokenEnv string) (workspaceJiraConfigGuide, error) {
	if strings.TrimSpace(jiraTokenEnv) == "" {
		jiraTokenEnv = "AGENTIC_OPS_JIRA_API_TOKEN"
	}
	configPath := filepath.Join(agenticOpsInstallDir(), "user", "projects", info.Name, "jira.local.yaml")
	jiraBaseURL = strings.TrimSpace(jiraBaseURL)
	if jiraBaseURL != "" {
		if err := writePersonalProjectJiraConfig(configPath, jiraUser, jiraBaseURL, jiraTokenEnv); err != nil {
			return workspaceJiraConfigGuide{}, err
		}
		status := "needs_token_env"
		nextAction := "set_jira_token_env"
		if os.Getenv(jiraTokenEnv) != "" {
			status = "configured"
			nextAction = "agent_init"
		}
		return workspaceJiraConfigGuide{Status: status, Path: configPath, TokenEnv: jiraTokenEnv, NextAction: nextAction}, nil
	}
	runtimeConfig, err := resolveJiraRuntimeConfig(info.Name)
	if err != nil {
		return workspaceJiraConfigGuide{}, err
	}
	if runtimeConfig.Adapter == "real" {
		status := "configured"
		nextAction := "agent_init"
		if runtimeConfig.APIToken == "" {
			status = "needs_token_env"
			nextAction = "set_jira_token_env"
		}
		return workspaceJiraConfigGuide{Status: status, Path: runtimeConfig.Source, TokenEnv: runtimeConfig.APITokenEnv, NextAction: nextAction}, nil
	}
	return workspaceJiraConfigGuide{
		Status:     "needs_configuration",
		Path:       configPath,
		TokenEnv:   jiraTokenEnv,
		NextAction: "rerun_workspace_init_with_--jira-base-url",
	}, nil
}

func writePersonalProjectJiraConfig(path string, jiraUser string, jiraBaseURL string, jiraTokenEnv string) error {
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		return err
	}
	data, err := yaml.Marshal(jiraLocalConfigFile{
		Adapter:     "real",
		BaseURL:     jiraBaseURL,
		Email:       jiraUser,
		APITokenEnv: jiraTokenEnv,
	})
	if err != nil {
		return err
	}
	return os.WriteFile(path, data, 0o600)
}

func materializeWorkspaceProfile(info workspace.Info, jiraUser string, jiraProjectOverride string, sourceRootOverride string) (string, string, string, string, error) {
	sourcePath, err := repoProjectProfilePath(info.Name)
	if err != nil {
		return "", "", "", "", err
	}
	loadedProfile, err := profile.LoadFile(sourcePath)
	if err != nil {
		return "", "", "", "", err
	}
	if loadedProfile.Workspace != info.Name {
		return "", "", "", "", fmt.Errorf("profile workspace %q does not match project %q", loadedProfile.Workspace, info.Name)
	}
	if jiraProjectOverride != "" && loadedProfile.Jira.Project != jiraProjectOverride {
		return "", "", "", "", fmt.Errorf("profile jira.project %q does not match %q", loadedProfile.Jira.Project, jiraProjectOverride)
	}
	sourceRoot := strings.TrimSpace(sourceRootOverride)
	if sourceRoot == "" {
		sourceRoot = filepath.Join(info.Root, "repos", info.Name)
	}
	overlay := map[string]any{
		"workspace": info.Name,
		"jira": map[string]any{
			"user": jiraUser,
		},
		"local": map[string]any{
			"workspace_root": info.Root,
			"source_root":    sourceRoot,
			"runs_dir":       info.RunsDir,
			"run_logs_dir":   info.RunLogsDir,
			"feedback_dir":   info.FeedbackDir,
		},
	}
	data, err := yaml.Marshal(overlay)
	if err != nil {
		return "", "", "", "", err
	}
	targetPath := filepath.Join(info.Root, ".agentic-ops", "profile.local.yaml")
	if err := os.WriteFile(targetPath, data, 0o644); err != nil {
		return "", "", "", "", err
	}
	effective, err := resolveEffectiveProfile(info.Name, info.Root)
	if err != nil {
		return "", "", "", "", err
	}
	if issues := profile.Validate(effective); len(issues) > 0 {
		return "", "", "", "", fmt.Errorf("workflow profile validation failed: %s", issues[0].Code)
	}
	registry, err := repoProcessRegistry()
	if err != nil {
		return "", "", "", "", err
	}
	if issues := profile.ValidateProcesses(effective, registry); len(issues) > 0 {
		return "", "", "", "", fmt.Errorf("workflow profile process validation failed: %s", issues[0].Code)
	}
	return sourcePath, targetPath, effective.Jira.Project, sourceRoot, nil
}

func existingWorkspaceConfigPaths(info workspace.Info) []string {
	candidates := []string{
		filepath.Join(info.Root, ".agentic-ops", "agent.json"),
		filepath.Join(info.Root, ".agentic-ops", "profile.local.yaml"),
	}
	var existing []string
	for _, path := range candidates {
		if stat, err := os.Stat(path); err == nil && !stat.IsDir() {
			existing = append(existing, path)
		}
	}
	if data, err := os.ReadFile(filepath.Join(info.Root, "AGENTS.md")); err == nil && strings.Contains(string(data), "BEGIN AGENTICOPS MANAGED BLOCK") {
		existing = append(existing, filepath.Join(info.Root, "AGENTS.md"))
	}
	return existing
}

func writeAgentConfig(info workspace.Info, jiraUser string, jiraProject string, profileRef string, profileOverlayPath string, agentType string) (string, error) {
	configPath := filepath.Join(info.Root, ".agentic-ops", "agent.json")
	data, err := json.Marshal(agentConfig{
		Workspace:      info.Name,
		Project:        info.Name,
		JiraUser:       jiraUser,
		JiraProject:    jiraProject,
		Profile:        profileOverlayPath,
		ProfileRef:     "$HOME/.agentic-ops/install-resources/basic/projects/" + info.Name + "/profile.yaml",
		ProfileOverlay: profileOverlayPath,
		AgentType:      agentType,
		AgentID:        agentID(),
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
		"4. Use `agentic-cli list-tasks` to find available Jira tasks from the real Jira adapter; do not use sample or fake Jira tasks for business work.",
		"5. If Jira adapter config is missing, ask the development lead to provide runtime local config through environment variables, `.agentic-ops/jira.local.yaml`, `$AGENTIC_OPS_HOME/user/projects/<workspace>/jira.local.yaml`, or `$AGENTIC_OPS_HOME/user/jira.local.yaml`; never use fake Jira for business work.",
		"6. Use `agentic-cli task run <issue-key>` to take over a task and start the matched capability.",
		"7. Use `agentic-cli takeover-task <issue-key>` only when you need the lower-level takeover operation.",
		"8. Use `agentic-cli write-evidence --run-id <run-id>` and `agentic-cli release-agent --run-id <run-id> --issue-key <issue-key> --completion-evidence <file>` to finish or hand off work.",
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
	projectTools := []string{}
	if workspaceName == "tapdata" {
		projectTools = append(projectTools, "tapdata branch-align")
	}
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
		"asset_resolution": map[string]any{
			"order": []string{
				"workspace_overlay",
				"personal",
				"project_package",
				"company",
				"builtin",
			},
			"workspace_overlay": ".agentic-ops/profile.local.yaml",
			"personal":          "$HOME/.agentic-ops/user/",
			"project_package":   "$HOME/.agentic-ops/install-resources/basic/projects/" + workspaceName + "/",
			"company":           "$HOME/.agentic-ops/install-resources/basic/company/",
			"builtin":           "agentic-cli",
		},
		"project_tools": projectTools,
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
			"switch_branch",
			"tapdata branch-align",
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
	Workspace      string `json:"workspace"`
	Project        string `json:"project"`
	JiraUser       string `json:"jira_user"`
	JiraProject    string `json:"jira_project"`
	Profile        string `json:"profile"`
	ProfileRef     string `json:"profile_ref"`
	ProfileOverlay string `json:"profile_overlay"`
	AgentType      string `json:"agent_type"`
	AgentID        string `json:"agent_id"`
}
