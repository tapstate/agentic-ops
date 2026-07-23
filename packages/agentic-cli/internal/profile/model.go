package profile

type Profile struct {
	Workspace              string                     `yaml:"workspace"`
	Jira                   JiraConfig                 `yaml:"jira"`
	JiraFormMapping        FormMapping                `yaml:"jira_form_mapping"`
	TaskClassMapping       TaskClassMapping           `yaml:"task_class_mapping"`
	StandardProcessMapping map[string]string          `yaml:"standard_process_mapping"`
	StatusMapping          map[string]string          `yaml:"status_mapping"`
	TransitionMapping      map[string]string          `yaml:"transition_mapping"`
	JiraTransitionMapping  map[string]JiraTransition  `yaml:"jira_transition_mapping"`
	GitHub                 GitHubConfig               `yaml:"github"`
	Local                  LocalConfig                `yaml:"local"`
	HumanGates             []string                   `yaml:"human_gates"`
	ReviewGates            map[string]ReviewGate      `yaml:"review_gates"`
	RetryRedo              map[string]RetryRedoPolicy `yaml:"retry_redo"`
	Templates              map[string]string          `yaml:"templates"`
}

type JiraConfig struct {
	User      string `yaml:"user"`
	Project   string `yaml:"project"`
	TaskQuery string `yaml:"task_query"`
}

type JiraTransition struct {
	ID   string `yaml:"id"`
	Name string `yaml:"name"`
}

type FormMapping struct {
	Fields map[string]FormField `yaml:"fields"`
}

type FormField struct {
	Source            string `yaml:"source"`
	JiraField         string `yaml:"jira_field"`
	Section           string `yaml:"section"`
	Fallback          string `yaml:"fallback"`
	RequiredFromStage string `yaml:"required_from_stage"`
}

type TaskClassMapping struct {
	IssueTypes map[string]string `yaml:"issue_types"`
	Labels     map[string]string `yaml:"labels"`
}

type GitHubConfig struct {
	Organization string            `yaml:"organization"`
	Repositories RepositoryMapping `yaml:"repositories"`
}

type RepositoryMapping struct {
	Default     string            `yaml:"default"`
	ByComponent map[string]string `yaml:"by_component"`
	ByLabel     map[string]string `yaml:"by_label"`
	ByIssueType map[string]string `yaml:"by_issue_type"`
}

type LocalConfig struct {
	WorkspaceRoot string `yaml:"workspace_root"`
	SourceRoot    string `yaml:"source_root"`
	RunsDir       string `yaml:"runs_dir"`
	FeedbackDir   string `yaml:"feedback_dir"`
}

type ReviewGate struct {
	Role               string `yaml:"role"`
	DecisionField      string `yaml:"decision_field"`
	ReturnedNextAction string `yaml:"returned_next_action"`
}

type RetryRedoPolicy struct {
	Retry         bool   `yaml:"retry"`
	MaxAttempts   int    `yaml:"max_attempts"`
	RedoFromStage string `yaml:"redo_from_stage"`
	NextAction    string `yaml:"next_action"`
}
