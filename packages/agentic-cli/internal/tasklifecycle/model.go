package tasklifecycle

import (
	"context"
	"time"

	"github.com/tapstate/agentic-ops/packages/agentic-cli/internal/feedback"
	"github.com/tapstate/agentic-ops/packages/agentic-cli/internal/jira"
	"github.com/tapstate/agentic-ops/packages/agentic-cli/internal/process"
	"github.com/tapstate/agentic-ops/packages/agentic-cli/internal/profile"
)

const (
	Operation = "task_run"
	TaskType  = "task_takeover"
)

type TaskLifecycleRunner struct {
	Client               jira.Client
	Mode                 string
	Profile              profile.Profile
	ProcessRegistry      map[string]process.Process
	AgentID              string
	Now                  time.Time
	ConfirmRealJiraWrite bool
	AppendEvent          func(feedback.Event) error
	TakeoverFields       func(profile.Profile, string, string) map[string]any
	ReleaseFields        func(profile.Profile) map[string]any
}

type Request struct {
	Workspace string
	IssueKey  string
	Process   string
}

type Result struct {
	OK                    bool
	Code                  string
	Message               string
	RequiredHumanAction   string
	Workspace             string
	IssueKey              string
	RunID                 string
	AgentID               string
	CurrentAgentID        string
	TakeoverAt            string
	TaskType              string
	TaskClass             string
	TaskClassSource       string
	ProcessID             string
	CapabilityID          string
	DefectComplexity      string
	TargetRepo            string
	CurrentStage          string
	NextAction            string
	CurrentAgentIDCleared bool
	AuditTarget           string
	AuditSubmitted        bool
	AuditReference        string
	HumanGate             bool
}

type LifecycleCapability interface {
	ID() string
	Process(context.Context, TaskContext) CapabilityResult
}

type TaskContext struct {
	IssueKey         string
	TaskClass        string
	ProcessID        string
	TargetRepo       string
	Labels           []string
	RequestedProcess string
}

type CapabilityResult struct {
	OK                    bool
	Code                  string
	Message               string
	RequiredHumanAction   string
	CurrentStage          string
	NextAction            string
	DefectComplexity      string
	CurrentAgentIDCleared bool
	AuditTarget           string
	AuditSubmitted        bool
	AuditReference        string
	HumanGate             bool
}
