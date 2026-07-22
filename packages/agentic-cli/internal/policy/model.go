package policy

type Policy struct {
	Policy  string          `yaml:"policy"`
	Version int             `yaml:"version"`
	Gates   map[string]Gate `yaml:"gates"`
}

type Gate struct {
	Required bool `yaml:"required"`
}
