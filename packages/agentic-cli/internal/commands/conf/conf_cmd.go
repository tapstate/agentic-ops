package conf

import (
	"github.com/tapstate/agentic-ops/packages/agentic-cli/internal/clihandlers"
	"github.com/tapstate/agentic-ops/packages/agentic-cli/internal/cmdkit"
)

func Register(registry *cmdkit.Registry) {
	registry.MustRegister(cmdkit.CommandSpec{
		Path:    []string{"conf"},
		Summary: "读取 AgenticOps effective 配置",
		Usage:   "agentic-cli conf <key> [--workspace <project>]",
		Examples: []string{
			"agentic-cli conf paths.user_config",
			"agentic-cli conf paths.user_env",
			"agentic-cli conf paths.workspace_config --workspace tapdata",
			"agentic-cli conf paths.workspace_env --workspace tapdata",
			"agentic-cli conf jira.base_url --workspace tapdata",
			"agentic-cli conf jira.email --workspace tapdata",
			"agentic-cli conf jira.api_token_configured --workspace tapdata",
		},
		Risk:      "read-only; secret values are redacted by default",
		HumanGate: false,
		Contract:  "conf_get",
		Run: func(ctx cmdkit.Context, args []string) int {
			return clihandlers.RunConf(args, ctx.Stdout)
		},
	})
}
