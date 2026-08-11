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
	baseURL, err := url.Parse(NormalizeBaseURL(config.BaseURL))
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

func NormalizeBaseURL(value string) string {
	value = strings.TrimSpace(value)
	parsed, err := url.Parse(value)
	if err != nil {
		return strings.TrimRight(value, "/")
	}
	normalizedPath := strings.TrimRight(parsed.Path, "/")
	if normalizedPath == "/jira" {
		parsed.Path = ""
	}
	parsed.RawQuery = ""
	parsed.Fragment = ""
	return strings.TrimRight(parsed.String(), "/")
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

func (client *RealClient) UpdateDescriptionSections(ctx context.Context, key string, sections map[string]string) error {
	requestPath := "/rest/api/3/issue/" + url.PathEscape(key)
	query := url.Values{}
	query.Set("fields", "description")
	var issue jiraIssueResponse
	if err := client.doJSON(ctx, http.MethodGet, requestPath+"?"+query.Encode(), nil, &issue); err != nil {
		return err
	}
	merged, err := mergeDescriptionSections(issue.Fields["description"], sections)
	if err != nil {
		return err
	}
	return client.UpdateFields(ctx, key, map[string]any{"description": merged})
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

func (client *RealClient) TransitionIssue(ctx context.Context, key string, request TransitionRequest) error {
	requestPath := "/rest/api/3/issue/" + url.PathEscape(key) + "/transitions"
	payload := map[string]any{
		"transition": map[string]string{
			"id": request.ID,
		},
	}
	if len(request.Fields) > 0 {
		payload["fields"] = request.Fields
	}
	if strings.TrimSpace(request.Comment) != "" {
		payload["update"] = map[string]any{
			"comment": []any{map[string]any{
				"add": map[string]any{"body": adfDocument(request.Comment)},
			}},
		}
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
		"comment":     true,
		"labels":      true,
		"components":  true,
	}
	for _, field := range client.profile.JiraFormMapping.Fields {
		if field.JiraField != "" {
			fields[field.JiraField] = true
		}
		if field.Source == "jira_comment" {
			fields["comment"] = true
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
	formValues := mappedFormValues(fields, client.profile)
	issue := Issue{
		Key:        raw.Key,
		Summary:    stringField(fields["summary"]),
		Assignee:   userIdentifier(fields["assignee"]),
		IssueType:  objectName(fields["issuetype"]),
		Status:     objectName(fields["status"]),
		Labels:     stringList(fields["labels"]),
		Components: objectNameList(fields["components"]),
		FormValues: formValues,
		Comments:   jiraComments(fields["comment"]),
	}
	issue.Owner = formValues["owner"]
	if issue.Owner == "" {
		issue.Owner = issue.Assignee
	}
	issue.TargetRepo = formValues["target_repo"]
	issue.AgenticID = formValues["agentic_id"]
	return issue
}

func jiraComments(value any) []Comment {
	container, ok := value.(map[string]any)
	if !ok {
		return nil
	}
	items, ok := container["comments"].([]any)
	if !ok {
		return nil
	}
	comments := make([]Comment, 0, len(items))
	for _, item := range items {
		raw, ok := item.(map[string]any)
		if !ok {
			continue
		}
		comments = append(comments, Comment{
			ID:      stringField(raw["id"]),
			Author:  userIdentifier(raw["author"]),
			Created: stringField(raw["created"]),
			Updated: stringField(raw["updated"]),
			Body:    strings.TrimSpace(plainText(raw["body"])),
		})
	}
	return comments
}

func mappedFormValues(fields map[string]any, p profile.Profile) map[string]string {
	values := map[string]string{}
	commentValues := jiraCommentOwnershipValues(fields["comment"])
	for name, field := range p.JiraFormMapping.Fields {
		if field.Source == "jira_comment" {
			values[name] = commentValues[name]
			continue
		}
		values[name] = mappedField(fields, p, name)
	}
	return values
}

func mappedField(fields map[string]any, p profile.Profile, name string) string {
	field, ok := p.JiraFormMapping.Fields[name]
	if !ok {
		return ""
	}
	if field.Source == "jira_description_section" {
		return extractADFSection(fields["description"], field.Section)
	}
	if name == "risk_level" && field.JiraField == "labels" {
		return riskLevelFromLabels(stringList(fields[field.JiraField]))
	}
	return stringField(fields[field.JiraField])
}

func extractADFSection(description any, section string) string {
	document, ok := description.(map[string]any)
	if !ok {
		return extractSection(plainText(description), section)
	}
	content, ok := document["content"].([]any)
	if !ok {
		return extractSection(plainText(description), section)
	}
	normalizedSection := normalizeSectionTitle(section)
	collected := make([]string, 0)
	inSection := false
	for _, node := range content {
		if title, isTitle := nodeTitle(node); isTitle {
			if inSection {
				break
			}
			if normalizeSectionTitle(title) == normalizedSection {
				inSection = true
			}
			continue
		}
		if inSection {
			text := strings.TrimSpace(plainText(node))
			if text != "" {
				collected = append(collected, text)
			}
		}
	}
	if inSection {
		return strings.Join(collected, "\n")
	}
	return extractSection(plainText(description), section)
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

func riskLevelFromLabels(labels []string) string {
	for _, label := range labels {
		trimmed := strings.TrimSpace(label)
		normalized := strings.ToUpper(trimmed)
		normalized = strings.TrimPrefix(normalized, "RISK:")
		normalized = strings.TrimPrefix(normalized, "RISK-")
		normalized = strings.TrimPrefix(normalized, "RISK_")
		normalized = strings.TrimPrefix(normalized, "RISK/")
		switch normalized {
		case "T1", "T2", "T3", "T4", "P0", "P1", "P2", "P3", "P4", "LOW", "MEDIUM", "HIGH":
			return trimmed
		}
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

func jiraCommentOwnershipValues(value any) map[string]string {
	result := map[string]string{}
	container, ok := value.(map[string]any)
	if !ok {
		return result
	}
	comments, ok := container["comments"].([]any)
	if !ok {
		return result
	}
	for _, item := range comments {
		comment, ok := item.(map[string]any)
		if !ok {
			continue
		}
		text := plainText(comment["body"])
		if !strings.Contains(text, "AgenticOps ownership") {
			continue
		}
		for _, line := range strings.Split(text, "\n") {
			key, value, ok := strings.Cut(line, ":")
			if !ok {
				continue
			}
			key = strings.TrimSpace(key)
			switch key {
			case "agentic_id", "agentic_run_id", "agentic_takeover_at", "agentic_next_action", "agentic_completion_evidence", "agentic_heartbeat_at":
				result[key] = strings.TrimSpace(value)
			}
		}
	}
	return result
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
		if sectionTitleMatches(trimmed, section) {
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

func sectionTitleMatches(line string, section string) bool {
	line = normalizeSectionTitle(line)
	section = normalizeSectionTitle(section)
	return line != "" && section != "" && line == section
}

func normalizeSectionTitle(value string) string {
	value = strings.TrimSpace(value)
	value = strings.TrimLeft(value, "#")
	value = strings.TrimSpace(value)
	value = strings.TrimRight(value, "：:")
	return strings.TrimSpace(value)
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
