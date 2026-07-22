package main

import (
	"os"

	"github.com/tapstate/agentic-ops/packages/agent-task-ops/internal/cli"
)

func main() {
	os.Exit(cli.Run(os.Args[1:], os.Stdout, os.Stderr))
}
