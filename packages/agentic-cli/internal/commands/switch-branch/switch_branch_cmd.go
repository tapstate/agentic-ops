package switchbranch

import (
	"github.com/tapstate/agentic-ops/packages/agentic-cli/internal/clihandlers"
	"github.com/tapstate/agentic-ops/packages/agentic-cli/internal/cmdkit"
)

func Register(registry *cmdkit.Registry) {
	registry.MustRegister(cmdkit.CommandSpec{
		Path:     []string{"switch-branch"},
		Summary:  "legacy Tapdata 分支对齐入口；请迁移到 agentic-cli tapdata branch-align",
		Usage:    "agentic-cli switch-branch <list|status|plan|apply> [args]",
		Examples: []string{"agentic-cli tapdata branch-align plan develop", "agentic-cli tapdata branch-align apply develop"},
		Risk:     "legacy alias；apply 会切换本地多仓分支",
		Contract: "switch_branch",
		Run: func(ctx cmdkit.Context, args []string) int {
			return clihandlers.RunSwitchBranch(args, ctx.Stdout)
		},
	})
}
