package evidence

import (
	"os"
	"strings"
	"testing"
)

func TestWriteCreatesEvidenceFile(t *testing.T) {
	path := t.TempDir() + "/runs/run-1/evidence.md"
	err := Write(path, "## 任务接管成功\n")
	if err != nil {
		t.Fatalf("Write error = %v", err)
	}
	data, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("ReadFile error = %v", err)
	}
	if !strings.Contains(string(data), "任务接管成功") {
		t.Fatalf("content = %s", string(data))
	}
}
