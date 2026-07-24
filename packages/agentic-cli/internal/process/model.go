package process

type Process struct {
	ProcessID   string   `yaml:"process_id"`
	TaskClasses []string `yaml:"task_classes"`
	EntryStage  string   `yaml:"entry_stage"`
	Stages      []Stage  `yaml:"stages"`
}

type Stage struct {
	ID         string `yaml:"id"`
	ReviewGate string `yaml:"review_gate"`
}

func (process Process) HasStage(stageID string) bool {
	for _, stage := range process.Stages {
		if stage.ID == stageID {
			return true
		}
	}
	return false
}
