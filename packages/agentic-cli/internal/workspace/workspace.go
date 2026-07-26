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
	RunLogsDir  string
	FeedbackDir string
}

type DirStatus struct {
	Status  string
	Message string
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
		RunLogsDir:  filepath.Join(base, "run-logs"),
		FeedbackDir: filepath.Join(base, "feedback"),
	}
	if err := os.MkdirAll(info.RunsDir, 0o755); err != nil {
		return Info{}, err
	}
	if err := os.MkdirAll(info.RunLogsDir, 0o755); err != nil {
		return Info{}, err
	}
	if err := os.MkdirAll(info.FeedbackDir, 0o755); err != nil {
		return Info{}, err
	}
	return info, nil
}

func ResolveProjectPath(path string, root string) string {
	resolved := strings.ReplaceAll(path, "<project-ai-workspace>", root)
	return filepath.Clean(resolved)
}

func DirectoryStatus(path string) DirStatus {
	if strings.TrimSpace(path) == "" {
		return DirStatus{Status: "failed", Message: "path is empty"}
	}
	stat, err := os.Stat(path)
	if err == nil {
		if stat.IsDir() {
			return DirStatus{Status: "ok", Message: "exists"}
		}
		return DirStatus{Status: "failed", Message: "path is not a directory"}
	}
	parent := filepath.Dir(path)
	for {
		if stat, statErr := os.Stat(parent); statErr == nil && stat.IsDir() {
			if file, createErr := os.CreateTemp(parent, ".agentic-ops-dir-check-*"); createErr == nil {
				name := file.Name()
				_ = file.Close()
				_ = os.Remove(name)
				return DirStatus{Status: "ok", Message: "creatable"}
			}
			return DirStatus{Status: "failed", Message: "parent is not writable"}
		}
		next := filepath.Dir(parent)
		if next == parent {
			return DirStatus{Status: "failed", Message: err.Error()}
		}
		parent = next
	}
}
