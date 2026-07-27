package jira

import "context"

type Client interface {
	CurrentUser(ctx context.Context) (string, error)
	SearchIssues(ctx context.Context, workspace string, jql string) ([]Issue, error)
	GetIssueByKey(ctx context.Context, workspace string, key string) (Issue, bool, error)
	AddComment(ctx context.Context, key string, body string) error
	UpdateDescriptionSections(ctx context.Context, key string, sections map[string]string) error
	UpdateFields(ctx context.Context, key string, fields map[string]any) error
	Transitions(ctx context.Context, key string) ([]Transition, error)
	TransitionIssue(ctx context.Context, key string, transitionID string) error
}

type Transition struct {
	ID   string
	Name string
}
