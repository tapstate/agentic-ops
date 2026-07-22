package jira

import (
	"bytes"
	"context"
	"encoding/json"
	"io"
	"net/http"
	"strings"
	"testing"

	"github.com/tapstate/agentic-ops/packages/agentic-cli/internal/profile"
)

func TestRealClientCurrentUserUsesMyselfEndpoint(t *testing.T) {
	client := newTestRealClient(t, func(r *http.Request) *http.Response {
		assertRealJiraRequest(t, r, http.MethodGet, "/rest/api/3/myself")
		return jsonResponse(http.StatusOK, `{"accountId":"account-123","emailAddress":"owner@example.com"}`)
	})

	currentUser, err := client.CurrentUser(context.Background())
	if err != nil {
		t.Fatalf("CurrentUser error = %v", err)
	}
	if currentUser != "account-123" {
		t.Fatalf("currentUser = %s", currentUser)
	}
}

func TestRealClientSearchIssuesMapsProfileFields(t *testing.T) {
	client := newTestRealClient(t, func(r *http.Request) *http.Response {
		assertRealJiraRequest(t, r, http.MethodPost, "/rest/api/3/search/jql")
		var body map[string]any
		if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
			t.Fatalf("Decode body error = %v", err)
		}
		if body["jql"] != "assignee = currentUser()" {
			t.Fatalf("jql = %v", body["jql"])
		}
		return jsonResponse(http.StatusOK, `{"issues":[{"key":"TAP-123","fields":{"summary":"修复示例任务","assignee":{"accountId":"account-123"},"issuetype":{"name":"Task"},"status":{"name":"To Do"},"customfield_acceptance":"单元测试通过","customfield_target_repo":"tapstate/example-repo","customfield_risk":{"value":"low"},"customfield_current_agent_id":"agent-1","description":{"type":"doc","content":[{"type":"paragraph","content":[{"type":"text","text":"验证方式"}]},{"type":"paragraph","content":[{"type":"text","text":"go test ./..."}]}]}}}]}`)
	})

	issues, err := client.SearchIssues(context.Background(), "tapstate", "assignee = currentUser()")
	if err != nil {
		t.Fatalf("SearchIssues error = %v", err)
	}
	if len(issues) != 1 {
		t.Fatalf("len = %d", len(issues))
	}
	got := issues[0]
	if got.Key != "TAP-123" || got.Owner != "account-123" || got.Assignee != "account-123" {
		t.Fatalf("issue owner mapping failed: %+v", got)
	}
	if got.TargetRepo != "tapstate/example-repo" || got.AcceptanceCriteria != "单元测试通过" || got.VerificationMethod != "go test ./..." {
		t.Fatalf("issue standard field mapping failed: %+v", got)
	}
	if got.RiskLevel != "low" || got.CurrentAgentID != "agent-1" {
		t.Fatalf("issue gate field mapping failed: %+v", got)
	}
}

func TestRealClientUpdateFieldsUsesIssueEditEndpoint(t *testing.T) {
	client := newTestRealClient(t, func(r *http.Request) *http.Response {
		assertRealJiraRequest(t, r, http.MethodPut, "/rest/api/3/issue/TAP-123")
		var body map[string]map[string]any
		if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
			t.Fatalf("Decode body error = %v", err)
		}
		if body["fields"]["customfield_current_agent_id"] != "agent-1" {
			t.Fatalf("fields = %#v", body["fields"])
		}
		return jsonResponse(http.StatusNoContent, "")
	})

	err := client.UpdateFields(context.Background(), "TAP-123", map[string]any{
		"customfield_current_agent_id": "agent-1",
		"customfield_takeover_at":      "2026-07-21T10:30:12Z",
	})
	if err != nil {
		t.Fatalf("UpdateFields error = %v", err)
	}
}

func TestRealClientAddCommentUsesADFBody(t *testing.T) {
	client := newTestRealClient(t, func(r *http.Request) *http.Response {
		assertRealJiraRequest(t, r, http.MethodPost, "/rest/api/3/issue/TAP-123/comment")
		var body map[string]any
		if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
			t.Fatalf("Decode body error = %v", err)
		}
		raw, _ := json.Marshal(body["body"])
		if !strings.Contains(string(raw), "请研发 owner 确认") {
			t.Fatalf("comment body = %s", string(raw))
		}
		return jsonResponse(http.StatusCreated, `{"id":"10000"}`)
	})

	if err := client.AddComment(context.Background(), "TAP-123", "请研发 owner 确认"); err != nil {
		t.Fatalf("AddComment error = %v", err)
	}
}

func TestRealClientTransitionIssueUsesTransitionEndpoint(t *testing.T) {
	client := newTestRealClient(t, func(r *http.Request) *http.Response {
		assertRealJiraRequest(t, r, http.MethodPost, "/rest/api/3/issue/TAP-123/transitions")
		var body map[string]map[string]string
		if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
			t.Fatalf("Decode body error = %v", err)
		}
		if body["transition"]["id"] != "31" {
			t.Fatalf("transition body = %#v", body)
		}
		return jsonResponse(http.StatusNoContent, "")
	})

	if err := client.TransitionIssue(context.Background(), "TAP-123", "31"); err != nil {
		t.Fatalf("TransitionIssue error = %v", err)
	}
}

func newTestRealClient(t *testing.T, handler func(*http.Request) *http.Response) *RealClient {
	t.Helper()
	client, err := NewRealClient(RealClientConfig{
		BaseURL:  "https://jira.example.test",
		Email:    "bot@example.com",
		APIToken: "token-123",
		Profile:  validRealClientProfile(),
		HTTPClient: &http.Client{
			Transport: roundTripFunc(handler),
		},
	})
	if err != nil {
		t.Fatalf("NewRealClient error = %v", err)
	}
	return client
}

type roundTripFunc func(*http.Request) *http.Response

func (fn roundTripFunc) RoundTrip(request *http.Request) (*http.Response, error) {
	return fn(request), nil
}

func jsonResponse(statusCode int, body string) *http.Response {
	return &http.Response{
		StatusCode: statusCode,
		Header:     make(http.Header),
		Body:       io.NopCloser(bytes.NewBufferString(body)),
	}
}

func assertRealJiraRequest(t *testing.T, request *http.Request, method string, requestPath string) {
	t.Helper()
	if request.Method != method {
		t.Fatalf("method = %s, want %s", request.Method, method)
	}
	if request.URL.Path != requestPath {
		t.Fatalf("path = %s, want %s", request.URL.Path, requestPath)
	}
	email, token, ok := request.BasicAuth()
	if !ok || email != "bot@example.com" || token != "token-123" {
		t.Fatalf("basic auth missing or wrong")
	}
}

func validRealClientProfile() profile.Profile {
	return profile.Profile{
		JiraFormMapping: profile.FormMapping{
			Fields: map[string]profile.FormField{
				"owner": {
					Source:    "jira_field",
					JiraField: "assignee",
				},
				"acceptance_criteria": {
					Source:    "jira_field",
					JiraField: "customfield_acceptance",
				},
				"target_repo": {
					Source:    "jira_field",
					JiraField: "customfield_target_repo",
				},
				"verification_method": {
					Source:  "jira_description_section",
					Section: "验证方式",
				},
				"risk_level": {
					Source:    "jira_field",
					JiraField: "customfield_risk",
				},
				"current_agent_id": {
					Source:    "jira_field",
					JiraField: "customfield_current_agent_id",
				},
			},
		},
	}
}
