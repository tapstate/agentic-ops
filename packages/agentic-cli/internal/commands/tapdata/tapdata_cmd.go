package tapdata

import (
	"fmt"

	"github.com/tapstate/agentic-ops/packages/agentic-cli/internal/cmdkit"
)

func Register(registry *cmdkit.Registry) {
	registry.MustRegister(cmdkit.CommandSpec{
		Path:     []string{"tapdata"},
		Summary:  "Tapdata 项目工具：branch-align TapData 多仓分支对齐",
		Usage:    "agentic-cli tapdata <tool>",
		Examples: []string{"agentic-cli tapdata branch-align plan develop"},
		Run: func(ctx cmdkit.Context, args []string) int {
			fmt.Fprintln(ctx.Stdout, "Usage: agentic-cli tapdata <tool>")
			fmt.Fprintln(ctx.Stdout)
			fmt.Fprintln(ctx.Stdout, "Tools:")
			fmt.Fprintln(ctx.Stdout, "  branch-align  TapData 多仓分支对齐")
			return 0
		},
	})
}
