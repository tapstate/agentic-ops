package policy

type Policy struct {
	Policy              string                        `yaml:"policy"`
	Version             int                           `yaml:"version"`
	Gates               map[string]Gate               `yaml:"gates"`
	AuthorizationScopes map[string]AuthorizationScope `yaml:"authorization_scopes"`
}

type Gate struct {
	Required bool `yaml:"required"`
}

type AuthorizationScope struct {
	ConfirmationSource string   `yaml:"confirmation_source"`
	RequiredBindings   []string `yaml:"required_bindings"`
	CoveredOperations  []string `yaml:"covered_operations"`
	ExcludedOperations []string `yaml:"excluded_operations"`
	InvalidatedBy      []string `yaml:"invalidated_by"`
}
