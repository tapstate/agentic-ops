package contract

type Operation struct {
	Operation      string               `yaml:"operation"`
	Version        int                  `yaml:"version"`
	Purpose        string               `yaml:"purpose"`
	TaskType       string               `yaml:"task_type"`
	AllowedStages  []string             `yaml:"allowed_stages"`
	RequiredInputs []string             `yaml:"required_inputs"`
	Input          map[string]FieldSpec `yaml:"input"`
	Preconditions  []string             `yaml:"preconditions"`
	Output         map[string]FieldSpec `yaml:"output"`
	Failure        FailureSpec          `yaml:"failure"`
	SideEffects    []string             `yaml:"side_effects"`
	HumanGate      *HumanGate           `yaml:"human_gate"`
	RetryPolicy    RetryPolicy          `yaml:"retry_policy"`
	RedoFromStage  string               `yaml:"redo_from_stage"`
}

type FieldSpec struct {
	Type     string   `yaml:"type"`
	Required bool     `yaml:"required"`
	Enum     []string `yaml:"enum"`
	Fields   []string `yaml:"fields"`
}

type FailureSpec struct {
	Codes []string `yaml:"codes"`
}

type HumanGate struct {
	Required bool `yaml:"required"`
}

type RetryPolicy struct {
	Retryable     bool   `yaml:"retryable"`
	MaxAttempts   int    `yaml:"max_attempts"`
	RedoFromStage string `yaml:"redo_from_stage"`
}
