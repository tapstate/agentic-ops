package updatetaskdescriptionsections

import (
	"github.com/tapstate/agentic-ops/packages/agentic-cli/internal/clihandlers"
	"github.com/tapstate/agentic-ops/packages/agentic-cli/internal/cmdkit"
)

func Register(registry *cmdkit.Registry) {
	registry.MustRegister(cmdkit.CommandSpec{
		Path:      []string{"update-task-description-sections"},
		Summary:   "安全更新 Jira Description 的指定章节",
		Usage:     "agentic-cli update-task-description-sections <issue-key> --workspace <project> --sections-file <path> --confirm-real-jira-write",
		Examples:  []string{"agentic-cli update-task-description-sections TAP-12289 --workspace tapdata --sections-file /tmp/sections.yaml --confirm-real-jira-write"},
		Risk:      "external_write",
		HumanGate: true,
		Contract:  "update_task_description_sections",
		Run: func(ctx cmdkit.Context, args []string) int {
			return clihandlers.RunUpdateTaskDescriptionSections(args, ctx.Stdout)
		},
	})
}
