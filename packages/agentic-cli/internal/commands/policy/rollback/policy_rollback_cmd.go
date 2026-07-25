package policyrollback

import (
	"github.com/tapstate/agentic-ops/packages/agentic-cli/internal/clihandlers"
	"github.com/tapstate/agentic-ops/packages/agentic-cli/internal/cmdkit"
)

func Register(registry *cmdkit.Registry) {
	registry.MustRegister(cmdkit.CommandSpec{
		Path: []string{"policy", "rollback"},
		Run: func(ctx cmdkit.Context, args []string) int {
			return clihandlers.RunPolicyRollback(args, ctx.Stdout)
		},
	})
}
