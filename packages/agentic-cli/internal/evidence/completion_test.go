package evidence

import (
	"errors"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

const completeEvidenceBody = `## 变更内容

修复接管原子性和证据链。

## 验证命令与结果

go test ./...：通过。

## 风险

未发现额外风险。

## 恢复说明

无需恢复。

## 事实来源

Jira AO、Git 和 GitHub PR 回读。
`

func TestReadCompletionBodyAcceptsCompleteContent(t *testing.T) {
	root := t.TempDir()
	path := filepath.Join(root, "completion.md")
	if err := os.WriteFile(path, []byte(completeEvidenceBody), 0o600); err != nil {
		t.Fatalf("WriteFile error = %v", err)
	}

	got, err := ReadCompletionBody(root, path, 65536)
	if err != nil {
		t.Fatalf("ReadCompletionBody error = %v", err)
	}
	if got != completeEvidenceBody {
		t.Fatalf("body = %q", got)
	}
}

func TestReadCompletionBodyRejectsMissingOrEmptySections(t *testing.T) {
	tests := []struct {
		name string
		body string
	}{
		{name: "empty", body: ""},
		{name: "missing change", body: strings.Replace(completeEvidenceBody, "## 变更内容\n\n修复接管原子性和证据链。\n\n", "", 1)},
		{name: "missing verification", body: strings.Replace(completeEvidenceBody, "## 验证命令与结果", "## 其它验证", 1)},
		{name: "empty risk", body: strings.Replace(completeEvidenceBody, "## 风险\n\n未发现额外风险。", "## 风险\n", 1)},
		{name: "missing recovery", body: strings.Replace(completeEvidenceBody, "## 恢复说明", "## 其它说明", 1)},
		{name: "missing source", body: strings.Replace(completeEvidenceBody, "## 事实来源", "## 其它来源", 1)},
	}

	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			root := t.TempDir()
			path := filepath.Join(root, "completion.md")
			if err := os.WriteFile(path, []byte(test.body), 0o600); err != nil {
				t.Fatalf("WriteFile error = %v", err)
			}
			_, err := ReadCompletionBody(root, path, 65536)
			if !errors.Is(err, ErrInvalidEvidenceSections) {
				t.Fatalf("error = %v, want %v", err, ErrInvalidEvidenceSections)
			}
		})
	}
}

func TestReadCompletionBodyRejectsOversizedContent(t *testing.T) {
	root := t.TempDir()
	path := filepath.Join(root, "completion.md")
	if err := os.WriteFile(path, []byte(strings.Repeat("甲", 32)), 0o600); err != nil {
		t.Fatalf("WriteFile error = %v", err)
	}

	_, err := ReadCompletionBody(root, path, 32)
	if !errors.Is(err, ErrEvidenceTooLarge) {
		t.Fatalf("error = %v, want %v", err, ErrEvidenceTooLarge)
	}
}

func TestReadCompletionBodyRejectsOutsideWorkspaceAndExternalSymlink(t *testing.T) {
	root := t.TempDir()
	outsideRoot := t.TempDir()
	outside := filepath.Join(outsideRoot, "completion.md")
	if err := os.WriteFile(outside, []byte(completeEvidenceBody), 0o600); err != nil {
		t.Fatalf("WriteFile error = %v", err)
	}

	for _, path := range []string{outside, filepath.Join(root, "external-link.md")} {
		if filepath.Base(path) == "external-link.md" {
			if err := os.Symlink(outside, path); err != nil {
				t.Fatalf("Symlink error = %v", err)
			}
		}
		_, err := ReadCompletionBody(root, path, 65536)
		if !errors.Is(err, ErrOutsideWorkspace) {
			t.Fatalf("ReadCompletionBody(%s) error = %v, want %v", path, err, ErrOutsideWorkspace)
		}
	}
}
