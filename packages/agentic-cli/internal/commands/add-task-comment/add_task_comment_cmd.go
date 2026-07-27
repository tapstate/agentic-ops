package addtaskcomment

import (
	"github.com/tapstate/agentic-ops/packages/agentic-cli/internal/clihandlers"
	"github.com/tapstate/agentic-ops/packages/agentic-cli/internal/cmdkit"
)

func Register(registry *cmdkit.Registry) {
	registry.MustRegister(cmdkit.CommandSpec{
		Path:      []string{"add-task-comment"},
		Summary:   "向 Jira 任务追加分类评论",
		Usage:     "agentic-cli add-task-comment <issue-key> --workspace <project> --category <analysis|plan|decision|evidence|blocked> --content-file <path> [--run-id <id>] --confirm-real-jira-write",
		Examples:  []string{"agentic-cli add-task-comment TAP-12289 --workspace tapdata --category plan --content-file /tmp/fix-plan.md --confirm-real-jira-write"},
		Risk:      "external_write",
		HumanGate: true,
		Contract:  "add_task_comment",
		Run: func(ctx cmdkit.Context, args []string) int {
			return clihandlers.RunAddTaskComment(args, ctx.Stdout)
		},
	})
}
