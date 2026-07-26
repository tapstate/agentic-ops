package jira

type Issue struct {
	Key            string            `json:"key"`
	Summary        string            `json:"summary"`
	Owner          string            `json:"owner"`
	Assignee       string            `json:"assignee"`
	IssueType      string            `json:"issue_type"`
	Status         string            `json:"status"`
	Labels         []string          `json:"labels,omitempty"`
	Components     []string          `json:"components,omitempty"`
	TargetRepo     string            `json:"target_repo"`
	CurrentAgentID string            `json:"current_agent_id,omitempty"`
	FormValues     map[string]string `json:"form_values,omitempty"`
}
