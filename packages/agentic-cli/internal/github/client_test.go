package github

import (
	"context"
	"strings"
	"testing"
)

func TestReadPRCommentsParsesReviewAndIssueComments(t *testing.T) {
	runner := &fakeRunner{output: `{
  "comments": [
    {"author":{"login":"reviewer-a"},"body":"请补测试","url":"https://github.example/comment/1"}
  ],
  "reviews": [
    {"author":{"login":"reviewer-b"},"body":"这里需要处理空值","state":"CHANGES_REQUESTED","url":"https://github.example/review/1"}
  ]
}`}
	client := Client{Runner: runner}

	comments, err := client.ReadPRComments(context.Background(), "tapdata/tapdata", "42")
	if err != nil {
		t.Fatalf("ReadPRComments error = %v", err)
	}
	if len(comments) != 2 {
		t.Fatalf("len(comments) = %d, want 2", len(comments))
	}
	if comments[0].Author != "reviewer-a" || comments[0].Kind != "comment" {
		t.Fatalf("comments[0] = %#v", comments[0])
	}
	if comments[1].Author != "reviewer-b" || comments[1].State != "CHANGES_REQUESTED" || comments[1].Kind != "review" {
		t.Fatalf("comments[1] = %#v", comments[1])
	}
	if !strings.Contains(runner.command, "pr view 42 --repo tapdata/tapdata --json comments,reviews") {
		t.Fatalf("command = %q", runner.command)
	}
}

func TestCheckCIStatusSummarizesFailingChecks(t *testing.T) {
	runner := &fakeRunner{output: `[
  {"name":"unit","state":"COMPLETED","conclusion":"SUCCESS","detailsUrl":"https://github.example/checks/1"},
  {"name":"e2e","state":"COMPLETED","conclusion":"FAILURE","detailsUrl":"https://github.example/checks/2"}
]`}
	client := Client{Runner: runner}

	status, err := client.CheckCIStatus(context.Background(), "tapdata/tapdata", "42")
	if err != nil {
		t.Fatalf("CheckCIStatus error = %v", err)
	}
	if status.Status != "failed" {
		t.Fatalf("Status = %q, want failed", status.Status)
	}
	if len(status.FailingChecks) != 1 || status.FailingChecks[0].Name != "e2e" {
		t.Fatalf("FailingChecks = %#v", status.FailingChecks)
	}
	if !strings.Contains(runner.command, "pr checks 42 --repo tapdata/tapdata --json name,state,conclusion,detailsUrl") {
		t.Fatalf("command = %q", runner.command)
	}
}

type fakeRunner struct {
	command string
	output  string
}

func (f *fakeRunner) Run(ctx context.Context, args ...string) ([]byte, error) {
	f.command = strings.Join(args, " ")
	return []byte(f.output), nil
}
