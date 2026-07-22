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
			AcceptanceCriteria: "单元测试通过",
			VerificationMethod: "go test ./...",
			RiskLevel:          "low",
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

func (FakeClient) TransitionIssue(ctx context.Context, key string, transitionID string) error {
	return nil
}

func fakeIssues(workspace string) []Issue {
	valid := (FakeClient{}).ListTasks(workspace)[0]
	missingRepo := valid
	missingRepo.Key = "TAP-MISSING-REPO"
	missingRepo.TargetRepo = ""
	otherOwner := valid
	otherOwner.Key = "TAP-OTHER-OWNER"
	otherOwner.Owner = "other-user"
	agentConflict := valid
	agentConflict.Key = "TAP-AGENT-CONFLICT"
	agentConflict.CurrentAgentID = "other-agent"
	unknownStatus := valid
	unknownStatus.Key = "TAP-UNKNOWN-STATUS"
	unknownStatus.Status = "Custom Review"
	return []Issue{valid, missingRepo, otherOwner, agentConflict, unknownStatus}
}
