package cli

import (
	"bytes"
	"strings"
	"testing"
)

func TestVersionOutputsJSON(t *testing.T) {
	var stdout bytes.Buffer
	var stderr bytes.Buffer
	code := Run([]string{"--version"}, &stdout, &stderr)
	if code != 0 {
		t.Fatalf("code = %d, want 0", code)
	}
	assertJSONField(t, stdout.String(), "operation", "version")
	assertJSONField(t, stdout.String(), "version", "SRC-source")
	assertJSONField(t, stdout.String(), "version_state", "SRC")
	assertJSONField(t, stdout.String(), "iteration_version", "source")
	assertJSONNumber(t, stdout.String(), "commit_index", 0)
	assertJSONField(t, stdout.String(), "commit", "unknown")
	if stderr.String() != "" {
		t.Fatalf("stderr = %s", stderr.String())
	}
}

func TestUnknownCommandFailsWithStableCode(t *testing.T) {
	var stdout bytes.Buffer
	var stderr bytes.Buffer
	code := Run([]string{"missing"}, &stdout, &stderr)
	if code != 1 {
		t.Fatalf("code = %d, want 1", code)
	}
	assertJSONField(t, stdout.String(), "code", "unknown_command")
	if !strings.Contains(stdout.String(), "agentic-cli -h") {
		t.Fatalf("stdout missing help guidance: %s", stdout.String())
	}
	if !strings.Contains(stderr.String(), "unknown command: missing") {
		t.Fatalf("stderr = %s", stderr.String())
	}
}

func TestRootHelpListsGlobalCommandsAndProjectNamespaces(t *testing.T) {
	var stdout bytes.Buffer
	var stderr bytes.Buffer
	code := Run([]string{"-h"}, &stdout, &stderr)
	if code != 0 {
		t.Fatalf("code = %d stdout = %s stderr = %s", code, stdout.String(), stderr.String())
	}
	for _, want := range []string{
		"Usage: agentic-cli <command>",
		"workspace init",
		"agent init",
		"tapdata",
	} {
		if !strings.Contains(stdout.String(), want) {
			t.Fatalf("root help missing %s: %s", want, stdout.String())
		}
	}
}

func TestTapdataNamespaceHelpListsBranchAlign(t *testing.T) {
	var stdout bytes.Buffer
	var stderr bytes.Buffer
	code := Run([]string{"tapdata", "-h"}, &stdout, &stderr)
	if code != 0 {
		t.Fatalf("code = %d stdout = %s stderr = %s", code, stdout.String(), stderr.String())
	}
	for _, want := range []string{
		"Usage: agentic-cli tapdata <tool>",
		"branch-align",
		"TapData 多仓分支对齐",
	} {
		if !strings.Contains(stdout.String(), want) {
			t.Fatalf("tapdata help missing %s: %s", want, stdout.String())
		}
	}
}

func TestWorkspaceInitHelpListsInteractiveAndFlagModes(t *testing.T) {
	var stdout bytes.Buffer
	var stderr bytes.Buffer
	code := Run([]string{"workspace", "init", "-h"}, &stdout, &stderr)
	if code != 0 {
		t.Fatalf("code = %d stdout = %s stderr = %s", code, stdout.String(), stderr.String())
	}
	for _, want := range []string{
		"Usage: agentic-cli workspace init --project <project> [--interactive]",
		"workspace init --project tapdata --interactive",
		"--jira-base-url",
		"--jira-token-env",
	} {
		if !strings.Contains(stdout.String(), want) {
			t.Fatalf("workspace init help missing %s: %s", want, stdout.String())
		}
	}
}

func TestTapdataBranchAlignHelpListsActions(t *testing.T) {
	var stdout bytes.Buffer
	var stderr bytes.Buffer
	code := Run([]string{"tapdata", "branch-align", "-h"}, &stdout, &stderr)
	if code != 0 {
		t.Fatalf("code = %d stdout = %s stderr = %s", code, stdout.String(), stderr.String())
	}
	for _, want := range []string{
		"Usage: agentic-cli tapdata branch-align <list|status|plan|apply>",
		"plan develop",
		"apply develop",
	} {
		if !strings.Contains(stdout.String(), want) {
			t.Fatalf("branch-align help missing %s: %s", want, stdout.String())
		}
	}
}
