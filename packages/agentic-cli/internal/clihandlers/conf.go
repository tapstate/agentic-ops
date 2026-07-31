package clihandlers

import (
	"io"
	"strings"

	"github.com/tapstate/agentic-ops/packages/agentic-cli/internal/output"
	"github.com/tapstate/agentic-ops/packages/agentic-cli/internal/runtimeconfig"
)

func RunConf(args []string, stdout io.Writer) int {
	key := positionalArg(args, "conf")
	if strings.TrimSpace(key) == "" {
		return writeJSON(stdout, output.Failure("conf_get", "missing_config_key", "缺少配置 key", "请提供配置 key，例如 agentic-cli conf jira.base_url --workspace tapdata"))
	}
	workspaceName := workspaceNameFromArgsOrAgentConfig(args, "default")
	scope := runtimeConfigScope(workspaceName)
	payload := map[string]any{
		"key":                 key,
		"workspace":           workspaceName,
		"agentic_next_action": "continue",
	}
	switch key {
	case "paths.user_config":
		payload["value"] = scope.UserConfigPath()
		return writeJSON(stdout, output.Success("conf_get", payload))
	case "paths.user_env":
		payload["value"] = scope.UserEnvPath()
		return writeJSON(stdout, output.Success("conf_get", payload))
	case "paths.workspace_config":
		payload["value"] = scope.WorkspaceConfigPath()
		return writeJSON(stdout, output.Success("conf_get", payload))
	case "paths.workspace_env":
		payload["value"] = scope.WorkspaceEnvPath()
		return writeJSON(stdout, output.Success("conf_get", payload))
	case "jira.api_token":
		return writeSecretConfigRedacted(stdout, key, workspaceName)
	}

	_, field, found := runtimeConfigurationRegistry().FindField(key)
	if !found {
		result := output.Failure("conf_get", "conf_key_not_found", "未找到配置 key: "+key, "请检查 key 名称，或运行 agentic-cli conf -h 查看可用配置")
		result["key"] = key
		result["workspace"] = workspaceName
		return writeJSON(stdout, result)
	}
	if field.Secret {
		return writeSecretConfigRedacted(stdout, key, workspaceName)
	}
	runtimeConfig, err := resolveJiraRuntimeConfig(workspaceName)
	if err != nil {
		return writeJSON(stdout, output.Failure("conf_get", "conf_resolve_failed", err.Error(), "请检查本地配置文件"))
	}
	payload["source"] = runtimeConfig.Source
	switch key {
	case "jira.adapter":
		payload["value"] = runtimeConfig.Adapter
	case "jira.base_url":
		payload["value"] = runtimeConfig.BaseURL
	case "jira.email":
		payload["value"] = runtimeConfig.Email
	case "jira.api_token_configured":
		payload["value"] = strings.TrimSpace(runtimeConfig.APIToken) != ""
	}
	if strings.TrimSpace(runtimeConfig.Source) == "" || runtimeConfig.Source == "default" {
		return writeJSON(stdout, output.Failure("conf_get", "conf_key_not_configured", "配置 key 尚未配置: "+key, "请先运行 workspace init 或维护本地 config.local.yaml"))
	}
	return writeJSON(stdout, output.Success("conf_get", payload))
}

func writeSecretConfigRedacted(stdout io.Writer, key string, workspaceName string) int {
	result := output.Failure("conf_get", "conf_secret_redacted", "secret 配置默认不输出原值", "请通过受控运行时环境或后续显式门禁读取 secret")
	result["key"] = key
	result["workspace"] = workspaceName
	result["secret"] = true
	return writeJSON(stdout, result)
}

func runtimeConfigurationRegistry() *runtimeconfig.Registry {
	registry := runtimeconfig.NewRegistry()
	registry.Register(jiraRuntimeModuleSpec())
	return registry
}

func jiraRuntimeModuleSpec() runtimeconfig.ModuleSpec {
	return runtimeconfig.ModuleSpec{
		Name: "jira",
		Fields: []runtimeconfig.FieldSpec{
			{
				Key:      "adapter",
				Default:  "real",
				Prompt:   "Jira adapter",
				Target:   "config",
				Required: true,
			},
			{
				Key:      "base_url",
				Default:  "https://tapdata.atlassian.net",
				Prompt:   "Jira base URL",
				Target:   "config",
				Required: true,
			},
			{
				Key:      "email",
				Prompt:   "Jira user email",
				Target:   "config",
				Required: true,
			},
			{
				Key:      "api_token",
				EnvName:  "AGENTIC_OPS_JIRA_API_TOKEN",
				Prompt:   "Jira API token",
				Target:   "env",
				Secret:   true,
				Required: true,
			},
			{
				Key:    "api_token_configured",
				Target: "derived",
			},
		},
	}
}
