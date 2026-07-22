package jira

type FakeClient struct{}

func (FakeClient) ListTasks(workspace string) []Issue {
	return []Issue{
		{
			Key:                "TAP-123",
			Summary:            "修复示例任务",
			Owner:              "current-user",
			TargetRepo:         workspace + "/example-repo",
			AcceptanceCriteria: "单元测试通过",
			VerificationMethod: "go test ./...",
		},
	}
}

func (FakeClient) GetIssue(workspace string, key string) (Issue, bool) {
	for _, issue := range (FakeClient{}).ListTasks(workspace) {
		if issue.Key == key {
			return issue, true
		}
	}
	return Issue{}, false
}
