package workspaceinit

import (
	"github.com/tapstate/agentic-ops/packages/agentic-cli/internal/clihandlers"
	"github.com/tapstate/agentic-ops/packages/agentic-cli/internal/cmdkit"
)

func Register(registry *cmdkit.Registry) {
	registry.MustRegister(cmdkit.CommandSpec{
		Path:     []string{"workspace", "init"},
		Summary:  "初始化项目 AI 工作空间本地 overlay",
		Usage:    "agentic-cli workspace init --project <project> --jira-user <email>",
		Examples: []string{"agentic-cli workspace init --project tapdata --jira-user lead@example.com"},
		Contract: "workspace_init",
		Run: func(ctx cmdkit.Context, args []string) int {
			return clihandlers.RunWorkspaceInit(args, ctx.Stdout)
		},
	})
}
