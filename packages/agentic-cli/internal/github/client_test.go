package github

import (
	"context"
	"errors"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"testing"
)

func TestReadPullRequestFactsUsesRESTFixtures(t *testing.T) {
	runner := &fixtureRunner{outputs: map[string][]byte{
		"api --method GET repos/tapdata/tapdata/pulls/42":                                     fixture(t, "pr.json"),
		"api --paginate --slurp repos/tapdata/tapdata/commits/abc123/check-runs?per_page=100": fixture(t, "check-runs-passed.json"),
		"api --paginate --slurp repos/tapdata/tapdata/commits/abc123/status?per_page=100":     fixture(t, "status-success.json"),
		"api --paginate --slurp repos/tapdata/tapdata/issues/42/comments?per_page=100":        fixture(t, "comments.json"),
		"api --paginate --slurp repos/tapdata/tapdata/pulls/42/reviews?per_page=100":          fixture(t, "reviews.json"),
	}}

	facts, err := (Client{Runner: runner}).ReadPullRequestFacts(context.Background(), "tapdata/tapdata", "42")
	if err != nil {
		t.Fatalf("ReadPullRequestFacts error = %v", err)
	}
	if facts.URL != "https://github.com/tapdata/tapdata/pull/42" || facts.HeadSHA != "abc123" {
		t.Fatalf("facts = %#v", facts)
	}
	if len(facts.Comments) != 1 || facts.Comments[0].Author != "reviewer-a" {
		t.Fatalf("comments = %#v", facts.Comments)
	}
	if len(facts.Reviews) != 1 || facts.Reviews[0].State != "APPROVED" {
		t.Fatalf("reviews = %#v", facts.Reviews)
	}
	if facts.CI.Status != "passed" || len(facts.CI.Checks) != 2 || facts.ReadAt.IsZero() {
		t.Fatalf("facts CI/read time = %#v", facts)
	}
	wantCommands := []string{
		"api --method GET repos/tapdata/tapdata/pulls/42",
		"api --paginate --slurp repos/tapdata/tapdata/commits/abc123/check-runs?per_page=100",
		"api --paginate --slurp repos/tapdata/tapdata/commits/abc123/status?per_page=100",
		"api --paginate --slurp repos/tapdata/tapdata/issues/42/comments?per_page=100",
		"api --paginate --slurp repos/tapdata/tapdata/pulls/42/reviews?per_page=100",
	}
	if !reflect.DeepEqual(runner.commands, wantCommands) {
		t.Fatalf("commands = %#v, want %#v", runner.commands, wantCommands)
	}
	for _, command := range runner.commands {
		if strings.Contains(command, "pr checks") || strings.Contains(command, "--json conclusion,detailsUrl") {
			t.Fatalf("unstable gh command = %q", command)
		}
	}
}

func TestCheckCIStatusReturnsFourStates(t *testing.T) {
	tests := []struct {
		name       string
		checks     string
		statuses   string
		wantStatus string
		wantFail   int
		wantPend   int
	}{
		{name: "passed", checks: "check-runs-passed.json", statuses: "status-success.json", wantStatus: "passed"},
		{name: "failed", checks: "check-runs-failed.json", statuses: "status-success.json", wantStatus: "failed", wantFail: 4},
		{name: "status error", checks: "check-runs-passed.json", statuses: "status-error", wantStatus: "failed", wantFail: 1},
		{name: "pending", checks: "check-runs-pending.json", statuses: "status-success.json", wantStatus: "pending", wantPend: 2},
		{name: "not configured", checks: "check-runs-empty.json", statuses: "status-empty.json", wantStatus: "not_configured"},
	}

	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			var statusPayload []byte
			if test.statuses == "status-error" {
				statusPayload = []byte(`[{"state":"error","statuses":[{"context":"legacy","state":"error","target_url":"https://github.example/status/1"}]}]`)
			} else {
				statusPayload = fixture(t, test.statuses)
			}
			runner := &fixtureRunner{outputs: map[string][]byte{
				"api --method GET repos/tapdata/tapdata/pulls/42":                                     fixture(t, "pr.json"),
				"api --paginate --slurp repos/tapdata/tapdata/commits/abc123/check-runs?per_page=100": fixture(t, test.checks),
				"api --paginate --slurp repos/tapdata/tapdata/commits/abc123/status?per_page=100":     statusPayload,
			}}
			status, err := (Client{Runner: runner}).CheckCIStatus(context.Background(), "tapdata/tapdata", "42")
			if err != nil {
				t.Fatalf("CheckCIStatus error = %v", err)
			}
			if status.Status != test.wantStatus || len(status.FailingChecks) != test.wantFail || len(status.PendingChecks) != test.wantPend {
				t.Fatalf("status = %#v", status)
			}
		})
	}
}

func TestReadPullRequestFactsReturnsAPIError(t *testing.T) {
	runner := &fixtureRunner{
		outputs: map[string][]byte{"api --method GET repos/tapdata/tapdata/pulls/42": fixture(t, "pr.json")},
		errors:  map[string]error{"api --paginate --slurp repos/tapdata/tapdata/commits/abc123/check-runs?per_page=100": errors.New("API denied")},
	}
	_, err := (Client{Runner: runner}).ReadPullRequestFacts(context.Background(), "tapdata/tapdata", "42")
	if err == nil || !strings.Contains(err.Error(), "API denied") {
		t.Fatalf("error = %v", err)
	}
}

func fixture(t *testing.T, name string) []byte {
	t.Helper()
	data, err := os.ReadFile(filepath.Join("testdata", name))
	if err != nil {
		t.Fatalf("ReadFile(%s) error = %v", name, err)
	}
	return data
}

type fixtureRunner struct {
	commands []string
	outputs  map[string][]byte
	errors   map[string]error
}

func (runner *fixtureRunner) Run(ctx context.Context, args ...string) ([]byte, error) {
	command := strings.Join(args, " ")
	runner.commands = append(runner.commands, command)
	if err := runner.errors[command]; err != nil {
		return nil, err
	}
	output, ok := runner.outputs[command]
	if !ok {
		return nil, errors.New("unexpected command: " + command)
	}
	return output, nil
}
