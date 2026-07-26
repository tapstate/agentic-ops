package cli

import (
	"encoding/json"
	"fmt"
	"io"
	"os"

	"github.com/tapstate/agentic-ops/packages/agentic-cli/internal/clihandlers"
	"github.com/tapstate/agentic-ops/packages/agentic-cli/internal/cmdkit"
	"github.com/tapstate/agentic-ops/packages/agentic-cli/internal/commandcatalog"
	"github.com/tapstate/agentic-ops/packages/agentic-cli/internal/output"
)

var Version = "SRC-source"
var VersionState = "SRC"
var IterationVersion = "source"
var CommitIndex = "0"
var Commit = "unknown"
var BuildTime = ""

func Run(args []string, stdout io.Writer, stderr io.Writer) int {
	return RunWithIO(args, os.Stdin, stdout, stderr, stdinIsTerminal())
}

func RunWithIO(args []string, stdin io.Reader, stdout io.Writer, stderr io.Writer, interactive bool) int {
	syncHandlerVersionInfo()

	if len(args) == 0 {
		return writeJSON(stdout, output.Failure("unknown", "missing_command", "缺少命令", "请提供命令"))
	}
	registry := cmdkit.NewRegistry()
	commandcatalog.RegisterAll(registry)
	if args[0] == "-h" || args[0] == "--help" || args[0] == "help" {
		registry.WriteRootHelp(stdout)
		return 0
	}
	if args[0] == "--version" || args[0] == "version" {
		return writeJSON(stdout, output.Success("version", map[string]any{
			"version":           Version,
			"version_state":     VersionState,
			"iteration_version": IterationVersion,
			"commit_index":      parseCommitIndex(CommitIndex),
			"commit":            Commit,
			"build_time":        BuildTime,
		}))
	}

	return registry.Dispatch(cmdkit.Context{Stdin: stdin, Stdout: stdout, Stderr: stderr, Interactive: interactive}, args, unknownCommand)
}

func stdinIsTerminal() bool {
	stat, err := os.Stdin.Stat()
	if err != nil {
		return false
	}
	return (stat.Mode() & os.ModeCharDevice) != 0
}

func syncHandlerVersionInfo() {
	clihandlers.SetVersionInfo(Version, VersionState, IterationVersion, CommitIndex, Commit, BuildTime)
}

func unknownCommand(ctx cmdkit.Context, args []string) int {
	command := "unknown"
	if len(args) > 0 {
		command = args[0]
		fmt.Fprintf(ctx.Stderr, "unknown command: %s\n", args[0])
	}
	return writeJSON(ctx.Stdout, output.Failure(command, "unknown_command", "未知命令", "请检查命令名称，或运行 agentic-cli -h 查看可用命令"))
}

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
