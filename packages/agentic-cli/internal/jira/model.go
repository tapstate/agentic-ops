package jira

type Issue struct {
	Key                string `json:"key"`
	Summary            string `json:"summary"`
	Owner              string `json:"owner"`
	Assignee           string `json:"assignee"`
	IssueType          string `json:"issue_type"`
	Status             string `json:"status"`
	TargetRepo         string `json:"target_repo"`
	AcceptanceCriteria string `json:"acceptance_criteria"`
	VerificationMethod string `json:"verification_method"`
	RiskLevel          string `json:"risk_level"`
	CurrentAgentID     string `json:"current_agent_id,omitempty"`
}
