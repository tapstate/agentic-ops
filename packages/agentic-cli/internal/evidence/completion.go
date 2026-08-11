package evidence

import (
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"strings"
)

var ErrOutsideWorkspace = errors.New("evidence_content_outside_workspace")

var ErrEvidenceTooLarge = errors.New("evidence_content_too_large")

var ErrInvalidEvidenceSections = errors.New("invalid_evidence_sections")

var requiredCompletionSections = []string{
	"## 变更内容",
	"## 验证命令与结果",
	"## 风险",
	"## 恢复说明",
	"## 事实来源",
}

func ReadCompletionBody(workspaceRoot string, path string, maxBytes int64) (string, error) {
	resolvedRoot, err := resolvePath(workspaceRoot)
	if err != nil {
		return "", err
	}
	resolvedPath, err := resolvePath(path)
	if err != nil {
		return "", err
	}
	if !pathWithinRoot(resolvedRoot, resolvedPath) {
		return "", fmt.Errorf("%w: %s", ErrOutsideWorkspace, path)
	}
	info, err := os.Stat(resolvedPath)
	if err != nil {
		return "", err
	}
	if !info.Mode().IsRegular() {
		return "", fmt.Errorf("evidence content is not a regular file: %s", path)
	}
	file, err := os.Open(resolvedPath)
	if err != nil {
		return "", err
	}
	defer file.Close()

	content, err := io.ReadAll(io.LimitReader(file, maxBytes+1))
	if err != nil {
		return "", err
	}
	if int64(len(content)) > maxBytes {
		return "", fmt.Errorf("%w: maximum %d bytes", ErrEvidenceTooLarge, maxBytes)
	}
	body := string(content)
	if err := validateCompletionSections(body); err != nil {
		return "", err
	}
	return body, nil
}

func resolvePath(path string) (string, error) {
	absolute, err := filepath.Abs(path)
	if err != nil {
		return "", err
	}
	return filepath.EvalSymlinks(absolute)
}

func pathWithinRoot(root string, path string) bool {
	relative, err := filepath.Rel(root, path)
	if err != nil {
		return false
	}
	return relative != ".." && !strings.HasPrefix(relative, ".."+string(filepath.Separator))
}

func validateCompletionSections(body string) error {
	lines := strings.Split(strings.ReplaceAll(body, "\r\n", "\n"), "\n")
	positions := make([]int, len(requiredCompletionSections))
	next := 0
	for lineIndex, line := range lines {
		trimmed := strings.TrimSpace(line)
		for sectionIndex, section := range requiredCompletionSections {
			if trimmed != section {
				continue
			}
			if sectionIndex != next {
				return fmt.Errorf("%w: section %q is out of order", ErrInvalidEvidenceSections, section)
			}
			positions[next] = lineIndex
			next++
			break
		}
	}
	if next != len(requiredCompletionSections) {
		return fmt.Errorf("%w: all required sections must be present", ErrInvalidEvidenceSections)
	}
	for index, start := range positions {
		end := len(lines)
		if index+1 < len(positions) {
			end = positions[index+1]
		}
		if strings.TrimSpace(strings.Join(lines[start+1:end], "\n")) == "" {
			return fmt.Errorf("%w: section %q is empty", ErrInvalidEvidenceSections, requiredCompletionSections[index])
		}
	}
	return nil
}
