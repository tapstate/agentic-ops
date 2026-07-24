package git

import (
	"context"
	"fmt"
	"os/exec"
	"strings"
)

type WorkspaceStatus struct {
	Root         string   `json:"root"`
	Branch       string   `json:"branch"`
	Commit       string   `json:"commit"`
	Dirty        bool     `json:"dirty"`
	ChangedFiles []string `json:"changed_files"`
}

func InspectWorkspace(ctx context.Context, root string) (WorkspaceStatus, error) {
	branch, err := runGit(ctx, root, "rev-parse", "--abbrev-ref", "HEAD")
	if err != nil {
		return WorkspaceStatus{}, err
	}
	commit, err := runGit(ctx, root, "rev-parse", "HEAD")
	if err != nil {
		commit = ""
	}
	porcelain, err := runGit(ctx, root, "status", "--porcelain=v1")
	if err != nil {
		return WorkspaceStatus{}, err
	}
	changedFiles := parseChangedFiles(porcelain)
	return WorkspaceStatus{
		Root:         root,
		Branch:       strings.TrimSpace(branch),
		Commit:       strings.TrimSpace(commit),
		Dirty:        len(changedFiles) > 0,
		ChangedFiles: changedFiles,
	}, nil
}

func runGit(ctx context.Context, root string, args ...string) (string, error) {
	cmd := exec.CommandContext(ctx, "git", append([]string{"-C", root}, args...)...)
	output, err := cmd.CombinedOutput()
	if err != nil {
		return "", fmt.Errorf("git %s failed: %w: %s", strings.Join(args, " "), err, strings.TrimSpace(string(output)))
	}
	return string(output), nil
}

func parseChangedFiles(porcelain string) []string {
	var files []string
	seen := map[string]bool{}
	for _, line := range strings.Split(porcelain, "\n") {
		if len(line) < 4 {
			continue
		}
		path := strings.TrimSpace(line[3:])
		if renameIndex := strings.LastIndex(path, " -> "); renameIndex >= 0 {
			path = path[renameIndex+4:]
		}
		path = strings.Trim(path, `"`)
		if path == "" || seen[path] {
			continue
		}
		seen[path] = true
		files = append(files, path)
	}
	return files
}
