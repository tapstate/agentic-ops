package profilerollback

import (
	"github.com/tapstate/agentic-ops/packages/agentic-cli/internal/clihandlers"
	"github.com/tapstate/agentic-ops/packages/agentic-cli/internal/cmdkit"
)

func Register(registry *cmdkit.Registry) {
	registry.MustRegister(cmdkit.CommandSpec{
		Path: []string{"profile", "rollback"},
		Run: func(ctx cmdkit.Context, args []string) int {
			return clihandlers.RunProfileRollback(args, ctx.Stdout)
		},
	})
}
