package updatetaskform

import (
	"github.com/tapstate/agentic-ops/packages/agentic-cli/internal/clihandlers"
	"github.com/tapstate/agentic-ops/packages/agentic-cli/internal/cmdkit"
)

func Register(registry *cmdkit.Registry) {
	registry.MustRegister(cmdkit.CommandSpec{
		Path:      []string{"update-task-form"},
		Summary:   "按项目 profile 的逻辑字段映射更新 Jira 表单",
		Usage:     "agentic-cli update-task-form <issue-key> --workspace <project> --values-file <path> --confirm-real-jira-write",
		Examples:  []string{"agentic-cli update-task-form TAP-12289 --workspace tapdata --values-file /tmp/form-values.yaml --confirm-real-jira-write"},
		Risk:      "external_write",
		HumanGate: true,
		Contract:  "update_task_form",
		Run: func(ctx cmdkit.Context, args []string) int {
			return clihandlers.RunUpdateTaskForm(args, ctx.Stdout)
		},
	})
}
