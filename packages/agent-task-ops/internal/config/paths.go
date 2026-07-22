package config

import "path/filepath"

func DefaultInstallDir(home string) string {
	return filepath.Join(home, ".agentic-ops")
}
