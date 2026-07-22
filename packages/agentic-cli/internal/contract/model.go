package contract

type Operation struct {
	Operation      string    `yaml:"operation"`
	Version        int       `yaml:"version"`
	Purpose        string    `yaml:"purpose"`
	TaskType       string    `yaml:"task_type"`
	AllowedStages  []string  `yaml:"allowed_stages"`
	RequiredInputs []string  `yaml:"required_inputs"`
	SideEffects    []string  `yaml:"side_effects"`
	HumanGate      HumanGate `yaml:"human_gate"`
}

type HumanGate struct {
	Required bool `yaml:"required"`
}
