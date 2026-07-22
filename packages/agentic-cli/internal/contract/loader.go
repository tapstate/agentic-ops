package contract

import (
	"os"

	"gopkg.in/yaml.v3"
)

func LoadFile(path string) (Operation, error) {
	data, err := os.ReadFile(path)
	if err != nil {
		return Operation{}, err
	}
	var op Operation
	if err := yaml.Unmarshal(data, &op); err != nil {
		return Operation{}, err
	}
	return op, nil
}
