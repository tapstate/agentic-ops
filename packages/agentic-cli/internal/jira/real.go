package jira

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"path"
	"sort"
	"strings"

	"github.com/tapstate/agentic-ops/packages/agentic-cli/internal/profile"
)

type RealClientConfig struct {
	BaseURL    string
	Email      string
	APIToken   string
	Profile    profile.Profile
	HTTPClient *http.Client
}

type RealClient struct {
	baseURL    *url.URL
	email      string
	apiToken   string
	profile    profile.Profile
	httpClient *http.Client
}

func NewRealClient(config RealClientConfig) (*RealClient, error) {
	if config.BaseURL == "" {
		return nil, fmt.Errorf("jira base URL is required")
	}
	if config.Email == "" {
		return nil, fmt.Errorf("jira email is required")
	}
	if config.APIToken == "" {
		return nil, fmt.Errorf("jira API token is required")
	}
	baseURL, err := url.Parse(config.BaseURL)
	if err != nil {
		return nil, fmt.Errorf("parse jira base URL: %w", err)
	}
	httpClient := config.HTTPClient
	if httpClient == nil {
		httpClient = http.DefaultClient
	}
	return &RealClient{
		baseURL:    baseURL,
		email:      config.Email,
		apiToken:   config.APIToken,
		profile:    config.Profile,
		httpClient: httpClient,
	}, nil
}

func (client *RealClient) CurrentUser(ctx context.Context) (string, error) {
	var payload struct {
		AccountID    string `json:"accountId"`
		EmailAddress string `json:"emailAddress"`
		DisplayName  string `json:"displayName"`
	}
	if err := client.doJSON(ctx, http.MethodGet, "/rest/api/3/myself", nil, &payload); err != nil {
		return "", err
	}
	if payload.AccountID != "" {
		return payload.AccountID, nil
	}
	if payload.EmailAddress != "" {
		return payload.EmailAddress, nil
	}
	return payload.DisplayName, nil
}

func (client *RealClient) SearchIssues(ctx context.Context, workspace string, jql string) ([]Issue, error) {
	var payload jiraSearchResponse
	body := map[string]any{
		"jql":        jql,
		"maxResults": 50,
		"fields":     client.issueFields(),
	}
	if err := client.doJSON(ctx, http.MethodPost, "/rest/api/3/search/jql", body, &payload); err != nil {
		return nil, err
	}
	issues := make([]Issue, 0, len(payload.Issues))
	for _, raw := range payload.Issues {
		issues = append(issues, client.mapIssue(raw))
	}
	return issues, nil
}

func (client *RealClient) GetIssueByKey(ctx context.Context, workspace string, key string) (Issue, bool, error) {
	var payload jiraIssueResponse
	requestPath := "/rest/api/3/issue/" + url.PathEscape(key)
	query := url.Values{}
	query.Set("fields", strings.Join(client.issueFields(), ","))
	if err := client.doJSON(ctx, http.MethodGet, requestPath+"?"+query.Encode(), nil, &payload); err != nil {
		if isNotFound(err) {
			return Issue{}, false, nil
		}
		return Issue{}, false, err
	}
	return client.mapIssue(payload), true, nil
}

func (client *RealClient) AddComment(ctx context.Context, key string, body string) error {
	requestPath := "/rest/api/3/issue/" + url.PathEscape(key) + "/comment"
	payload := map[string]any{
		"body": adfDocument(body),
	}
	return client.doJSON(ctx, http.MethodPost, requestPath, payload, nil)
}

func (client *RealClient) UpdateFields(ctx context.Context, key string, fields map[string]any) error {
	requestPath := "/rest/api/3/issue/" + url.PathEscape(key)
	payload := map[string]any{
		"fields": fields,
	}
	return client.doJSON(ctx, http.MethodPut, requestPath, payload, nil)
}

func (client *RealClient) Transitions(ctx context.Context, key string) ([]Transition, error) {
	requestPath := "/rest/api/3/issue/" + url.PathEscape(key) + "/transitions"
	var payload jiraTransitionsResponse
	if err := client.doJSON(ctx, http.MethodGet, requestPath, nil, &payload); err != nil {
		return nil, err
	}
	transitions := make([]Transition, 0, len(payload.Transitions))
	for _, transition := range payload.Transitions {
		transitions = append(transitions, Transition{ID: transition.ID, Name: transition.Name})
	}
	return transitions, nil
}

func (client *RealClient) TransitionIssue(ctx context.Context, key string, transitionID string) error {
	requestPath := "/rest/api/3/issue/" + url.PathEscape(key) + "/transitions"
	payload := map[string]any{
		"transition": map[string]string{
			"id": transitionID,
		},
	}
	return client.doJSON(ctx, http.MethodPost, requestPath, payload, nil)
}

type jiraSearchResponse struct {
	Issues []jiraIssueResponse `json:"issues"`
}

type jiraTransitionsResponse struct {
	Transitions []struct {
		ID   string `json:"id"`
		Name string `json:"name"`
	} `json:"transitions"`
}

type jiraIssueResponse struct {
	Key    string         `json:"key"`
	Fields map[string]any `json:"fields"`
}

func (client *RealClient) issueFields() []string {
	fields := map[string]bool{
		"summary":     true,
		"status":      true,
		"issuetype":   true,
		"assignee":    true,
		"description": true,
		"labels":      true,
		"components":  true,
	}
	for _, field := range client.profile.JiraFormMapping.Fields {
		if field.JiraField != "" {
			fields[field.JiraField] = true
		}
	}
	result := make([]string, 0, len(fields))
	for field := range fields {
		result = append(result, field)
	}
	sort.Strings(result)
	return result
}

