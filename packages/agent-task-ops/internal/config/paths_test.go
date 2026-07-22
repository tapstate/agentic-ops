package config

import "testing"

func TestDefaultInstallDir(t *testing.T) {
	got := DefaultInstallDir("/home/dev")
	if got != "/home/dev/.agentic-ops" {
		t.Fatalf("got %q", got)
	}
}
