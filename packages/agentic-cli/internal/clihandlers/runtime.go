package clihandlers

import (
	"context"
	"encoding/json"
	"fmt"
	"github.com/tapstate/agentic-ops/packages/agentic-cli/internal/config"
	gitops "github.com/tapstate/agentic-ops/packages/agentic-cli/internal/git"
	"github.com/tapstate/agentic-ops/packages/agentic-cli/internal/github"
	"github.com/tapstate/agentic-ops/packages/agentic-cli/internal/runtimeclock"
	"io"
	"os"
	"os/exec"
	"regexp"
	"strings"
)

var Version = "SRC-source"

var VersionState = "SRC"

var IterationVersion = "source"

var CommitIndex = "0"

var Commit = "unknown"

var BuildTime = ""

var runGitHubAuthStatus = func(ctx context.Context) error {
	return exec.CommandContext(ctx, "gh", "auth", "status").Run()
}

var commandAvailable = func(name string) bool {
	_, err := exec.LookPath(name)
	return err == nil
}

var inspectGitWorkspace = gitops.InspectWorkspace

var gitHubClient = github.Client{Runner: github.ExecRunner{}}

var currentClock runtimeclock.Clock = runtimeclock.SystemClock{}

func parseCommitIndex(value string) int {
	var index int
	if _, err := fmt.Sscanf(value, "%d", &index); err != nil {
		return 0
	}
	return index
}

func writeJSON(stdout io.Writer, payload map[string]any) int {
	encoded, err := json.Marshal(payload)
	if err != nil {
		fmt.Fprintln(stdout, `{"ok":false,"operation":"internal","code":"json_encode_failed","message":"JSON 编码失败"}`)
		return 1
	}
	fmt.Fprintln(stdout, string(encoded))
	if ok, _ := payload["ok"].(bool); ok {
		return 0
	}
	return 1
}

func readFlag(args []string, name string, fallback string) string {
	for i := 0; i < len(args)-1; i++ {
		if args[i] == name {
			return args[i+1]
		}
	}
	return fallback
}

func positionalArg(args []string, command string) string {
	for i, arg := range args {
		if arg == command && i+1 < len(args) && !strings.HasPrefix(args[i+1], "--") {
			return args[i+1]
		}
	}
	return ""
}

func hasFlag(args []string, name string) bool {
	for _, arg := range args {
		if arg == name {
			return true
		}
	}
	return false
}

func redactSensitive(value string) string {
	keyValuePattern := regexp.MustCompile(`(?i)(token|password|secret|authorization)=([^\s,"}]+)`)
	jsonPattern := regexp.MustCompile(`(?i)("(?:token|password|secret|authorization)"\s*:\s*")([^"]+)(")`)
	redacted := keyValuePattern.ReplaceAllString(value, `${1}=[REDACTED]`)
	redacted = jsonPattern.ReplaceAllString(redacted, `${1}[REDACTED]${3}`)
	return redacted
}

func readInstallDir(args []string) string {
	if installDir := readFlag(args, "--install-dir", ""); installDir != "" {
		return installDir
	}
	if installDir := os.Getenv("AGENTIC_OPS_HOME"); installDir != "" {
		return installDir
	}
	home, _ := os.UserHomeDir()
	return config.DefaultInstallDir(home)
}

func currentUser() string {
	if value := os.Getenv("AGENTIC_OPS_CURRENT_USER"); value != "" {
		return value
	}
	return "current-user"
}

func agentID() string {
	if value := os.Getenv("AGENTIC_OPS_AGENT_ID"); value != "" {
		return value
	}
	return "agentic-cli-local-agent"
}
