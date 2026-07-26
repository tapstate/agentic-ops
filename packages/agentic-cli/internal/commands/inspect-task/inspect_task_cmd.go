package inspecttask

import (
	"github.com/tapstate/agentic-ops/packages/agentic-cli/internal/clihandlers"
	"github.com/tapstate/agentic-ops/packages/agentic-cli/internal/cmdkit"
)

func Register(registry *cmdkit.Registry) {
	registry.MustRegister(cmdkit.CommandSpec{
		Path:     []string{"inspect-task"},
		Summary:  "只读检查 Jira 任务事实和项目资产引用",
		Usage:    "agentic-cli inspect-task <issue-key> --workspace <project>",
		Examples: []string{"agentic-cli inspect-task TAP-12289 --workspace tapdata"},
		Risk:     "read_only",
		Contract: "inspect_task",
		Run: func(ctx cmdkit.Context, args []string) int {
			return clihandlers.RunInspectTask(args, ctx.Stdout)
		},
	})
}
