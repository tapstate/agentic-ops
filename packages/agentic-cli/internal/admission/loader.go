package admission

import (
	"os"

	"gopkg.in/yaml.v3"
)

func LoadFile(path string) (Standard, error) {
	data, err := os.ReadFile(path)
	if err != nil {
		return Standard{}, err
	}
	var standard Standard
	if err := yaml.Unmarshal(data, &standard); err != nil {
		return Standard{}, err
	}
	return standard, nil
}
