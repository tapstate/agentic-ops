package cmdkit

import "io"

type Context struct {
	Stdout io.Writer
	Stderr io.Writer
}

type Handler func(ctx Context, args []string) int

type CommandSpec struct {
	Path      []string
	Run       Handler
	Summary   string
	Usage     string
	Examples  []string
	Risk      string
	HumanGate bool
	Contract  string
}
