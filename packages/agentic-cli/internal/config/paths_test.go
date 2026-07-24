package config

import (
	"path/filepath"
	"testing"
)

func TestDefaultInstallDir(t *testing.T) {
	got := DefaultInstallDir("/home/dev")
	if got != "/home/dev/.agentic-ops" {
		t.Fatalf("got %q", got)
	}
}

func TestCurrentPath(t *testing.T) {
	got := CurrentPath("/home/dev/.agentic-ops")
	if got != filepath.Join("/home/dev/.agentic-ops", "current.json") {
		t.Fatalf("CurrentPath = %s", got)
	}
}
