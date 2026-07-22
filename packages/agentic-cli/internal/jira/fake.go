package jira

type FakeClient struct{}

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
