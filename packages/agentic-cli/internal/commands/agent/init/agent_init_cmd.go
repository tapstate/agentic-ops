package agentinit

import (
	"github.com/tapstate/agentic-ops/packages/agentic-cli/internal/clihandlers"
	"github.com/tapstate/agentic-ops/packages/agentic-cli/internal/cmdkit"
)

func Register(registry *cmdkit.Registry) {
	registry.MustRegister(cmdkit.CommandSpec{
		Path:     []string{"agent", "init"},
		Summary:  "输出 AIAgent 初始化入口、资产解析顺序和能力清单",
		Usage:    "agentic-cli agent init",
		Examples: []string{"agentic-cli agent init"},
		Contract: "agent_init",
		Run: func(ctx cmdkit.Context, args []string) int {
			return clihandlers.RunAgentInit(args, ctx.Stdout)
		},
	})
}
