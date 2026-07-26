package cli

import (
	"bytes"
	"path/filepath"
	"testing"
)

func TestConfGetsEffectiveJiraConfigValue(t *testing.T) {
	installDir := t.TempDir()
	t.Setenv("AGENTIC_OPS_HOME", installDir)
	writeCLITestFile(t, filepath.Join(installDir, "user", "config.local.yaml"), "projects:\n  tapdata:\n    jira:\n      adapter: real\n      base_url: https://tapdata.atlassian.net\n      email: lead@example.com\n      api_token_env: TAPDATA_JIRA_TOKEN\n")

	var stdout bytes.Buffer
	var stderr bytes.Buffer
	code := Run([]string{"conf", "jira.base_url", "--workspace", "tapdata"}, &stdout, &stderr)
	if code != 0 {
		t.Fatalf("code = %d stdout = %s stderr = %s", code, stdout.String(), stderr.String())
	}
	assertJSONField(t, stdout.String(), "operation", "conf_get")
	assertJSONField(t, stdout.String(), "key", "jira.base_url")
	assertJSONField(t, stdout.String(), "value", "https://tapdata.atlassian.net")
	assertJSONField(t, stdout.String(), "source", filepath.Join(installDir, "user", "config.local.yaml"))
}

func TestConfReturnsCentralConfigPaths(t *testing.T) {
	installDir := t.TempDir()
	t.Setenv("AGENTIC_OPS_HOME", installDir)

	var stdout bytes.Buffer
	var stderr bytes.Buffer
	code := Run([]string{"conf", "paths.user_env"}, &stdout, &stderr)
	if code != 0 {
		t.Fatalf("code = %d stdout = %s stderr = %s", code, stdout.String(), stderr.String())
	}
	assertJSONField(t, stdout.String(), "operation", "conf_get")
	assertJSONField(t, stdout.String(), "key", "paths.user_env")
	assertJSONField(t, stdout.String(), "value", filepath.Join(installDir, "user", ".env"))
}

func TestConfDoesNotRevealJiraTokenValue(t *testing.T) {
	installDir := t.TempDir()
	t.Setenv("AGENTIC_OPS_HOME", installDir)
	writeCLITestFile(t, filepath.Join(installDir, "user", "config.local.yaml"), "projects:\n  tapdata:\n    jira:\n      adapter: real\n      base_url: https://tapdata.atlassian.net\n      email: lead@example.com\n      api_token_env: TAPDATA_JIRA_TOKEN\n")
	writeCLITestFile(t, filepath.Join(installDir, "user", ".env"), "TAPDATA_JIRA_TOKEN=secret-token\n")

	var stdout bytes.Buffer
	var stderr bytes.Buffer
	code := Run([]string{"conf", "jira.api_token", "--workspace", "tapdata"}, &stdout, &stderr)
	if code != 1 {
		t.Fatalf("code = %d stdout = %s stderr = %s", code, stdout.String(), stderr.String())
	}
	assertJSONField(t, stdout.String(), "operation", "conf_get")
	assertJSONField(t, stdout.String(), "code", "conf_secret_redacted")
	assertJSONField(t, stdout.String(), "key", "jira.api_token")
	assertJSONField(t, stdout.String(), "secret", true)
}
