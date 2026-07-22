package jira

import "testing"

func TestFakeClientListsTaskWithRequiredFields(t *testing.T) {
	issues := FakeClient{}.ListTasks("tapstate")
	if len(issues) != 1 {
		t.Fatalf("len = %d, want 1", len(issues))
	}
	got := issues[0]
	if got.Key != "TAP-123" {
		t.Fatalf("Key = %s", got.Key)
	}
	if got.TargetRepo == "" || got.AcceptanceCriteria == "" || got.VerificationMethod == "" {
		t.Fatalf("issue missing required fields: %+v", got)
	}
}

func TestFakeClientGetsIssue(t *testing.T) {
	issue, ok := FakeClient{}.GetIssue("TAP-123")
	if !ok {
		t.Fatal("expected TAP-123")
	}
	if issue.Key != "TAP-123" {
		t.Fatalf("Key = %s", issue.Key)
	}
}
