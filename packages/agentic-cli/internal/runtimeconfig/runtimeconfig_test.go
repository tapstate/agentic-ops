package runtimeconfig

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestScopePathsUseCentralConfigAndEnvFiles(t *testing.T) {
	installDir := filepath.Join(t.TempDir(), ".agentic-ops")
	workspaceRoot := filepath.Join(t.TempDir(), "workspace")

	scope := NewScope(installDir, workspaceRoot, "tapdata")

	if got, want := scope.UserConfigPath(), filepath.Join(installDir, "user", "config.local.yaml"); got != want {
		t.Fatalf("UserConfigPath = %s, want %s", got, want)
	}
	if got, want := scope.UserEnvPath(), filepath.Join(installDir, "user", ".env"); got != want {
		t.Fatalf("UserEnvPath = %s, want %s", got, want)
	}
	if got, want := scope.WorkspaceConfigPath(), filepath.Join(workspaceRoot, ".agentic-ops", "config.local.yaml"); got != want {
		t.Fatalf("WorkspaceConfigPath = %s, want %s", got, want)
	}
	if got, want := scope.WorkspaceEnvPath(), filepath.Join(workspaceRoot, ".agentic-ops", ".env"); got != want {
		t.Fatalf("WorkspaceEnvPath = %s, want %s", got, want)
	}
}

func TestLookupEnvUsesProcessWorkspaceThenUser(t *testing.T) {
	installDir := filepath.Join(t.TempDir(), ".agentic-ops")
	workspaceRoot := filepath.Join(t.TempDir(), "workspace")
	scope := NewScope(installDir, workspaceRoot, "tapdata")
	writeRuntimeConfigTestFile(t, scope.UserEnvPath(), "TOKEN=user-token\n")
	writeRuntimeConfigTestFile(t, scope.WorkspaceEnvPath(), "TOKEN=workspace-token\n")

	got, ok, err := scope.LookupEnv("TOKEN")
	if err != nil {
		t.Fatalf("LookupEnv error = %v", err)
	}
	if !ok || got != "workspace-token" {
		t.Fatalf("LookupEnv = %q, %v; want workspace-token, true", got, ok)
	}

	t.Setenv("TOKEN", "process-token")
	got, ok, err = scope.LookupEnv("TOKEN")
	if err != nil {
		t.Fatalf("LookupEnv with process env error = %v", err)
	}
	if !ok || got != "process-token" {
		t.Fatalf("LookupEnv with process env = %q, %v; want process-token, true", got, ok)
	}
}

func TestEnsureEnvPlaceholderCreatesAndAppendsCentralEnv(t *testing.T) {
	scope := NewScope(filepath.Join(t.TempDir(), ".agentic-ops"), "", "tapdata")

	if err := scope.EnsureUserEnvPlaceholder("TOKEN_A", "first token"); err != nil {
		t.Fatalf("EnsureUserEnvPlaceholder TOKEN_A error = %v", err)
	}
	if err := scope.EnsureUserEnvPlaceholder("TOKEN_B", "second token"); err != nil {
		t.Fatalf("EnsureUserEnvPlaceholder TOKEN_B error = %v", err)
	}
	if err := scope.EnsureUserEnvPlaceholder("TOKEN_A", "first token"); err != nil {
		t.Fatalf("EnsureUserEnvPlaceholder TOKEN_A repeat error = %v", err)
	}

	data, err := os.ReadFile(scope.UserEnvPath())
	if err != nil {
		t.Fatalf("ReadFile env error = %v", err)
	}
	got := string(data)
	for _, want := range []string{"# first token\nTOKEN_A=\n", "# second token\nTOKEN_B=\n"} {
		if !strings.Contains(got, want) {
			t.Fatalf("env file missing %q: %s", want, got)
		}
	}
	if strings.Count(got, "TOKEN_A=") != 1 {
		t.Fatalf("TOKEN_A placeholder should be written once: %s", got)
	}
}

func TestRegistryFindsRegisteredModuleFields(t *testing.T) {
	registry := NewRegistry()
	registry.Register(ModuleSpec{
		Name: "jira",
		Fields: []FieldSpec{
			{Key: "base_url", Default: "https://tapdata.atlassian.net", Prompt: "Jira base URL", Target: "config"},
			{Key: "api_token", EnvName: "AGENTIC_OPS_JIRA_API_TOKEN", Prompt: "Jira API token", Target: "env", Secret: true},
		},
	})

	module, field, ok := registry.FindField("jira.base_url")
	if !ok {
		t.Fatalf("FindField(jira.base_url) did not find registered field")
	}
	if module.Name != "jira" || field.Key != "base_url" || field.Default != "https://tapdata.atlassian.net" || field.Target != "config" {
		t.Fatalf("FindField(jira.base_url) = %#v %#v", module, field)
	}

	_, tokenField, ok := registry.FindField("jira.api_token")
	if !ok || !tokenField.Secret || tokenField.Target != "env" {
		t.Fatalf("FindField(jira.api_token) = %#v, %v", tokenField, ok)
	}

	if _, _, ok := registry.FindField("jira.unknown"); ok {
		t.Fatalf("FindField(jira.unknown) should not find a field")
	}
}

func writeRuntimeConfigTestFile(t *testing.T, path string, data string) {
	t.Helper()
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		t.Fatalf("MkdirAll(%s) error = %v", filepath.Dir(path), err)
	}
	if err := os.WriteFile(path, []byte(data), 0o600); err != nil {
		t.Fatalf("WriteFile(%s) error = %v", path, err)
	}
}
