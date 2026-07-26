package admission

type Standard struct {
	TaskClass      string                   `yaml:"task_class" json:"task_class"`
	RequiredFields []string                 `yaml:"required_fields" json:"required_fields"`
	OptionalFields []string                 `yaml:"optional_fields" json:"optional_fields,omitempty"`
	Template       string                   `yaml:"template" json:"template"`
	Guidance       map[string]FieldGuidance `yaml:"guidance" json:"guidance,omitempty"`
	AnalysisHints  map[string]AnalysisHint  `yaml:"analysis_hints" json:"analysis_hints,omitempty"`
	PreFixGate     PreFixGate               `yaml:"pre_fix_gate" json:"pre_fix_gate,omitempty"`
}

type FieldGuidance struct {
	Label       string `yaml:"label" json:"label"`
	Location    string `yaml:"location" json:"location"`
	Example     string `yaml:"example" json:"example"`
	Description string `yaml:"description" json:"description"`
}

type AnalysisHint struct {
	Source  string `yaml:"source" json:"source"`
	Message string `yaml:"message" json:"message"`
}

type PreFixGate struct {
	Required    bool   `yaml:"required" json:"required"`
	MustConfirm bool   `yaml:"must_confirm" json:"must_confirm"`
	Message     string `yaml:"message" json:"message,omitempty"`
}

type CheckResult struct {
	OK            bool            `json:"ok"`
	MissingFields []string        `json:"missing_fields,omitempty"`
	Guidance      []FieldGuidance `json:"guidance,omitempty"`
}

func Check(standard Standard, values map[string]string) CheckResult {
	var missing []string
	var guidance []FieldGuidance
	for _, field := range standard.RequiredFields {
		if values[field] == "" {
			missing = append(missing, field)
			guidance = append(guidance, standard.Guidance[field])
		}
	}
	return CheckResult{
		OK:            len(missing) == 0,
		MissingFields: missing,
		Guidance:      guidance,
	}
}

func DefaultStandard(taskClass string) Standard {
	required := []string{"acceptance_criteria", "target_repo", "verification_method", "risk_level"}
	optional := []string{}
	if taskClass == "bug_fix" {
		required = []string{"problem_branch", "target_branch", "problem_summary"}
		optional = []string{"reproduction_path", "acceptance_criteria"}
	}
	return Standard{
		TaskClass:      taskClass,
		RequiredFields: required,
		OptionalFields: optional,
		Template:       "templates/jira-missing-field.md",
	}
}
