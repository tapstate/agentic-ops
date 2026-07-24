package config

import "path/filepath"

func DefaultInstallDir(home string) string {
	return filepath.Join(home, ".agentic-ops")
}

func CurrentPath(installDir string) string {
	return filepath.Join(installDir, "current.json")
}
