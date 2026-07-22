package policy

import (
	"os"

	"gopkg.in/yaml.v3"
)

func LoadFile(path string) (Policy, error) {
	data, err := os.ReadFile(path)
	if err != nil {
		return Policy{}, err
	}
	var policy Policy
	if err := yaml.Unmarshal(data, &policy); err != nil {
		return Policy{}, err
	}
	return policy, nil
}
