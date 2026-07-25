package cmdkit

import "io"

type Context struct {
	Stdout io.Writer
	Stderr io.Writer
}

type Handler func(ctx Context, args []string) int

type CommandSpec struct {
	Path []string
	Run  Handler
}
