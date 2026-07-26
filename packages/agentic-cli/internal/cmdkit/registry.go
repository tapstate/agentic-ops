package cmdkit

import (
	"fmt"
	"io"
	"sort"
	"strings"
)

type Registry struct {
	commands map[string]CommandSpec
}

func NewRegistry() *Registry {
	return &Registry{commands: map[string]CommandSpec{}}
}

func (registry *Registry) MustRegister(spec CommandSpec) {
	if err := registry.Register(spec); err != nil {
		panic(err)
	}
}

func (registry *Registry) Register(spec CommandSpec) error {
	key, err := commandKey(spec.Path)
	if err != nil {
		return err
	}
	if spec.Run == nil {
		return fmt.Errorf("command %s missing handler", key)
	}
	if _, exists := registry.commands[key]; exists {
		return fmt.Errorf("command %s already registered", key)
	}
	registry.commands[key] = spec
	return nil
}

func (registry *Registry) Dispatch(ctx Context, args []string, unknown Handler) int {
	if len(args) == 0 {
		return unknown(ctx, args)
	}
	if helpRequested(args) {
		cleanArgs := removeHelpArgs(args)
		if len(cleanArgs) == 0 {
			registry.WriteRootHelp(ctx.Stdout)
			return 0
		}
		if spec, ok := registry.Match(cleanArgs); ok {
			WriteCommandHelp(ctx.Stdout, spec)
			return 0
		}
	}
	for end := len(args); end > 0; end-- {
		key := strings.Join(args[:end], " ")
		if spec, ok := registry.commands[key]; ok {
			return spec.Run(ctx, args)
		}
	}
	return unknown(ctx, args)
}

func (registry *Registry) Match(args []string) (CommandSpec, bool) {
	for end := len(args); end > 0; end-- {
		key := strings.Join(args[:end], " ")
		if spec, ok := registry.commands[key]; ok {
			return spec, true
		}
	}
	return CommandSpec{}, false
}

func (registry *Registry) Specs() []CommandSpec {
	specs := make([]CommandSpec, 0, len(registry.commands))
	for _, spec := range registry.commands {
		specs = append(specs, spec)
	}
	sort.Slice(specs, func(i, j int) bool {
		return strings.Join(specs[i].Path, " ") < strings.Join(specs[j].Path, " ")
	})
	return specs
}

func (registry *Registry) WriteRootHelp(stdout io.Writer) {
	fmt.Fprintln(stdout, "Usage: agentic-cli <command> [args]")
	fmt.Fprintln(stdout)
	fmt.Fprintln(stdout, "Commands:")
	for _, spec := range registry.Specs() {
		if len(spec.Path) == 0 {
			continue
		}
		name := strings.Join(spec.Path, " ")
		if spec.Summary == "" {
			fmt.Fprintf(stdout, "  %s\n", name)
			continue
		}
		fmt.Fprintf(stdout, "  %-28s %s\n", name, spec.Summary)
	}
	fmt.Fprintln(stdout)
	fmt.Fprintln(stdout, "Use `agentic-cli <command> -h` for command help.")
}

func WriteCommandHelp(stdout io.Writer, spec CommandSpec) {
	usage := spec.Usage
	if usage == "" {
		usage = "agentic-cli " + strings.Join(spec.Path, " ")
	}
	fmt.Fprintf(stdout, "Usage: %s\n", usage)
	if spec.Summary != "" {
		fmt.Fprintln(stdout)
		fmt.Fprintln(stdout, spec.Summary)
	}
	if len(spec.Examples) > 0 {
		fmt.Fprintln(stdout)
		fmt.Fprintln(stdout, "Examples:")
		for _, example := range spec.Examples {
			fmt.Fprintf(stdout, "  %s\n", example)
		}
	}
	if spec.Risk != "" || spec.Contract != "" || spec.HumanGate {
		fmt.Fprintln(stdout)
		fmt.Fprintln(stdout, "Metadata:")
		if spec.Risk != "" {
			fmt.Fprintf(stdout, "  risk: %s\n", spec.Risk)
		}
		if spec.Contract != "" {
			fmt.Fprintf(stdout, "  contract: %s\n", spec.Contract)
		}
		if spec.HumanGate {
			fmt.Fprintln(stdout, "  human_gate: true")
		}
	}
}

func helpRequested(args []string) bool {
	for _, arg := range args {
		if arg == "-h" || arg == "--help" || arg == "help" {
			return true
		}
	}
	return false
}

func removeHelpArgs(args []string) []string {
	clean := make([]string, 0, len(args))
	for _, arg := range args {
		if arg == "-h" || arg == "--help" || arg == "help" {
			continue
		}
		clean = append(clean, arg)
	}
	return clean
}

func commandKey(path []string) (string, error) {
	if len(path) == 0 {
		return "", fmt.Errorf("command path is empty")
	}
	clean := make([]string, 0, len(path))
	for _, part := range path {
		part = strings.TrimSpace(part)
		if part == "" {
			return "", fmt.Errorf("command path contains empty segment")
		}
		clean = append(clean, part)
	}
	return strings.Join(clean, " "), nil
}
