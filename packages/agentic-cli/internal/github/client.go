package github

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"os/exec"
	"strings"
	"time"
)

const (
	PRReadFailedCode = "github_pr_read_failed"
	CIReadFailedCode = "github_ci_read_failed"
)

type ReadError struct {
	Code string
	Err  error
}

func (err *ReadError) Error() string {
	return err.Err.Error()
}

func (err *ReadError) Unwrap() error {
	return err.Err
}

func ReadErrorCode(err error) string {
	var readErr *ReadError
	if errors.As(err, &readErr) {
		return readErr.Code
	}
	return PRReadFailedCode
}

type Runner interface {
	Run(ctx context.Context, args ...string) ([]byte, error)
}

type ExecRunner struct {
	Binary string
}

func (runner ExecRunner) Run(ctx context.Context, args ...string) ([]byte, error) {
	binary := runner.Binary
	if binary == "" {
		binary = "gh"
	}
	cmd := exec.CommandContext(ctx, binary, args...)
	output, err := cmd.CombinedOutput()
	if err != nil {
		return nil, fmt.Errorf("gh %s failed: %w: %s", strings.Join(args, " "), err, strings.TrimSpace(string(output)))
	}
	return output, nil
}

type Client struct {
	Runner Runner
}

type PullRequestFacts struct {
	URL      string      `json:"url"`
	HeadSHA  string      `json:"head_sha"`
	Comments []PRComment `json:"comments"`
	Reviews  []PRReview  `json:"reviews"`
	CI       CIStatus    `json:"ci"`
	ReadAt   time.Time   `json:"read_at"`
}

type PRComment struct {
	Kind   string `json:"kind,omitempty"`
	Author string `json:"author"`
	Body   string `json:"body"`
	State  string `json:"state,omitempty"`
	URL    string `json:"url,omitempty"`
}

type PRReview struct {
	Author string `json:"author"`
	Body   string `json:"body"`
	State  string `json:"state"`
	URL    string `json:"url,omitempty"`
}

type Check struct {
	Name       string `json:"name"`
	State      string `json:"state"`
	Conclusion string `json:"conclusion"`
	DetailsURL string `json:"details_url,omitempty"`
}

type CIStatus struct {
	Status        string  `json:"status"`
	Checks        []Check `json:"checks"`
	FailingChecks []Check `json:"failing_checks"`
	PendingChecks []Check `json:"pending_checks"`
}

func (client Client) ReadPullRequestFacts(ctx context.Context, repo string, pr string) (PullRequestFacts, error) {
	url, headSHA, err := client.readPullRequest(ctx, repo, pr)
	if err != nil {
		return PullRequestFacts{}, &ReadError{Code: PRReadFailedCode, Err: err}
	}
	ci, err := client.readCIStatus(ctx, repo, headSHA)
	if err != nil {
		return PullRequestFacts{}, &ReadError{Code: CIReadFailedCode, Err: err}
	}
	comments, err := client.readComments(ctx, repo, pr)
	if err != nil {
		return PullRequestFacts{}, &ReadError{Code: PRReadFailedCode, Err: err}
	}
	reviews, err := client.readReviews(ctx, repo, pr)
	if err != nil {
		return PullRequestFacts{}, &ReadError{Code: PRReadFailedCode, Err: err}
	}
	return PullRequestFacts{
		URL:      url,
		HeadSHA:  headSHA,
		Comments: comments,
		Reviews:  reviews,
		CI:       ci,
		ReadAt:   time.Now().UTC(),
	}, nil
}

func (client Client) ReadPRComments(ctx context.Context, repo string, pr string) ([]PRComment, error) {
	comments, err := client.readComments(ctx, repo, pr)
	if err != nil {
		return nil, err
	}
	reviews, err := client.readReviews(ctx, repo, pr)
	if err != nil {
		return nil, err
	}
	result := make([]PRComment, 0, len(comments)+len(reviews))
	result = append(result, comments...)
	for _, review := range reviews {
		result = append(result, PRComment{
			Kind:   "review",
			Author: review.Author,
			Body:   review.Body,
			State:  review.State,
			URL:    review.URL,
		})
	}
	return result, nil
}

func (client Client) CheckCIStatus(ctx context.Context, repo string, pr string) (CIStatus, error) {
	_, headSHA, err := client.readPullRequest(ctx, repo, pr)
	if err != nil {
		return CIStatus{}, err
	}
	return client.readCIStatus(ctx, repo, headSHA)
}

func (client Client) readPullRequest(ctx context.Context, repo string, pr string) (string, string, error) {
	output, err := client.runner().Run(ctx, "api", "--method", "GET", "repos/"+repo+"/pulls/"+pr)
	if err != nil {
		return "", "", err
	}
	var payload struct {
		URL  string `json:"html_url"`
		Head struct {
			SHA string `json:"sha"`
		} `json:"head"`
	}
	if err := json.Unmarshal(output, &payload); err != nil {
		return "", "", err
	}
	if strings.TrimSpace(payload.URL) == "" || strings.TrimSpace(payload.Head.SHA) == "" {
		return "", "", fmt.Errorf("GitHub PR facts missing URL or head SHA")
	}
	return payload.URL, payload.Head.SHA, nil
}

