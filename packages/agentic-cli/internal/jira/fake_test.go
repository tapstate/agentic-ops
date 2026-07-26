package jira

import "testing"

func TestFakeClientListsTaskWithRequiredFields(t *testing.T) {
	issues := FakeClient{}.ListTasks("tapstate")
	if len(issues) != 2 {
		t.Fatalf("len = %d, want 2", len(issues))
	}
	byKey := map[string]Issue{}
	for _, issue := range issues {
		byKey[issue.Key] = issue
	}
	for _, key := range []string{"TAP-123", "TAP-BUG-123"} {
		got := byKey[key]
		if got.Key == "" {
			t.Fatalf("%s missing from fake list: %+v", key, issues)
		}
		if got.TargetRepo == "" || got.FormValues["acceptance_criteria"] == "" || got.FormValues["verification_method"] == "" {
			t.Fatalf("issue missing required fields: %+v", got)
		}
	}
	if byKey["TAP-BUG-123"].IssueType != "Bug" {
		t.Fatalf("TAP-BUG-123 IssueType = %s", byKey["TAP-BUG-123"].IssueType)
	}
}

func TestFakeClientGetsIssue(t *testing.T) {
	issue, ok := FakeClient{}.GetIssue("CYNTEX", "TAP-123")
	if !ok {
		t.Fatal("expected TAP-123")
	}
	if issue.Key != "TAP-123" {
		t.Fatalf("Key = %s", issue.Key)
	}
	if issue.TargetRepo != "CYNTEX/example-repo" {
		t.Fatalf("TargetRepo = %s", issue.TargetRepo)
	}
}
