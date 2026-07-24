package process

import (
	"os"
	"path/filepath"
	"strings"

	"gopkg.in/yaml.v3"
)

func LoadFile(path string) (Process, error) {
	data, err := os.ReadFile(path)
	if err != nil {
		return Process{}, err
	}
	var process Process
	if err := yaml.Unmarshal(data, &process); err != nil {
		return Process{}, err
	}
	return process, nil
}

func LoadRegistry(dir string) (map[string]Process, error) {
	entries, err := os.ReadDir(dir)
	if err != nil {
		return nil, err
	}
	registry := map[string]Process{}
	for _, entry := range entries {
		if entry.IsDir() || !strings.HasSuffix(entry.Name(), ".yaml") {
			continue
		}
		process, err := LoadFile(filepath.Join(dir, entry.Name()))
		if err != nil {
			return nil, err
		}
		if process.ProcessID != "" {
			registry[process.ProcessID] = process
		}
	}
	return registry, nil
}
