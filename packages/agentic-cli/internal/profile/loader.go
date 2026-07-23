package profile

import (
	"os"

	"gopkg.in/yaml.v3"
)

func LoadFile(path string) (Profile, error) {
	data, err := os.ReadFile(path)
	if err != nil {
		return Profile{}, err
	}
	var profile Profile
	if err := yaml.Unmarshal(data, &profile); err != nil {
		return Profile{}, err
	}
	return profile, nil
}
