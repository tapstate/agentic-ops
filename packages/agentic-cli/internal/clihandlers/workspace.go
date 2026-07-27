package clihandlers

import (
	"bufio"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"strings"

	"github.com/tapstate/agentic-ops/packages/agentic-cli/internal/jira"
	"github.com/tapstate/agentic-ops/packages/agentic-cli/internal/output"
	"github.com/tapstate/agentic-ops/packages/agentic-cli/internal/profile"
	"github.com/tapstate/agentic-ops/packages/agentic-cli/internal/runtimeconfig"
	"github.com/tapstate/agentic-ops/packages/agentic-cli/internal/workspace"

	"gopkg.in/yaml.v3"
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
	promptInput := stdinOrEmpty(stdin)
	prompter := workspaceInitPrompter{reader: bufio.NewReader(promptInput), input: promptInput, writer: stderr}
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
	jiraAPIToken := ""
	if strings.TrimSpace(jiraTokenEnv) != "" && strings.TrimSpace(jiraTokenEnv) != jiraAPITokenEnvName {
		return writeJSON(stdout, output.FailureWithContext("workspace_init", output.FailureContext{
			Code:                "unsupported_jira_token_env_name",
			Message:             "Jira API token 配置名已统一为 " + jiraAPITokenEnvName,
			RequiredHumanAction: "请把真实 Jira API token 写入 ~/.agentic-ops/user/.env 中的 " + jiraAPITokenEnvName,
			TaskType:            "workspace_initialization",
			CurrentStage:        "jira_config",
			NextAction:          "set_jira_api_token",
		}))
	}
	jiraTokenEnv = jiraAPITokenEnvName
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
		if jiraBaseURL != "" || defaults.BaseURL != "" {
			if !jiraTokenConfiguredInFiles(jiraTokenEnv, jiraRuntimeEnvFiles(runtimeConfigScope(projectName))) {
				jiraAPIToken, err = prompter.promptSecretOptional("Jira API token", "")
				if err != nil {
					return writeJSON(stdout, output.Failure("workspace_init", "interactive_input_failed", err.Error(), "请重新运行交互式初始化"))
				}
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
	profileRef, profileOverlayPath, jiraProject, materializedSourceRoot, sourceRepo, err := materializeWorkspaceProfile(info, jiraUser, jiraProjectOverride, sourceRoot)
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
	sourceCheckout, err := ensureSourceCheckout(materializedSourceRoot, sourceRepo)
	if err != nil {
		return writeJSON(stdout, output.FailureWithContext("workspace_init", output.FailureContext{
			Code:                "source_checkout_failed",
			Message:             err.Error(),
			RequiredHumanAction: "请检查 GitHub 仓库访问权限、SSH key 或使用 --source-root 指向已有本地源码目录",
			TaskType:            "workspace_initialization",
			CurrentStage:        "source_checkout",
			NextAction:          "fix_source_checkout",
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
	jiraConfig, err := prepareWorkspaceJiraConfig(info, jiraUser, jiraBaseURL, jiraTokenEnv, jiraAPIToken)
	if err != nil {
		if errors.Is(err, errInvalidJiraTokenEnvName) {
			return writeJSON(stdout, output.FailureWithContext("workspace_init", output.FailureContext{
				Code:                "invalid_jira_token_env_name",
				Message:             "Jira API token 配置名异常",
				RequiredHumanAction: "请把真实 Jira API token 写入 ~/.agentic-ops/user/.env 中的 " + jiraAPITokenEnvName,
				TaskType:            "workspace_initialization",
				CurrentStage:        "jira_config",
				NextAction:          "set_jira_api_token",
			}))
		}
		return writeJSON(stdout, output.Failure("workspace_init", "jira_config_failed", err.Error(), "请检查个人配置目录权限"))
	}
	payload := output.Success("workspace_init", map[string]any{
		"workspace":               info.Name,
		"workspace_root":          info.Root,
		"source_root":             materializedSourceRoot,
		"source_repo":             sourceCheckout.Repo,
		"source_repo_url":         sourceCheckout.RepoURL,
		"source_checkout_status":  sourceCheckout.Status,
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
		"jira_env_file":           jiraConfig.EnvFile,
		"jira_config_next_action": jiraConfig.NextAction,
		"next_action":             "init_agent_capability",
	})
	addJiraTokenGuidance(payload, jiraConfig.Status, jiraConfig.TokenEnv, jiraConfig.EnvFile)
	return writeJSON(stdout, payload)
}

type workspaceInitPrompter struct {
	reader *bufio.Reader
	input  io.Reader
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

func (prompter workspaceInitPrompter) promptSecretOptional(label string, fallback string) (string, error) {
	if file, ok := prompter.input.(*os.File); ok && isTerminalFile(file) {
		if fallback != "" {
			fmt.Fprintf(prompter.writer, "%s [configured]: ", label)
		} else {
			fmt.Fprintf(prompter.writer, "%s: ", label)
		}
		echoDisabled := exec.Command("stty", "-echo")
		echoDisabled.Stdin = file
		if err := echoDisabled.Run(); err == nil {
			defer func() {
				echoEnabled := exec.Command("stty", "echo")
				echoEnabled.Stdin = file
				_ = echoEnabled.Run()
				fmt.Fprintln(prompter.writer)
			}()
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
	}
	return prompter.promptOptional(label, fallback)
}

func isTerminalFile(file *os.File) bool {
	stat, err := file.Stat()
	return err == nil && (stat.Mode()&os.ModeCharDevice) != 0
}

func workspaceJiraPromptDefaults(workspaceName string) jiraRuntimeConfig {
	envDefaults := jiraRuntimeConfig{
		BaseURL:     os.Getenv("AGENTIC_OPS_JIRA_BASE_URL"),
		Email:       os.Getenv("AGENTIC_OPS_JIRA_EMAIL"),
		APITokenEnv: jiraAPITokenEnvName,
	}
	projectDefaultBaseURL := projectJiraDefaultBaseURL(workspaceName)
	config, err := resolveJiraRuntimeConfig(workspaceName)
	if err != nil || config.Adapter != "real" {
		if envDefaults.BaseURL != "" || envDefaults.Email != "" {
			if envDefaults.BaseURL == "" {
				envDefaults.BaseURL = projectDefaultBaseURL
			}
			return envDefaults
		}
		return jiraRuntimeConfig{BaseURL: projectDefaultBaseURL}
	}
	if config.BaseURL == "" {
		config.BaseURL = firstNonEmpty(envDefaults.BaseURL, projectDefaultBaseURL)
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
	EnvFile    string
	NextAction string
}

var errInvalidJiraTokenEnvName = errors.New("invalid jira token env name")

func prepareWorkspaceJiraConfig(info workspace.Info, jiraUser string, jiraBaseURL string, jiraTokenEnv string, jiraAPIToken string) (workspaceJiraConfigGuide, error) {
	scope := runtimeconfig.NewScope(agenticOpsInstallDir(), info.Root, info.Name)
	configPath := scope.UserConfigPath()
	envFile := scope.UserEnvPath()
	if strings.TrimSpace(jiraTokenEnv) == "" {
		jiraTokenEnv = jiraAPITokenEnvName
	}
	if !validJiraTokenEnvName(jiraTokenEnv) {
		return workspaceJiraConfigGuide{}, fmt.Errorf("%w: use an environment variable name such as AGENTIC_OPS_JIRA_API_TOKEN, then store the token in %s", errInvalidJiraTokenEnvName, envFile)
	}
	jiraBaseURL = jira.NormalizeBaseURL(jiraBaseURL)
	if jiraBaseURL != "" {
		if err := writePersonalProjectJiraConfig(scope, jiraUser, jiraBaseURL, jiraTokenEnv); err != nil {
			return workspaceJiraConfigGuide{}, err
		}
		if err := scope.EnsureUserEnvPlaceholder(jiraTokenEnv, "Create a Jira API token: "+jiraTokenHelpURL); err != nil {
			return workspaceJiraConfigGuide{}, err
		}
		if strings.TrimSpace(jiraAPIToken) != "" {
			if err := scope.WriteUserEnvValue(jiraTokenEnv, jiraAPIToken, "Create a Jira API token: "+jiraTokenHelpURL); err != nil {
				return workspaceJiraConfigGuide{}, err
			}
		}
		status := "needs_jira_api_token"
		nextAction := "set_jira_api_token"
		if jiraTokenConfiguredInFiles(jiraTokenEnv, scope.EnvPaths()) {
			status = "configured"
			nextAction = "agent_init"
		}
		return workspaceJiraConfigGuide{Status: status, Path: configPath, TokenEnv: jiraTokenEnv, EnvFile: envFile, NextAction: nextAction}, nil
	}
	runtimeConfig, err := resolveJiraRuntimeConfig(info.Name)
	if err != nil {
		return workspaceJiraConfigGuide{}, err
	}
	if runtimeConfig.Adapter == "real" {
		status := "configured"
		nextAction := "agent_init"
		if runtimeConfig.APIToken == "" {
			status = "needs_jira_api_token"
			nextAction = "set_jira_api_token"
		}
		return workspaceJiraConfigGuide{Status: status, Path: runtimeConfig.Source, TokenEnv: runtimeConfig.APITokenEnv, EnvFile: runtimeConfig.EnvFile, NextAction: nextAction}, nil
	}
	if defaultBaseURL := projectJiraDefaultBaseURL(info.Name); defaultBaseURL != "" {
		if err := writePersonalProjectJiraConfig(scope, jiraUser, defaultBaseURL, jiraTokenEnv); err != nil {
			return workspaceJiraConfigGuide{}, err
		}
		if err := scope.EnsureUserEnvPlaceholder(jiraTokenEnv, "Create a Jira API token: "+jiraTokenHelpURL); err != nil {
			return workspaceJiraConfigGuide{}, err
		}
		if strings.TrimSpace(jiraAPIToken) != "" {
			if err := scope.WriteUserEnvValue(jiraTokenEnv, jiraAPIToken, "Create a Jira API token: "+jiraTokenHelpURL); err != nil {
				return workspaceJiraConfigGuide{}, err
			}
		}
		status := "needs_jira_api_token"
		nextAction := "set_jira_api_token"
		if jiraTokenConfiguredInFiles(jiraTokenEnv, scope.EnvPaths()) {
			status = "configured"
			nextAction = "agent_init"
		}
		return workspaceJiraConfigGuide{Status: status, Path: configPath, TokenEnv: jiraTokenEnv, EnvFile: envFile, NextAction: nextAction}, nil
	}
	return workspaceJiraConfigGuide{
		Status:     "needs_configuration",
		Path:       configPath,
		TokenEnv:   jiraTokenEnv,
		EnvFile:    envFile,
		NextAction: "rerun_workspace_init_with_--jira-base-url",
	}, nil
}

func validJiraTokenEnvName(value string) bool {
	value = strings.TrimSpace(value)
	if value == "" || len(value) > 80 {
		return false
	}
	for i := 0; i < len(value); i++ {
		ch := value[i]
		if i == 0 {
			if !((ch >= 'A' && ch <= 'Z') || (ch >= 'a' && ch <= 'z') || ch == '_') {
				return false
			}
			continue
		}
		if !((ch >= 'A' && ch <= 'Z') || (ch >= 'a' && ch <= 'z') || (ch >= '0' && ch <= '9') || ch == '_') {
			return false
		}
	}
	return true
}

func writePersonalProjectJiraConfig(scope runtimeconfig.Scope, jiraUser string, jiraBaseURL string, jiraTokenEnv string) error {
	return runtimeconfig.WriteProjectModule(scope.UserConfigPath(), scope.Project, "jira", jiraRuntimeConfig{
		Adapter: "real",
		BaseURL: jira.NormalizeBaseURL(jiraBaseURL),
		Email:   jiraUser,
	})
}

func projectJiraDefaultBaseURL(workspaceName string) string {
	sourcePath, err := repoProjectProfilePath(workspaceName)
	if err != nil {
		return ""
	}
	loadedProfile, err := profile.LoadFile(sourcePath)
	if err != nil {
		return ""
	}
	return strings.TrimSpace(loadedProfile.Jira.BaseURL)
}

type sourceCheckoutResult struct {
	Status  string
	Repo    string
	RepoURL string
	Path    string
}

var runGitClone = func(repoURL string, targetPath string) error {
	cmd := exec.Command("git", "clone", repoURL, targetPath)
	output, err := cmd.CombinedOutput()
	if err != nil {
		return fmt.Errorf("git clone failed: %v: %s", err, strings.TrimSpace(string(output)))
	}
	return nil
}

func ensureSourceCheckout(sourceRoot string, repo string) (sourceCheckoutResult, error) {
	sourceRoot = filepath.Clean(strings.TrimSpace(sourceRoot))
	repo = strings.TrimSpace(repo)
	repoURL := sourceRepoURL(repo)
	result := sourceCheckoutResult{Repo: repo, RepoURL: repoURL, Path: sourceRoot}
	if sourceRoot == "" || sourceRoot == "." {
		return result, errors.New("source_root is required")
	}
	if stat, err := os.Stat(sourceRoot); err == nil {
		if !stat.IsDir() {
			return result, fmt.Errorf("source_root is not a directory: %s", sourceRoot)
		}
		empty, err := directoryEmpty(sourceRoot)
		if err != nil {
			return result, err
		}
		if !empty {
			result.Status = "existing"
			return result, nil
		}
	} else if !errors.Is(err, os.ErrNotExist) {
		return result, err
	}
	if repo == "" {
		return result, errors.New("profile github.repositories.default is required to clone source code")
	}
	if repoURL == "" {
		return result, fmt.Errorf("cannot resolve repository URL from %q", repo)
	}
	if err := os.MkdirAll(filepath.Dir(sourceRoot), 0o755); err != nil {
		return result, err
	}
	if err := runGitClone(repoURL, sourceRoot); err != nil {
		return result, err
	}
	result.Status = "cloned"
	return result, nil
}

func sourceRepoURL(repo string) string {
	repo = strings.TrimSpace(repo)
	if repo == "" {
		return ""
	}
	if strings.Contains(repo, "://") || strings.HasPrefix(repo, "git@") || strings.HasPrefix(repo, "ssh://") {
		return repo
	}
	if strings.Count(repo, "/") == 1 {
		if strings.HasSuffix(repo, ".git") {
			return "git@github.com:" + repo
		}
		return "git@github.com:" + repo + ".git"
	}
	return repo
}

func directoryEmpty(path string) (bool, error) {
	entries, err := os.ReadDir(path)
	if err != nil {
		return false, err
	}
	return len(entries) == 0, nil
}

func materializeWorkspaceProfile(info workspace.Info, jiraUser string, jiraProjectOverride string, sourceRootOverride string) (string, string, string, string, string, error) {
	sourcePath, err := repoProjectProfilePath(info.Name)
	if err != nil {
		return "", "", "", "", "", err
	}
	loadedProfile, err := profile.LoadFile(sourcePath)
	if err != nil {
		return "", "", "", "", "", err
	}
	if loadedProfile.Workspace != info.Name {
		return "", "", "", "", "", fmt.Errorf("profile workspace %q does not match project %q", loadedProfile.Workspace, info.Name)
	}
	if jiraProjectOverride != "" && loadedProfile.Jira.Project != jiraProjectOverride {
		return "", "", "", "", "", fmt.Errorf("profile jira.project %q does not match %q", loadedProfile.Jira.Project, jiraProjectOverride)
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
		return "", "", "", "", "", err
	}
	targetPath := filepath.Join(info.Root, ".agentic-ops", "profile.local.yaml")
	if err := os.WriteFile(targetPath, data, 0o644); err != nil {
		return "", "", "", "", "", err
	}
	effective, err := resolveEffectiveProfile(info.Name, info.Root)
	if err != nil {
		return "", "", "", "", "", err
	}
	if issues := profile.Validate(effective); len(issues) > 0 {
		return "", "", "", "", "", fmt.Errorf("workflow profile validation failed: %s", issues[0].Code)
	}
	registry, err := repoProcessRegistry()
	if err != nil {
		return "", "", "", "", "", err
	}
	if issues := profile.ValidateProcesses(effective, registry); len(issues) > 0 {
		return "", "", "", "", "", fmt.Errorf("workflow profile process validation failed: %s", issues[0].Code)
	}
	return sourcePath, targetPath, effective.Jira.Project, sourceRoot, effective.GitHub.Repositories.Default, nil
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
		"5. If Jira adapter config is missing, ask the development lead to provide runtime local config through process environment variables, `.agentic-ops/config.local.yaml`, or `$AGENTIC_OPS_HOME/user/config.local.yaml`; Jira API token persists only in `$AGENTIC_OPS_HOME/user/.env` as `AGENTIC_OPS_JIRA_API_TOKEN`; never use fake Jira for business work.",
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
		"rule_resolution": map[string]any{
			"order": []string{
				"project_rule",
				"aiagent_rule",
				"company_rule",
				"personal_rule",
			},
			"note": "asset_resolution is for profile/config sources; rule conflicts follow this rule_resolution order",
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
			"conf",
			"workspace_init",
			"list_tasks",
			"inspect_task",
			"task_run",
			"takeover_task",
			"resume_takeover",
			"write_evidence",
			"release_agent",
			"inspect_workspace",
			"branch_align",
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
