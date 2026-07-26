package localenv

import (
	"os"
	"path/filepath"
	"testing"
)

func TestLookupReadsDotenvValues(t *testing.T) {
	path := filepath.Join(t.TempDir(), ".env")
	if err := os.WriteFile(path, []byte("TOKEN=plain\nQUOTED=\"quoted value\"\nexport EXPORTED=ok # comment\nEMPTY=\n"), 0o600); err != nil {
		t.Fatalf("WriteFile error = %v", err)
	}

	for key, want := range map[string]string{
		"TOKEN":    "plain",
		"QUOTED":   "quoted value",
		"EXPORTED": "ok",
	} {
		got, ok, err := Lookup(path, key)
		if err != nil {
			t.Fatalf("Lookup(%s) error = %v", key, err)
		}
		if !ok || got != want {
			t.Fatalf("Lookup(%s) = %q, %v; want %q, true", key, got, ok, want)
		}
	}

	if got, ok, err := Lookup(path, "EMPTY"); err != nil || ok || got != "" {
		t.Fatalf("Lookup(EMPTY) = %q, %v, %v; want empty false nil", got, ok, err)
	}
}
