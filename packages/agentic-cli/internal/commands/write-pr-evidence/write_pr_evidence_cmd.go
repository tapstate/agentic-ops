package writeprevidence

import (
	"github.com/tapstate/agentic-ops/packages/agentic-cli/internal/clihandlers"
	"github.com/tapstate/agentic-ops/packages/agentic-cli/internal/cmdkit"
)

func Register(registry *cmdkit.Registry) {
	registry.MustRegister(cmdkit.CommandSpec{
		Path: []string{"write-pr-evidence"},
		Run: func(ctx cmdkit.Context, args []string) int {
			return clihandlers.RunWritePREvidence(args, ctx.Stdout)
		},
	})
}