func (client Client) readCIStatus(ctx context.Context, repo string, headSHA string) (CIStatus, error) {
	checkOutput, err := client.runner().Run(ctx, "api", "--paginate", "--slurp", "repos/"+repo+"/commits/"+headSHA+"/check-runs?per_page=100")
	if err != nil {
		return CIStatus{}, err
	}
	statusOutput, err := client.runner().Run(ctx, "api", "--paginate", "--slurp", "repos/"+repo+"/commits/"+headSHA+"/status?per_page=100")
	if err != nil {
		return CIStatus{}, err
	}
	checks, err := decodeChecks(checkOutput, statusOutput)
	if err != nil {
		return CIStatus{}, err
	}
	return summarizeCI(checks), nil
}

func decodeChecks(checkOutput []byte, statusOutput []byte) ([]Check, error) {
	var checkPages []struct {
		CheckRuns []struct {
			Name       string `json:"name"`
			Status     string `json:"status"`
			Conclusion string `json:"conclusion"`
			DetailsURL string `json:"details_url"`
		} `json:"check_runs"`
	}
	if err := json.Unmarshal(checkOutput, &checkPages); err != nil {
		return nil, err
	}
	checks := make([]Check, 0)
	for _, page := range checkPages {
		for _, raw := range page.CheckRuns {
			checks = append(checks, Check{Name: raw.Name, State: raw.Status, Conclusion: raw.Conclusion, DetailsURL: raw.DetailsURL})
		}
	}

	var statusPages []struct {
		Statuses []struct {
			Context   string `json:"context"`
			State     string `json:"state"`
			TargetURL string `json:"target_url"`
		} `json:"statuses"`
	}
	if err := json.Unmarshal(statusOutput, &statusPages); err != nil {
		return nil, err
	}
	for _, page := range statusPages {
		for _, raw := range page.Statuses {
			checks = append(checks, Check{Name: raw.Context, State: raw.State, Conclusion: raw.State, DetailsURL: raw.TargetURL})
		}
	}
	return checks, nil
}

func (client Client) readComments(ctx context.Context, repo string, pr string) ([]PRComment, error) {
	output, err := client.runner().Run(ctx, "api", "--paginate", "--slurp", "repos/"+repo+"/issues/"+pr+"/comments?per_page=100")
	if err != nil {
		return nil, err
	}
	var pages [][]struct {
		User struct {
			Login string `json:"login"`
		} `json:"user"`
		Body string `json:"body"`
		URL  string `json:"html_url"`
	}
	if err := json.Unmarshal(output, &pages); err != nil {
		return nil, err
	}
	comments := make([]PRComment, 0)
	for _, page := range pages {
		for _, raw := range page {
			comments = append(comments, PRComment{Kind: "comment", Author: raw.User.Login, Body: raw.Body, URL: raw.URL})
		}
	}
	return comments, nil
}

func (client Client) readReviews(ctx context.Context, repo string, pr string) ([]PRReview, error) {
	output, err := client.runner().Run(ctx, "api", "--paginate", "--slurp", "repos/"+repo+"/pulls/"+pr+"/reviews?per_page=100")
	if err != nil {
		return nil, err
	}
	var pages [][]struct {
		User struct {
			Login string `json:"login"`
		} `json:"user"`
		Body  string `json:"body"`
		State string `json:"state"`
		URL   string `json:"html_url"`
	}
	if err := json.Unmarshal(output, &pages); err != nil {
		return nil, err
	}
	reviews := make([]PRReview, 0)
	for _, page := range pages {
		for _, raw := range page {
			reviews = append(reviews, PRReview{Author: raw.User.Login, Body: raw.Body, State: raw.State, URL: raw.URL})
		}
	}
	return reviews, nil
}

func summarizeCI(checks []Check) CIStatus {
	status := CIStatus{Status: "not_configured", Checks: checks}
	if len(checks) == 0 {
		return status
	}
	for _, check := range checks {
		if isFailingCheck(check) {
			status.FailingChecks = append(status.FailingChecks, check)
			continue
		}
		if isPendingCheck(check) {
			status.PendingChecks = append(status.PendingChecks, check)
		}
	}
	switch {
	case len(status.FailingChecks) > 0:
		status.Status = "failed"
	case len(status.PendingChecks) > 0:
		status.Status = "pending"
	default:
		status.Status = "passed"
	}
	return status
}

func (client Client) runner() Runner {
	if client.Runner != nil {
		return client.Runner
	}
	return ExecRunner{}
}

func isFailingCheck(check Check) bool {
	conclusion := strings.ToUpper(check.Conclusion)
	return conclusion == "FAILURE" || conclusion == "CANCELLED" || conclusion == "TIMED_OUT" || conclusion == "ACTION_REQUIRED" || conclusion == "ERROR"
}

func isPendingCheck(check Check) bool {
	state := strings.ToUpper(check.State)
	conclusion := strings.ToUpper(check.Conclusion)
	return conclusion == "" || state == "PENDING" || state == "QUEUED" || state == "IN_PROGRESS"
}
