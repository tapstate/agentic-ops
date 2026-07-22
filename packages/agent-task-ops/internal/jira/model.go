package jira

type Issue struct {
	Key                string `json:"key"`
	Summary            string `json:"summary"`
	Owner              string `json:"owner"`
	TargetRepo         string `json:"target_repo"`
	AcceptanceCriteria string `json:"acceptance_criteria"`
	VerificationMethod string `json:"verification_method"`
}
