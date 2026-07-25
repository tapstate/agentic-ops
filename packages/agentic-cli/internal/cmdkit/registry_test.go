package cmdkit

import (
	"bytes"
	"testing"
)

func TestRegistryDispatchesSingleSegmentCommand(t *testing.T) {
	registry := NewRegistry()
	registry.MustRegister(CommandSpec{
		Path: []string{"doctor"},
		Run: func(ctx Context, args []string) int {
			ctx.Stdout.Write([]byte(args[0]))
			return 0
		},
	})
	var stdout bytes.Buffer
	code := registry.Dispatch(Context{Stdout: &stdout}, []string{"doctor"}, failUnknown)
	if code != 0 {
		t.Fatalf("code = %d, want 0", code)
	}
	if stdout.String() != "doctor" {
		t.Fatalf("stdout = %q, want doctor", stdout.String())
	}
}

func TestRegistryDispatchesLongestPath(t *testing.T) {
	registry := NewRegistry()
	registry.MustRegister(CommandSpec{Path: []string{"profile"}, Run: writeCommand("parent")})
	registry.MustRegister(CommandSpec{Path: []string{"profile", "update"}, Run: writeCommand("child")})
	var stdout bytes.Buffer
	code := registry.Dispatch(Context{Stdout: &stdout}, []string{"profile", "update", "--source", "profile.yaml"}, failUnknown)
	if code != 0 {
		t.Fatalf("code = %d, want 0", code)
	}
	if stdout.String() != "child" {
		t.Fatalf("stdout = %q, want child", stdout.String())
	}
}

func TestRegistryUsesUnknownHandler(t *testing.T) {
	registry := NewRegistry()
	var stdout bytes.Buffer
	code := registry.Dispatch(Context{Stdout: &stdout}, []string{"missing"}, func(ctx Context, args []string) int {
		ctx.Stdout.Write([]byte(args[0]))
		return 7
	})
	if code != 7 {
		t.Fatalf("code = %d, want 7", code)
	}
	if stdout.String() != "missing" {
		t.Fatalf("stdout = %q, want missing", stdout.String())
	}
}

func TestRegistryRejectsDuplicateCommand(t *testing.T) {
	registry := NewRegistry()
	if err := registry.Register(CommandSpec{Path: []string{"doctor"}, Run: writeCommand("first")}); err != nil {
		t.Fatalf("Register first error = %v", err)
	}
	if err := registry.Register(CommandSpec{Path: []string{"doctor"}, Run: writeCommand("second")}); err == nil {
		t.Fatalf("Register duplicate error = nil, want error")
	}
}

func writeCommand(value string) Handler {
	return func(ctx Context, args []string) int {
		ctx.Stdout.Write([]byte(value))
		return 0
	}
}

func failUnknown(ctx Context, args []string) int {
	return 1
}
