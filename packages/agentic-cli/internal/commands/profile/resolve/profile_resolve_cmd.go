package profileresolve

import (
	"github.com/tapstate/agentic-ops/packages/agentic-cli/internal/clihandlers"
	"github.com/tapstate/agentic-ops/packages/agentic-cli/internal/cmdkit"
)

func Register(registry *cmdkit.Registry) {
	registry.MustRegister(cmdkit.CommandSpec{
		Path:     []string{"profile", "resolve"},
		Summary:  "解析公司、项目、个人与工作空间 overlay 后的 effective profile",
		Usage:    "agentic-cli profile resolve --project <project>",
		Examples: []string{"agentic-cli profile resolve --project tapdata"},
		Contract: "profile_resolve",
		Run: func(ctx cmdkit.Context, args []string) int {
			return clihandlers.RunProfileResolve(args, ctx.Stdout)
		},
	})
}
