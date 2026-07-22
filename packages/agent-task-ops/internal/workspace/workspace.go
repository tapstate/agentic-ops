package workspace

import (
	"errors"
	"os"
	"path/filepath"
	"strings"
)

type Info struct {
	Name        string
	Root        string
	RunsDir     string
	FeedbackDir string
}

func Ensure(root string, name string) (Info, error) {
	if strings.TrimSpace(name) == "" {
		return Info{}, errors.New("workspace name is required")
	}
	if strings.TrimSpace(root) == "" {
		return Info{}, errors.New("workspace root is required")
	}

	base := filepath.Join(root, ".agentic-ops")
	info := Info{
		Name:        name,
		Root:        root,
		RunsDir:     filepath.Join(base, "runs"),
		FeedbackDir: filepath.Join(base, "feedback"),
	}
	if err := os.MkdirAll(info.RunsDir, 0o755); err != nil {
		return Info{}, err
	}
	if err := os.MkdirAll(info.FeedbackDir, 0o755); err != nil {
		return Info{}, err
	}
	return info, nil
}
