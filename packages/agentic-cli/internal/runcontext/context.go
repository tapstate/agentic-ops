package runcontext

import (
	"errors"

	"github.com/tapstate/agentic-ops/packages/agentic-cli/internal/feedback"
)

type Query struct {
	RunID     string
	Workspace string
	AgentID   string
}

type Context struct {
	Workspace        string
	RunID            string
	IssueKey         string
	AgentID          string
	CurrentAgentID   string
	TaskClass        string
	ProcessID        string
	TargetRepo       string
	CurrentStage     string
	NextAction       string
	Terminal         bool
	HumanGatePending bool
}

var ErrRunNotFound = errors.New("run_not_found")

var ErrWorkspaceMismatch = errors.New("workspace_mismatch")

var ErrLocalStateMismatch = errors.New("local_state_mismatch")

func Read(events []feedback.Event, query Query) (Context, error) {
	var context Context
	anchorIndex := -1
	for index, event := range events {
		if event.RunID != query.RunID || event.Operation != "takeover_task" || !event.OK {
			continue
		}
		if event.Workspace != query.Workspace {
			return Context{}, ErrWorkspaceMismatch
		}
		if event.IssueKey == "" ||
			event.AgentID == "" ||
			event.CurrentAgentID == "" ||
			event.AgentID != query.AgentID ||
			event.CurrentAgentID != event.AgentID ||
			event.TaskClass == "" ||
			event.ProcessID == "" ||
			event.CurrentStage == "" ||
			event.NextAction == "" {
			return Context{}, ErrLocalStateMismatch
		}
		context = Context{
			Workspace:      event.Workspace,
			RunID:          event.RunID,
			IssueKey:       event.IssueKey,
			AgentID:        event.AgentID,
			CurrentAgentID: event.CurrentAgentID,
			TaskClass:      event.TaskClass,
			ProcessID:      event.ProcessID,
			TargetRepo:     event.TargetRepo,
			CurrentStage:   event.CurrentStage,
			NextAction:     event.NextAction,
		}
		anchorIndex = index
		break
	}
	if anchorIndex < 0 {
		return Context{}, ErrRunNotFound
	}
	for _, event := range events[anchorIndex+1:] {
		if event.RunID != query.RunID {
			continue
		}
		if immutableConflict(event.Workspace, context.Workspace) ||
			immutableConflict(event.IssueKey, context.IssueKey) ||
			immutableConflict(event.AgentID, context.AgentID) ||
			immutableConflict(event.CurrentAgentID, context.CurrentAgentID) ||
			immutableConflict(event.TaskClass, context.TaskClass) ||
			immutableConflict(event.ProcessID, context.ProcessID) {
			return Context{}, ErrLocalStateMismatch
		}
		if event.TargetRepo != "" {
			if context.TargetRepo == "" {
				context.TargetRepo = event.TargetRepo
			} else if event.TargetRepo != context.TargetRepo {
				return Context{}, ErrLocalStateMismatch
			}
		}
		if !stateBearingOperation(event.Operation) {
			continue
		}
		if event.Operation == "resume_takeover" && (!event.OK || event.CurrentStage == "takeover_resumed") {
			continue
		}
		if event.CurrentStage == "" || event.NextAction == "" {
			return Context{}, ErrLocalStateMismatch
		}
		context.CurrentStage = event.CurrentStage
		context.NextAction = event.NextAction
		context.Terminal = event.CurrentStage == "completed" || event.NextAction == "task_audit_submitted"
		context.HumanGatePending = event.RequiresHumanAction
	}
	return context, nil
}

func ReadFile(path string, query Query) (Context, error) {
	events, err := feedback.ReadEvents(path)
	if err != nil {
		return Context{}, err
	}
	return Read(events, query)
}

func ErrorCode(err error) string {
	switch {
	case errors.Is(err, ErrRunNotFound):
		return "run_not_found"
	case errors.Is(err, ErrWorkspaceMismatch):
		return "workspace_mismatch"
	case errors.Is(err, ErrLocalStateMismatch):
		return "local_state_mismatch"
	default:
		return "event_read_failed"
	}
}

func immutableConflict(value string, expected string) bool {
	return value != "" && value != expected
}

func stateBearingOperation(operation string) bool {
	switch operation {
	case "takeover_task", "resume_takeover", "write_evidence", "prepare_pr", "release_agent":
		return true
	default:
		return false
	}
}