func (client *RealClient) mapIssue(raw jiraIssueResponse) Issue {
	fields := raw.Fields
	issue := Issue{
		Key:        raw.Key,
		Summary:    stringField(fields["summary"]),
		Assignee:   userIdentifier(fields["assignee"]),
		IssueType:  objectName(fields["issuetype"]),
		Status:     objectName(fields["status"]),
		Labels:     stringList(fields["labels"]),
		Components: objectNameList(fields["components"]),
	}
	issue.Owner = mappedField(fields, client.profile, "owner")
	if issue.Owner == "" {
		issue.Owner = issue.Assignee
	}
	issue.AcceptanceCriteria = mappedField(fields, client.profile, "acceptance_criteria")
	issue.TargetRepo = mappedField(fields, client.profile, "target_repo")
	issue.VerificationMethod = mappedField(fields, client.profile, "verification_method")
	issue.RiskLevel = mappedField(fields, client.profile, "risk_level")
	issue.CurrentAgentID = mappedField(fields, client.profile, "current_agent_id")
	return issue
}

func mappedField(fields map[string]any, p profile.Profile, name string) string {
	field, ok := p.JiraFormMapping.Fields[name]
	if !ok {
		return ""
	}
	if field.Source == "jira_description_section" {
		return extractSection(plainText(fields["description"]), field.Section)
	}
	return stringField(fields[field.JiraField])
}

func stringField(value any) string {
	switch typed := value.(type) {
	case string:
		return typed
	case map[string]any:
		if value, ok := typed["value"].(string); ok {
			return value
		}
		if name, ok := typed["name"].(string); ok {
			return name
		}
	}
	return ""
}

func stringList(value any) []string {
	values, ok := value.([]any)
	if !ok {
		return nil
	}
	result := make([]string, 0, len(values))
	for _, item := range values {
		text := stringField(item)
		if text != "" {
			result = append(result, text)
		}
	}
	return result
}

func objectNameList(value any) []string {
	values, ok := value.([]any)
	if !ok {
		return nil
	}
	result := make([]string, 0, len(values))
	for _, item := range values {
		name := objectName(item)
		if name != "" {
			result = append(result, name)
		}
	}
	return result
}

func userIdentifier(value any) string {
	user, ok := value.(map[string]any)
	if !ok {
		return stringField(value)
	}
	for _, key := range []string{"accountId", "emailAddress", "displayName", "name"} {
		if value, ok := user[key].(string); ok && value != "" {
			return value
		}
	}
	return ""
}

func objectName(value any) string {
	object, ok := value.(map[string]any)
	if !ok {
		return stringField(value)
	}
	if name, ok := object["name"].(string); ok {
		return name
	}
	return ""
}

func plainText(value any) string {
	switch typed := value.(type) {
	case string:
		return typed
	case []any:
		var parts []string
		for _, item := range typed {
			text := plainText(item)
			if text != "" {
				parts = append(parts, text)
			}
		}
		return strings.Join(parts, "\n")
	case map[string]any:
		if text, ok := typed["text"].(string); ok {
			return text
		}
		return plainText(typed["content"])
	default:
		return ""
	}
}

func extractSection(text string, section string) string {
	if section == "" {
		return strings.TrimSpace(text)
	}
	lines := strings.Split(text, "\n")
	var collected []string
	inSection := false
	for _, line := range lines {
		trimmed := strings.TrimSpace(line)
		if trimmed == section {
			inSection = true
			continue
		}
		if inSection && trimmed != "" && strings.HasSuffix(trimmed, "：") {
			break
		}
		if inSection && trimmed != "" {
			collected = append(collected, trimmed)
		}
	}
	return strings.Join(collected, "\n")
}

func adfDocument(text string) map[string]any {
	return map[string]any{
		"type":    "doc",
		"version": 1,
		"content": []map[string]any{
			{
				"type": "paragraph",
				"content": []map[string]string{
					{
						"type": "text",
						"text": text,
					},
				},
			},
		},
	}
}

func (client *RealClient) doJSON(ctx context.Context, method string, requestPath string, body any, output any) error {
	var reader io.Reader
	if body != nil {
		data, err := json.Marshal(body)
		if err != nil {
			return err
		}
		reader = bytes.NewReader(data)
	}
	requestURL := *client.baseURL
	requestURL.Path = path.Join(client.baseURL.Path, requestPath)
	if strings.Contains(requestPath, "?") {
		parts := strings.SplitN(requestPath, "?", 2)
		requestURL.Path = path.Join(client.baseURL.Path, parts[0])
		requestURL.RawQuery = parts[1]
	}
	request, err := http.NewRequestWithContext(ctx, method, requestURL.String(), reader)
	if err != nil {
		return err
	}
	request.SetBasicAuth(client.email, client.apiToken)
	request.Header.Set("Accept", "application/json")
	if body != nil {
		request.Header.Set("Content-Type", "application/json")
	}
	response, err := client.httpClient.Do(request)
	if err != nil {
		return err
	}
	defer response.Body.Close()
	if response.StatusCode < 200 || response.StatusCode >= 300 {
		return jiraHTTPError{statusCode: response.StatusCode}
	}
	if output == nil {
		_, _ = io.Copy(io.Discard, response.Body)
		return nil
	}
	return json.NewDecoder(response.Body).Decode(output)
}

type jiraHTTPError struct {
	statusCode int
}

func (err jiraHTTPError) Error() string {
	return fmt.Sprintf("jira request failed with status %d", err.statusCode)
}

func isNotFound(err error) bool {
	httpErr, ok := err.(jiraHTTPError)
	return ok && httpErr.statusCode == http.StatusNotFound
}
