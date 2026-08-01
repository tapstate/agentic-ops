package feedbackpropose

import (
	"github.com/tapstate/agentic-ops/packages/agentic-cli/internal/clihandlers"
	"github.com/tapstate/agentic-ops/packages/agentic-cli/internal/cmdkit"
)

func Register(registry *cmdkit.Registry) {
	registry.MustRegister(cmdkit.CommandSpec{
		Path:    []string{"feedback", "propose"},
		Summary: "根据反馈证据生成结构化改进建议",
		Usage:   "agentic-cli feedback propose --workspace <name> [--date <yyyy-mm-dd>] [filters]",
		Run: func(ctx cmdkit.Context, args []string) int {
			return clihandlers.RunFeedbackPropose(args, ctx.Stdout)
		},
	})
}
