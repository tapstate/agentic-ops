package feedbackanalyze

import (
	"github.com/tapstate/agentic-ops/packages/agentic-cli/internal/clihandlers"
	"github.com/tapstate/agentic-ops/packages/agentic-cli/internal/cmdkit"
)

func Register(registry *cmdkit.Registry) {
	registry.MustRegister(cmdkit.CommandSpec{
		Path:    []string{"feedback", "analyze"},
		Summary: "分析反馈事件中的重复失败和人工门禁热点",
		Usage:   "agentic-cli feedback analyze --workspace <name> [--date <yyyy-mm-dd>] [filters]",
		Run: func(ctx cmdkit.Context, args []string) int {
			return clihandlers.RunFeedbackAnalyze(args, ctx.Stdout)
		},
	})
}
