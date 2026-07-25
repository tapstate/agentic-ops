package cmdkit

import (
	"fmt"
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
	for end := len(args); end > 0; end-- {
		key := strings.Join(args[:end], " ")
		if spec, ok := registry.commands[key]; ok {
			return spec.Run(ctx, args)
		}
	}
	return unknown(ctx, args)
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
