package github

import (
	"context"
	"encoding/json"
	"fmt"
	"os/exec"
	"strings"
)

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

type PRComment struct {
	Kind   string `json:"kind"`
	Author string `json:"author"`
	Body   string `json:"body"`
	State  string `json:"state,omitempty"`
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
}

func (client Client) ReadPRComments(ctx context.Context, repo string, pr string) ([]PRComment, error) {
	output, err := client.runner().Run(ctx, "pr", "view", pr, "--repo", repo, "--json", "comments,reviews")
	if err != nil {
		return nil, err
	}
	var payload struct {
		Comments []struct {
			Author struct {
				Login string `json:"login"`
			} `json:"author"`
			Body string `json:"body"`
			URL  string `json:"url"`
		} `json:"comments"`
		Reviews []struct {
			Author struct {
				Login string `json:"login"`
			} `json:"author"`
			Body  string `json:"body"`
			State string `json:"state"`
			URL   string `json:"url"`
		} `json:"reviews"`
	}
	if err := json.Unmarshal(output, &payload); err != nil {
		return nil, err
	}
	comments := make([]PRComment, 0, len(payload.Comments)+len(payload.Reviews))
	for _, comment := range payload.Comments {
		comments = append(comments, PRComment{
			Kind:   "comment",
			Author: comment.Author.Login,
			Body:   comment.Body,
			URL:    comment.URL,
		})
	}
	for _, review := range payload.Reviews {
		comments = append(comments, PRComment{
			Kind:   "review",
			Author: review.Author.Login,
			Body:   review.Body,
			State:  review.State,
			URL:    review.URL,
		})
	}
	return comments, nil
}

func (client Client) CheckCIStatus(ctx context.Context, repo string, pr string) (CIStatus, error) {
	output, err := client.runner().Run(ctx, "pr", "checks", pr, "--repo", repo, "--json", "name,state,conclusion,detailsUrl")
	if err != nil {
		return CIStatus{}, err
	}
	var rawChecks []struct {
		Name       string `json:"name"`
		State      string `json:"state"`
		Conclusion string `json:"conclusion"`
		DetailsURL string `json:"detailsUrl"`
	}
	if err := json.Unmarshal(output, &rawChecks); err != nil {
		return CIStatus{}, err
	}
	status := CIStatus{Status: "passed"}
	for _, raw := range rawChecks {
		check := Check{
			Name:       raw.Name,
			State:      raw.State,
			Conclusion: raw.Conclusion,
			DetailsURL: raw.DetailsURL,
		}
		status.Checks = append(status.Checks, check)
		if isFailingCheck(check) {
			status.FailingChecks = append(status.FailingChecks, check)
			status.Status = "failed"
			continue
		}
		if status.Status != "failed" && isPendingCheck(check) {
			status.Status = "pending"
		}
	}
	return status, nil
}

func (client Client) runner() Runner {
	if client.Runner != nil {
		return client.Runner
	}
	return ExecRunner{}
}

func isFailingCheck(check Check) bool {
	conclusion := strings.ToUpper(check.Conclusion)
	return conclusion == "FAILURE" || conclusion == "CANCELLED" || conclusion == "TIMED_OUT" || conclusion == "ACTION_REQUIRED"
}

func isPendingCheck(check Check) bool {
	state := strings.ToUpper(check.State)
	conclusion := strings.ToUpper(check.Conclusion)
	return conclusion == "" || state == "PENDING" || state == "QUEUED" || state == "IN_PROGRESS"
}
