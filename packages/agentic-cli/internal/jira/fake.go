package jira

import "context"

type FakeClient struct{}

func (FakeClient) CurrentUser(ctx context.Context) (string, error) {
	return "current-user", nil
}

func (client FakeClient) SearchIssues(ctx context.Context, workspace string, jql string) ([]Issue, error) {
	return client.ListTasks(workspace), nil
}

func (FakeClient) ListTasks(workspace string) []Issue {
	return []Issue{
		{
			Key:                "TAP-123",
			Summary:            "修复示例任务",
			Owner:              "current-user",
			Assignee:           "current-user",
			IssueType:          "Task",
			Status:             "To Do",
			TargetRepo:         workspace + "/example-repo",
			ProblemBranch:      "develop",
			TargetBranch:       "develop",
			ProblemSummary:     "修复示例任务",
			AcceptanceCriteria: "单元测试通过",
			VerificationMethod: "go test ./...",
			RiskLevel:          "low",
		},
		{
			Key:                "TAP-BUG-123",
			Summary:            "修复缺陷示例任务",
			Owner:              "current-user",
			Assignee:           "current-user",
			IssueType:          "Bug",
			Status:             "To Do",
			Labels:             []string{"defect"},
			TargetRepo:         workspace + "/example-repo",
			ProblemBranch:      "develop",
			TargetBranch:       "develop",
			ProblemSummary:     "修复缺陷示例任务",
			AcceptanceCriteria: "缺陷已修复并验证",
			VerificationMethod: "go test ./...",
			RiskLevel:          "medium",
		},
	}
}

func (FakeClient) GetIssue(workspace string, key string) (Issue, bool) {
	for _, issue := range fakeIssues(workspace) {
		if issue.Key == key {
			return issue, true
		}
	}
	return Issue{}, false
}

func (client FakeClient) GetIssueByKey(ctx context.Context, workspace string, key string) (Issue, bool, error) {
	issue, ok := client.GetIssue(workspace, key)
	return issue, ok, nil
}

func (FakeClient) AddComment(ctx context.Context, key string, body string) error {
	return nil
}

func (FakeClient) UpdateFields(ctx context.Context, key string, fields map[string]any) error {
	return nil
}

func (FakeClient) Transitions(ctx context.Context, key string) ([]Transition, error) {
	return []Transition{{ID: "31", Name: "Done"}}, nil
}

func (FakeClient) TransitionIssue(ctx context.Context, key string, transitionID string) error {
	return nil
}

func fakeIssues(workspace string) []Issue {
	listed := (FakeClient{}).ListTasks(workspace)
	valid := listed[0]
	missingRepo := valid
	missingRepo.Key = "TAP-MISSING-REPO"
	missingRepo.TargetRepo = ""
	missingRepo.Components = []string{"api"}
	otherOwner := valid
	otherOwner.Key = "TAP-OTHER-OWNER"
	otherOwner.Owner = "other-user"
	agentConflict := valid
	agentConflict.Key = "TAP-AGENT-CONFLICT"
	agentConflict.CurrentAgentID = "other-agent"
	unknownStatus := valid
	unknownStatus.Key = "TAP-UNKNOWN-STATUS"
	unknownStatus.Status = "Custom Review"
	inProgress := valid
	inProgress.Key = "TAP-IN-PROGRESS"
	inProgress.Status = "In Progress"
	return append(listed, missingRepo, otherOwner, agentConflict, unknownStatus, inProgress)
}
