package branchalign

import (
	"github.com/tapstate/agentic-ops/packages/agentic-cli/internal/clihandlers"
	"github.com/tapstate/agentic-ops/packages/agentic-cli/internal/cmdkit"
)

func Register(registry *cmdkit.Registry) {
	registry.MustRegister(cmdkit.CommandSpec{
		Path:     []string{"tapdata", "branch-align"},
		Summary:  "TapData 多仓分支对齐",
		Usage:    "agentic-cli tapdata branch-align <list|status|plan|apply> [args]",
		Examples: []string{"agentic-cli tapdata branch-align list TAP-12289", "agentic-cli tapdata branch-align status", "agentic-cli tapdata branch-align plan develop", "agentic-cli tapdata branch-align apply develop"},
		Risk:     "apply 会切换本地多仓分支；plan/list/status 只读",
		Contract: "switch_branch",
		Run: func(ctx cmdkit.Context, args []string) int {
			transformed := append([]string{"switch-branch"}, args[2:]...)
			if !hasWorkspaceFlag(transformed) {
				transformed = append(transformed, "--workspace", "tapdata")
			}
			return clihandlers.RunSwitchBranch(transformed, ctx.Stdout)
		},
	})
}

func hasWorkspaceFlag(args []string) bool {
	for index, arg := range args {
		if arg == "--workspace" && index+1 < len(args) {
			return true
		}
		if len(arg) > len("--workspace=") && arg[:len("--workspace=")] == "--workspace=" {
			return true
		}
	}
	return false
}
