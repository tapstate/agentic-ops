package feedbackrecordrecovery

import (
	"github.com/tapstate/agentic-ops/packages/agentic-cli/internal/clihandlers"
	"github.com/tapstate/agentic-ops/packages/agentic-cli/internal/cmdkit"
)

func Register(registry *cmdkit.Registry) {
	registry.MustRegister(cmdkit.CommandSpec{
		Path:      []string{"feedback", "record-recovery"},
		Summary:   "记录经人工确认和外部回读的结构化恢复事实",
		Usage:     "agentic-cli feedback record-recovery --workspace <name> --run-id <id> --original-operation <operation> --original-code <code> --evidence-file <path> --external-reference <ref> --readback-verified=true|false --remote-write-completed=true|false --retry-safe=true|false --confirm-recovery-record",
		Risk:      "只写本地反馈事件，不写 Jira、GitHub 或 Git；必须由研发工程师确认恢复和回读事实。",
		HumanGate: true,
		Contract:  "install-resources/basic/contracts/operations/feedback-record-recovery.yaml",
		Run: func(ctx cmdkit.Context, args []string) int {
			return clihandlers.RunFeedbackRecordRecovery(args, ctx.Stdout)
		},
	})
}
