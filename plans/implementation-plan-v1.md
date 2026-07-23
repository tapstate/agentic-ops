# AgenticOps 第一阶段实施计划

> **状态：** 历史计划 / 已完成本地基线。本计划记录第一阶段本地模拟流程的约束和验收口径，不再限制当前设计或后续实现范围；当前推进状态、剩余工作和阶段性限制应维护在仍处于活跃状态的计划文件中。

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 构建 AgenticOps 第一阶段最小可运行闭环，让研发负责人可以安装 Go CLI、初始化工作空间、初始化 AIAgent 能力、用 fake Jira 数据接管任务、写入 evidence，并生成每日反馈报告。

**Architecture:** 第一阶段采用本地优先的 Go CLI 运行时，shell 只做 `curl | bash` 安装引导。Go CLI 以操作契约为操作边界，先接模拟 Jira 适配器跑通本地闭环，再接真实 Jira / GitHub。机器可读操作契约的源头是仓库顶层 `contracts/operations/`，Go package 不维护第二份契约源头。

**Tech Stack:** Go 1.22+、标准库优先、`gopkg.in/yaml.v3` 用于 YAML、`gh` 作为 GitHub 登录状态检查、Jira 第一阶段先 fake adapter。

## Global Constraints

- 当前阶段先执行本计划，不直接扩展到 Web 控制台、后台常驻进程、自动创建拉取请求或完整自更新。
- CLI 统一入口为 `agentic-cli`。
- Go 是主实现语言；shell 只用于安装引导、轻量环境检测、下载或切换 Go release 二进制。
- `agentic-cli` 运行时不得依赖本地 Python、`jq` 或 shell 业务脚本。
- stdout 只输出结构化 JSON；stderr 输出人类诊断日志。
- 所有失败必须返回稳定 `code`。
- secrets 不允许出现在 stdout、stderr 或事件日志中。
- 支持 Linux (linux-amd64 / linux-arm64)、macOS Intel (darwin-amd64) 和 macOS Apple Silicon (darwin-arm64)。
- `~/.agentic-ops` 是全局安装和配置目录，不是具体项目或具体任务运行目录。
- 项目运行目录是项目 AI 工作空间，例如 `tapstate` 或 `tapdata`。
- AIAgent 不按固定角色工作，必须按 `task_type`、`current_stage`、`next_action` 推进。
- AIAgent 执行 Jira 任务前必须先识别 `task_class`，再选择 Standard Process Registry 中的 `process_id`。
- `agent_id` 是 AIAgent 唯一编号；`current_agent_id` 是任务运行中绑定字段，任务完成或交接结束后必须清理。
- 第一阶段操作名称集合以 `docs/contracts/operation-contract.md` 为文档权威。

---

## 1. 目标文件结构

```text
agentic-ops/
  go.mod
  contracts/
    operations/
      install.yaml
      workspace-init.yaml
      agent-init.yaml
      list-tasks.yaml
      takeover-task.yaml
      resume-takeover.yaml
      write-evidence.yaml
      feedback-report.yaml
  packages/
    agentic-cli/
      cmd/
        agentic-cli/
          main.go
      internal/
        cli/
          app.go
          app_test.go
        command/
          agent_init.go
          feedback_report.go
          list_tasks.go
          preflight.go
          takeover_task.go
          workspace_init.go
          write_evidence.go
        config/
          paths.go
          paths_test.go
        contract/
          loader.go
          loader_test.go
          model.go
        evidence/
          writer.go
          writer_test.go
        feedback/
          event.go
          report.go
          report_test.go
        jira/
          fake.go
          model.go
        output/
          json.go
          json_test.go
        policy/
          gate.go
          gate_test.go
        workspace/
          workspace.go
          workspace_test.go
      testdata/
        workspace/
        jira/
  scripts/
    init.sh
```

## 2. 第一阶段命令范围

先实现这些命令：

```text
agentic-cli --version
agentic-cli preflight --workspace <name>
agentic-cli workspace init --workspace <name>
agentic-cli agent init --workspace <name>
agentic-cli list-tasks --workspace <name>
agentic-cli takeover-task <issue-key> --workspace <name>
agentic-cli write-evidence --run-id <run_id> --workspace <name>
agentic-cli feedback report --workspace <name> --date <yyyy-mm-dd>
```

暂不实现这些命令：

```text
agentic-cli prepare-pr
agentic-cli fix-pr-comments
agentic-cli feedback collect
agentic-cli feedback analyze
agentic-cli feedback propose
agentic-cli self-update
```

这些命令保留在契约文档中，但不进入第一批可运行闭环。

---

### Task 1: Go CLI 骨架和 JSON 输出

**Files:**
- Create: `go.mod`
- Create: `packages/agentic-cli/cmd/agentic-cli/main.go`
- Create: `packages/agentic-cli/internal/cli/app.go`
- Create: `packages/agentic-cli/internal/cli/app_test.go`
- Create: `packages/agentic-cli/internal/output/json.go`
- Create: `packages/agentic-cli/internal/output/json_test.go`

**Interfaces:**
- Produces: `cli.Run(args []string, stdout io.Writer, stderr io.Writer) int`
- Produces: `output.Success(operation string, payload map[string]any) map[string]any`
- Produces: `output.Failure(operation string, code string, message string, requiredHumanAction string) map[string]any`

- [x] **Step 1: Create Go module**

```go
module github.com/tapstate/agentic-ops

go 1.22
```

Run: `go test ./...`
Expected: command succeeds with `go: warning: "./..." matched no packages` or no package output before code files are added.

- [x] **Step 2: Add JSON output helpers**

Create `packages/agentic-cli/internal/output/json.go`:

```go
package output

func Success(operation string, payload map[string]any) map[string]any {
	result := map[string]any{
		"ok":        true,
		"operation": operation,
	}
	for key, value := range payload {
		result[key] = value
	}
	return result
}

func Failure(operation string, code string, message string, requiredHumanAction string) map[string]any {
	result := map[string]any{
		"ok":        false,
		"operation": operation,
		"code":      code,
		"message":   message,
	}
	if requiredHumanAction != "" {
		result["required_human_action"] = requiredHumanAction
	}
	return result
}
```

Create `packages/agentic-cli/internal/output/json_test.go`:

```go
package output

import "testing"

func TestSuccessIncludesOperationAndPayload(t *testing.T) {
	got := Success("agent_init", map[string]any{"workspace": "tapstate"})
	if got["ok"] != true {
		t.Fatalf("ok = %v, want true", got["ok"])
	}
	if got["operation"] != "agent_init" {
		t.Fatalf("operation = %v", got["operation"])
	}
	if got["workspace"] != "tapstate" {
		t.Fatalf("workspace = %v", got["workspace"])
	}
}

func TestFailureIncludesStableCode(t *testing.T) {
	got := Failure("takeover_task", "missing_target_repo", "缺少目标仓库", "请补充 target_repo")
	if got["ok"] != false {
		t.Fatalf("ok = %v, want false", got["ok"])
	}
	if got["code"] != "missing_target_repo" {
		t.Fatalf("code = %v", got["code"])
	}
	if got["required_human_action"] != "请补充 target_repo" {
		t.Fatalf("required_human_action = %v", got["required_human_action"])
	}
}
```

Run: `go test ./packages/agentic-cli/internal/output`
Expected: PASS.

- [x] **Step 3: Add CLI app entry**

Create `packages/agentic-cli/internal/cli/app.go`:

```go
package cli

import (
	"encoding/json"
	"fmt"
	"io"

	"github.com/tapstate/agentic-ops/packages/agentic-cli/internal/output"
)

var Version = "source"
var VersionState = "SRC"
var Commit = "unknown"
var BuildTime = ""

func Run(args []string, stdout io.Writer, stderr io.Writer) int {
	if len(args) == 0 {
		return writeJSON(stdout, output.Failure("unknown", "missing_command", "缺少命令", "请提供命令"))
	}

	switch args[0] {
	case "--version", "version":
		return writeJSON(stdout, output.Success("version", map[string]any{"version": Version}))
	default:
		fmt.Fprintf(stderr, "unknown command: %s\n", args[0])
		return writeJSON(stdout, output.Failure(args[0], "unknown_command", "未知命令", "请检查命令名称"))
	}
}

func writeJSON(stdout io.Writer, payload map[string]any) int {
	encoded, err := json.Marshal(payload)
	if err != nil {
		fmt.Fprintln(stdout, `{"ok":false,"operation":"internal","code":"json_encode_failed","message":"JSON 编码失败"}`)
		return 1
	}
	fmt.Fprintln(stdout, string(encoded))
	if ok, _ := payload["ok"].(bool); ok {
		return 0
	}
	return 1
}
```

Create `packages/agentic-cli/internal/cli/app_test.go`:

```go
package cli

import (
	"bytes"
	"strings"
	"testing"
)

func TestVersionOutputsJSON(t *testing.T) {
	var stdout bytes.Buffer
	var stderr bytes.Buffer
	code := Run([]string{"--version"}, &stdout, &stderr)
	if code != 0 {
		t.Fatalf("code = %d, want 0", code)
	}
	if !strings.Contains(stdout.String(), `"operation":"version"`) {
		t.Fatalf("stdout = %s", stdout.String())
	}
	if stderr.String() != "" {
		t.Fatalf("stderr = %s", stderr.String())
	}
}

func TestUnknownCommandFailsWithStableCode(t *testing.T) {
	var stdout bytes.Buffer
	var stderr bytes.Buffer
	code := Run([]string{"missing"}, &stdout, &stderr)
	if code != 1 {
		t.Fatalf("code = %d, want 1", code)
	}
	if !strings.Contains(stdout.String(), `"code":"unknown_command"`) {
		t.Fatalf("stdout = %s", stdout.String())
	}
	if !strings.Contains(stderr.String(), "unknown command: missing") {
		t.Fatalf("stderr = %s", stderr.String())
	}
}
```

Create `packages/agentic-cli/cmd/agentic-cli/main.go`:

```go
package main

import (
	"os"

	"github.com/tapstate/agentic-ops/packages/agentic-cli/internal/cli"
)

func main() {
	os.Exit(cli.Run(os.Args[1:], os.Stdout, os.Stderr))
}
```

Run: `go test ./packages/agentic-cli/internal/...`
Expected: PASS.

- [x] **Step 4: Commit**

```bash
git add go.mod packages/agentic-cli
git commit -m "Feat(cli): add Go CLI skeleton"
```

### Task 2: 全局路径和工作空间目录

**Files:**
- Create: `packages/agentic-cli/internal/config/paths.go`
- Create: `packages/agentic-cli/internal/config/paths_test.go`
- Create: `packages/agentic-cli/internal/workspace/workspace.go`
- Create: `packages/agentic-cli/internal/workspace/workspace_test.go`
- Modify: `packages/agentic-cli/internal/cli/app.go`
- Modify: `packages/agentic-cli/internal/cli/app_test.go`

**Interfaces:**
- Consumes: `cli.Run(args []string, stdout io.Writer, stderr io.Writer) int`
- Produces: `config.DefaultInstallDir(home string) string`
- Produces: `workspace.Ensure(root string, name string) (Info, error)`
- Produces: `workspace.Info{Name, Root, RunsDir, FeedbackDir string}`

- [x] **Step 1: Add path helpers**

Create `packages/agentic-cli/internal/config/paths.go`:

```go
package config

import "path/filepath"

func DefaultInstallDir(home string) string {
	return filepath.Join(home, ".agentic-ops")
}
```

Create `packages/agentic-cli/internal/config/paths_test.go`:

```go
package config

import "testing"

func TestDefaultInstallDir(t *testing.T) {
	got := DefaultInstallDir("/home/dev")
	if got != "/home/dev/.agentic-ops" {
		t.Fatalf("got %q", got)
	}
}
```

Run: `go test ./packages/agentic-cli/internal/config`
Expected: PASS.

- [x] **Step 2: Add workspace creation**

Create `packages/agentic-cli/internal/workspace/workspace.go`:

```go
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
```

Create `packages/agentic-cli/internal/workspace/workspace_test.go`:

```go
package workspace

import (
	"os"
	"path/filepath"
	"testing"
)

func TestEnsureCreatesWorkspaceDirs(t *testing.T) {
	root := t.TempDir()
	info, err := Ensure(root, "tapstate")
	if err != nil {
		t.Fatalf("Ensure error = %v", err)
	}
	if info.Name != "tapstate" {
		t.Fatalf("Name = %s", info.Name)
	}
	for _, dir := range []string{info.RunsDir, info.FeedbackDir} {
		stat, err := os.Stat(dir)
		if err != nil {
			t.Fatalf("missing dir %s: %v", dir, err)
		}
		if !stat.IsDir() {
			t.Fatalf("%s is not dir", dir)
		}
	}
	if filepath.Base(filepath.Dir(info.RunsDir)) != ".agentic-ops" {
		t.Fatalf("RunsDir = %s", info.RunsDir)
	}
}
```

Run: `go test ./packages/agentic-cli/internal/workspace`
Expected: PASS.

- [x] **Step 3: Add `workspace init` command route**

Modify `packages/agentic-cli/internal/cli/app.go` to route:

```go
case "workspace":
	if len(args) >= 2 && args[1] == "init" {
		return writeJSON(stdout, output.Success("workspace_init", map[string]any{
			"workspace":   readFlag(args, "--workspace", "default"),
			"profile":     readFlag(args, "--workspace", "default"),
			"runs_dir":    "<project-ai-workspace>/.agentic-ops/runs",
			"next_action": "init_agent_capability",
		}))
	}
```

Add helper in same file:

```go
func readFlag(args []string, name string, fallback string) string {
	for i := 0; i < len(args)-1; i++ {
		if args[i] == name {
			return args[i+1]
		}
	}
	return fallback
}
```

Add test:

```go
func TestWorkspaceInitOutputsNextAction(t *testing.T) {
	var stdout bytes.Buffer
	var stderr bytes.Buffer
	code := Run([]string{"workspace", "init", "--workspace", "tapstate"}, &stdout, &stderr)
	if code != 0 {
		t.Fatalf("code = %d", code)
	}
	if !strings.Contains(stdout.String(), `"operation":"workspace_init"`) {
		t.Fatalf("stdout = %s", stdout.String())
	}
	if !strings.Contains(stdout.String(), `"next_action":"init_agent_capability"`) {
		t.Fatalf("stdout = %s", stdout.String())
	}
}
```

Run: `go test ./packages/agentic-cli/internal/...`
Expected: PASS.

- [x] **Step 4: Commit**

```bash
git add packages/agentic-cli/internal/config packages/agentic-cli/internal/workspace packages/agentic-cli/internal/cli
git commit -m "Feat(workspace): add workspace initialization model"
```

### Task 3: Operation Contract文件和读取器

**Files:**
- Create: `contracts/operations/takeover-task.yaml`
- Create: `contracts/operations/list-tasks.yaml`
- Create: `contracts/operations/write-evidence.yaml`
- Create: `packages/agentic-cli/internal/contract/model.go`
- Create: `packages/agentic-cli/internal/contract/loader.go`
- Create: `packages/agentic-cli/internal/contract/loader_test.go`
- Modify: `go.mod`

**Interfaces:**
- Produces: `contract.Operation`
- Produces: `contract.LoadFile(path string) (Operation, error)`

- [x] **Step 1: Add YAML dependency**

Run: `go get gopkg.in/yaml.v3`
Expected: `go.mod` and `go.sum` update with `gopkg.in/yaml.v3`.

- [x] **Step 2: Add operation YAML files**

Create `contracts/operations/takeover-task.yaml`:

```yaml
operation: takeover_task
version: 1
purpose: 研发负责人授权 AIAgent 接管一个已进入迭代的任务。
task_type: task_takeover
allowed_stages:
  - waiting_takeover
  - takeover_gate
required_inputs:
  - issue_key
  - workspace
side_effects:
  - may_write_jira_comment
  - may_create_takeover_record
  - must_not_modify_code
  - must_not_create_pr
human_gate:
  required: false
```

Create `contracts/operations/list-tasks.yaml`:

```yaml
operation: list_tasks
version: 1
purpose: 列出当前负责人可处理任务。
task_type: task_listing
allowed_stages:
  - initialized
required_inputs:
  - workspace
side_effects:
  - must_not_write_jira
  - must_not_modify_code
human_gate:
  required: false
```

Create `contracts/operations/write-evidence.yaml`:

```yaml
operation: write_evidence
version: 1
purpose: 写入 Jira / 拉取请求证据。
task_type: evidence_write
allowed_stages:
  - takeover_started
  - development_completed
  - blocked
required_inputs:
  - workspace
  - run_id
side_effects:
  - may_write_jira_comment
  - may_write_local_event
human_gate:
  required: false
```

- [x] **Step 3: Add contract model and loader**

Create `packages/agentic-cli/internal/contract/model.go`:

```go
package contract

type Operation struct {
	Operation      string    `yaml:"operation"`
	Version        int       `yaml:"version"`
	Purpose        string    `yaml:"purpose"`
	TaskType       string    `yaml:"task_type"`
	AllowedStages  []string  `yaml:"allowed_stages"`
	RequiredInputs []string  `yaml:"required_inputs"`
	SideEffects    []string  `yaml:"side_effects"`
	HumanGate       HumanGate `yaml:"human_gate"`
}

type HumanGate struct {
	Required bool `yaml:"required"`
}
```

Create `packages/agentic-cli/internal/contract/loader.go`:

```go
package contract

import (
	"os"

	"gopkg.in/yaml.v3"
)

func LoadFile(path string) (Operation, error) {
	data, err := os.ReadFile(path)
	if err != nil {
		return Operation{}, err
	}
	var op Operation
	if err := yaml.Unmarshal(data, &op); err != nil {
		return Operation{}, err
	}
	return op, nil
}
```

Create `packages/agentic-cli/internal/contract/loader_test.go`:

```go
package contract

import (
	"path/filepath"
	"testing"
)

func TestLoadFileReadsOperationContract(t *testing.T) {
	path := filepath.Join("..", "..", "..", "..", "contracts", "operations", "takeover-task.yaml")
	op, err := LoadFile(path)
	if err != nil {
		t.Fatalf("LoadFile error = %v", err)
	}
	if op.Operation != "takeover_task" {
		t.Fatalf("Operation = %s", op.Operation)
	}
	if op.TaskType != "task_takeover" {
		t.Fatalf("TaskType = %s", op.TaskType)
	}
	if len(op.AllowedStages) == 0 {
		t.Fatal("AllowedStages is empty")
	}
}
```

Run: `go test ./packages/agentic-cli/internal/contract`
Expected: PASS.

- [x] **Step 4: Commit**

```bash
git add go.mod go.sum contracts/operations packages/agentic-cli/internal/contract
git commit -m "Feat(contract): add Operation Contract loader"
```

### Task 4: Fake Jira adapter 和任务列表

**Files:**
- Create: `packages/agentic-cli/internal/jira/model.go`
- Create: `packages/agentic-cli/internal/jira/fake.go`
- Modify: `packages/agentic-cli/internal/cli/app.go`
- Modify: `packages/agentic-cli/internal/cli/app_test.go`

**Interfaces:**
- Produces: `jira.Issue`
- Produces: `jira.FakeClient.ListTasks(workspace string) []Issue`
- Consumes: `output.Success`

- [x] **Step 1: Add fake Jira model**

Create `packages/agentic-cli/internal/jira/model.go`:

```go
package jira

type Issue struct {
	Key                string `json:"key"`
	Summary            string `json:"summary"`
	Owner              string `json:"owner"`
	TargetRepo         string `json:"target_repo"`
	AcceptanceCriteria string `json:"acceptance_criteria"`
	VerificationMethod string `json:"verification_method"`
}
```

Create `packages/agentic-cli/internal/jira/fake.go`:

```go
package jira

type FakeClient struct{}

func (FakeClient) ListTasks(workspace string) []Issue {
	return []Issue{
		{
			Key:                "TAP-123",
			Summary:            "修复示例任务",
			Owner:              "current-user",
			TargetRepo:         "tapstate/example-repo",
			AcceptanceCriteria: "单元测试通过",
			VerificationMethod: "go test ./...",
		},
	}
}

func (FakeClient) GetIssue(key string) (Issue, bool) {
	for _, issue := range (FakeClient{}).ListTasks("tapstate") {
		if issue.Key == key {
			return issue, true
		}
	}
	return Issue{}, false
}
```

- [x] **Step 2: Add `list-tasks` command**

Modify `packages/agentic-cli/internal/cli/app.go` imports to include fake jira package:

```go
	"github.com/tapstate/agentic-ops/packages/agentic-cli/internal/jira"
```

Add route:

```go
case "list-tasks":
	workspaceName := readFlag(args, "--workspace", "default")
	issues := jira.FakeClient{}.ListTasks(workspaceName)
	return writeJSON(stdout, output.Success("list_tasks", map[string]any{
		"workspace":   workspaceName,
		"tasks":       issues,
		"next_action": "takeover_task",
	}))
```

Add test:

```go
func TestListTasksUsesFakeJira(t *testing.T) {
	var stdout bytes.Buffer
	var stderr bytes.Buffer
	code := Run([]string{"list-tasks", "--workspace", "tapstate"}, &stdout, &stderr)
	if code != 0 {
		t.Fatalf("code = %d", code)
	}
	for _, want := range []string{`"operation":"list_tasks"`, `"workspace":"tapstate"`, `"key":"TAP-123"`} {
		if !strings.Contains(stdout.String(), want) {
			t.Fatalf("stdout missing %s: %s", want, stdout.String())
		}
	}
}
```

Run: `go test ./packages/agentic-cli/internal/...`
Expected: PASS.

- [x] **Step 3: Commit**

```bash
git add packages/agentic-cli/internal/jira packages/agentic-cli/internal/cli
git commit -m "Feat(jira): add fake task listing"
```

### Task 5: 任务接管和事件日志

**Files:**
- Create: `packages/agentic-cli/internal/feedback/event.go`
- Create: `packages/agentic-cli/internal/feedback/event_test.go`
- Modify: `packages/agentic-cli/internal/cli/app.go`
- Modify: `packages/agentic-cli/internal/cli/app_test.go`

**Interfaces:**
- Produces: `feedback.Event`
- Produces: `feedback.RunID(issueKey string, taskType string, now time.Time, suffix string) string`
- Produces: `feedback.AppendEvent(path string, event Event) error`

- [x] **Step 1: Add event model**

Create `packages/agentic-cli/internal/feedback/event.go`:

```go
package feedback

import (
	"encoding/json"
	"fmt"
	"os"
	"strings"
	"time"
)

type Event struct {
	Timestamp           string `json:"timestamp"`
	Workspace           string `json:"workspace"`
	RunID               string `json:"run_id"`
	IssueKey            string `json:"issue_key,omitempty"`
	TaskType            string `json:"task_type"`
	Operation           string `json:"operation"`
	CurrentStage        string `json:"current_stage"`
	NextAction          string `json:"next_action"`
	OK                  bool   `json:"ok"`
	Code                string `json:"code,omitempty"`
	HumanGate           bool   `json:"human_gate"`
	RequiresHumanAction bool   `json:"requires_human_action"`
}

func RunID(issueKey string, taskType string, now time.Time, suffix string) string {
	cleanIssue := strings.ReplaceAll(issueKey, " ", "-")
	return fmt.Sprintf("%s-%s-%s-%s", cleanIssue, strings.TrimPrefix(taskType, "task_"), now.Format("20060102150405"), suffix)
}

func AppendEvent(path string, event Event) error {
	encoded, err := json.Marshal(event)
	if err != nil {
		return err
	}
	file, err := os.OpenFile(path, os.O_CREATE|os.O_APPEND|os.O_WRONLY, 0o644)
	if err != nil {
		return err
	}
	defer file.Close()
	_, err = file.Write(append(encoded, '\n'))
	return err
}
```

Create `packages/agentic-cli/internal/feedback/event_test.go`:

```go
package feedback

import (
	"os"
	"strings"
	"testing"
	"time"
)

func TestRunIDUsesIssueTaskAndTime(t *testing.T) {
	now := time.Date(2026, 7, 21, 10, 30, 12, 0, time.UTC)
	got := RunID("TAP-123", "task_takeover", now, "a8f3")
	if got != "TAP-123-takeover-20260721103012-a8f3" {
		t.Fatalf("RunID = %s", got)
	}
}

func TestAppendEventWritesNDJSON(t *testing.T) {
	path := t.TempDir() + "/events.ndjson"
	err := AppendEvent(path, Event{Workspace: "tapstate", RunID: "run-1", TaskType: "task_takeover", Operation: "takeover_task", CurrentStage: "takeover_gate", NextAction: "ask_owner"})
	if err != nil {
		t.Fatalf("AppendEvent error = %v", err)
	}
	data, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("ReadFile error = %v", err)
	}
	if !strings.Contains(string(data), `"current_stage":"takeover_gate"`) {
		t.Fatalf("event = %s", string(data))
	}
}
```

Run: `go test ./packages/agentic-cli/internal/feedback`
Expected: PASS.

- [x] **Step 2: Add `takeover-task` command**

Modify `packages/agentic-cli/internal/cli/app.go` to route:

```go
case "takeover-task":
	if len(args) < 2 {
		return writeJSON(stdout, output.Failure("takeover_task", "missing_issue_key", "缺少 Jira 卡片编号", "请提供 Jira 卡片编号"))
	}
	workspaceName := readFlag(args, "--workspace", "default")
	issueKey := args[1]
	issue, ok := jira.FakeClient{}.GetIssue(issueKey)
	if !ok {
		return writeJSON(stdout, output.Failure("takeover_task", "issue_not_found", "未找到 Jira 卡片", "请检查 Jira 卡片编号"))
	}
	return writeJSON(stdout, output.Success("takeover_task", map[string]any{
		"workspace":     workspaceName,
		"issue_key":     issue.Key,
		"run_id":        "TAP-123-takeover-20260721103012-a8f3",
		"task_type":     "task_takeover",
		"current_stage": "takeover_started",
		"target_repo":   issue.TargetRepo,
		"next_action":   "proceed",
	}))
```

Add test:

```go
func TestTakeoverTaskReturnsRunIDAndStage(t *testing.T) {
	var stdout bytes.Buffer
	var stderr bytes.Buffer
	code := Run([]string{"takeover-task", "TAP-123", "--workspace", "tapstate"}, &stdout, &stderr)
	if code != 0 {
		t.Fatalf("code = %d", code)
	}
	for _, want := range []string{`"operation":"takeover_task"`, `"task_type":"task_takeover"`, `"current_stage":"takeover_started"`, `"next_action":"proceed"`} {
		if !strings.Contains(stdout.String(), want) {
			t.Fatalf("stdout missing %s: %s", want, stdout.String())
		}
	}
}
```

Run: `go test ./packages/agentic-cli/internal/...`
Expected: PASS.

- [x] **Step 3: Commit**

```bash
git add packages/agentic-cli/internal/feedback packages/agentic-cli/internal/cli
git commit -m "Feat(takeover): add fake task takeover"
```

### Task 6: Evidence 写入

**Files:**
- Create: `packages/agentic-cli/internal/evidence/writer.go`
- Create: `packages/agentic-cli/internal/evidence/writer_test.go`
- Modify: `packages/agentic-cli/internal/cli/app.go`
- Modify: `packages/agentic-cli/internal/cli/app_test.go`

**Interfaces:**
- Produces: `evidence.Write(path string, content string) error`

- [x] **Step 1: Add evidence writer**

Create `packages/agentic-cli/internal/evidence/writer.go`:

```go
package evidence

import (
	"os"
	"path/filepath"
)

func Write(path string, content string) error {
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		return err
	}
	return os.WriteFile(path, []byte(content), 0o644)
}
```

Create `packages/agentic-cli/internal/evidence/writer_test.go`:

```go
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
```

Run: `go test ./packages/agentic-cli/internal/evidence`
Expected: PASS.

- [x] **Step 2: Add `write-evidence` command**

Route command:

```go
case "write-evidence":
	workspaceName := readFlag(args, "--workspace", "default")
	runID := readFlag(args, "--run-id", "")
	if runID == "" {
		return writeJSON(stdout, output.Failure("write_evidence", "missing_run_id", "缺少 run_id", "请提供 --run-id"))
	}
	return writeJSON(stdout, output.Success("write_evidence", map[string]any{
		"workspace":     workspaceName,
		"run_id":        runID,
		"current_stage": "evidence_written",
		"next_action":   "request_owner_confirmation",
	}))
```

Add test:

```go
func TestWriteEvidenceRequiresRunID(t *testing.T) {
	var stdout bytes.Buffer
	var stderr bytes.Buffer
	code := Run([]string{"write-evidence", "--workspace", "tapstate"}, &stdout, &stderr)
	if code != 1 {
		t.Fatalf("code = %d", code)
	}
	if !strings.Contains(stdout.String(), `"code":"missing_run_id"`) {
		t.Fatalf("stdout = %s", stdout.String())
	}
}
```

Run: `go test ./packages/agentic-cli/internal/...`
Expected: PASS.

- [x] **Step 3: Commit**

```bash
git add packages/agentic-cli/internal/evidence packages/agentic-cli/internal/cli
git commit -m "Feat(evidence): add evidence writer"
```

### Task 7: Feedback report

**Files:**
- Create: `packages/agentic-cli/internal/feedback/report.go`
- Create: `packages/agentic-cli/internal/feedback/report_test.go`
- Modify: `packages/agentic-cli/internal/cli/app.go`
- Modify: `packages/agentic-cli/internal/cli/app_test.go`

**Interfaces:**
- Produces: `feedback.Report{Runs, Succeeded, Blocked, Failed int}`
- Produces: `feedback.Summarize(events []Event) Report`

- [x] **Step 1: Add report summarizer**

Create `packages/agentic-cli/internal/feedback/report.go`:

```go
package feedback

type Report struct {
	Runs      int `json:"runs"`
	Succeeded int `json:"succeeded"`
	Blocked   int `json:"blocked"`
	Failed    int `json:"failed"`
}

func Summarize(events []Event) Report {
	report := Report{}
	for _, event := range events {
		report.Runs++
		if event.OK {
			report.Succeeded++
			continue
		}
		if event.RequiresHumanAction || event.NextAction == "ask_owner" {
			report.Blocked++
			continue
		}
		report.Failed++
	}
	return report
}
```

Create `packages/agentic-cli/internal/feedback/report_test.go`:

```go
package feedback

import "testing"

func TestSummarizeCountsRuns(t *testing.T) {
	got := Summarize([]Event{
		{OK: true},
		{OK: false, RequiresHumanAction: true},
		{OK: false, NextAction: "retry"},
	})
	if got.Runs != 3 {
		t.Fatalf("Runs = %d", got.Runs)
	}
	if got.Succeeded != 1 || got.Blocked != 1 || got.Failed != 1 {
		t.Fatalf("report = %+v", got)
	}
}
```

Run: `go test ./packages/agentic-cli/internal/feedback`
Expected: PASS.

- [x] **Step 2: Add `feedback report` command**

Route command:

```go
case "feedback":
	if len(args) >= 2 && args[1] == "report" {
		workspaceName := readFlag(args, "--workspace", "default")
		date := readFlag(args, "--date", "2026-07-21")
		return writeJSON(stdout, output.Success("feedback_report", map[string]any{
			"workspace":   workspaceName,
			"date":        date,
			"runs":        0,
			"succeeded":   0,
			"blocked":     0,
			"failed":      0,
			"report":      "<project-ai-workspace>/.agentic-ops/feedback/daily/" + date + ".md",
			"next_action": "review_proposals",
		}))
	}
```

Add test:

```go
func TestFeedbackReportOutputsReportPath(t *testing.T) {
	var stdout bytes.Buffer
	var stderr bytes.Buffer
	code := Run([]string{"feedback", "report", "--workspace", "tapstate", "--date", "2026-07-21"}, &stdout, &stderr)
	if code != 0 {
		t.Fatalf("code = %d", code)
	}
	if !strings.Contains(stdout.String(), `"operation":"feedback_report"`) {
		t.Fatalf("stdout = %s", stdout.String())
	}
	if !strings.Contains(stdout.String(), `"next_action":"review_proposals"`) {
		t.Fatalf("stdout = %s", stdout.String())
	}
}
```

Run: `go test ./packages/agentic-cli/internal/...`
Expected: PASS.

- [x] **Step 3: Commit**

```bash
git add packages/agentic-cli/internal/feedback packages/agentic-cli/internal/cli
git commit -m "Feat(feedback): add feedback report"
```

### Task 8: 安装 bootstrap 草案

**Files:**
- Create: `scripts/init.sh`
- Create: `scripts/test-init.sh`

**Interfaces:**
- Consumes: release artifact naming convention `agentic-cli_<os>_<arch>.tar.gz`
- Produces: `~/.agentic-ops/bin/agentic-cli`

- [x] **Step 1: Add bootstrap script**

Create `scripts/init.sh`:

```sh
#!/usr/bin/env bash
set -euo pipefail

INSTALL_DIR="${AGENTIC_OPS_HOME:-$HOME/.agentic-ops}"
BIN_DIR="$INSTALL_DIR/bin"
VERSION="${AGENTIC_OPS_VERSION:-latest}"

os="$(uname -s | tr '[:upper:]' '[:lower:]')"
arch="$(uname -m)"

case "$os" in
  darwin) target_os="darwin" ;;
  linux) target_os="linux" ;;
  *) echo "unsupported OS: $os" >&2; exit 1 ;;
esac

case "$arch" in
  arm64|aarch64) target_arch="arm64" ;;
  x86_64|amd64) target_arch="amd64" ;;
  *) echo "unsupported arch: $arch" >&2; exit 1 ;;
esac

mkdir -p "$BIN_DIR"

cat > "$BIN_DIR/agentic-cli" <<'SH'
#!/usr/bin/env sh
echo '{"ok":false,"operation":"install","code":"binary_not_installed","message":"agentic-cli release binary has not been downloaded in this first-stage bootstrap"}'
exit 1
SH

chmod +x "$BIN_DIR/agentic-cli"

printf '{"ok":true,"operation":"install","install_dir":"%s","bin":"%s","target":"%s-%s","version":"%s","next_action":"workspace_init"}\n' "$INSTALL_DIR" "$BIN_DIR/agentic-cli" "$target_os" "$target_arch" "$VERSION"
```

This script is a first-stage bootstrap stub that does not download a real binary. Replace the embedded stub with real release download logic when release artifacts exist.

- [x] **Step 2: Add bootstrap smoke test**

Create `scripts/test-init.sh`:

```sh
#!/usr/bin/env bash
set -euo pipefail

tmp_home="$(mktemp -d)"
trap 'rm -rf "$tmp_home"' EXIT

HOME="$tmp_home" bash scripts/init.sh > "$tmp_home/out.json"

grep '"ok":true' "$tmp_home/out.json"
test -x "$tmp_home/.agentic-ops/bin/agentic-cli"
```

Run: `bash scripts/test-init.sh`
Expected: command exits 0 and prints the matched JSON line containing `"ok":true`.

- [x] **Step 3: Commit**

```bash
git add scripts/init.sh scripts/test-init.sh
git commit -m "Feat(install): add bootstrap installer"
```

### Task 9: 本地端到端演示

**Files:**
- Create: `tests/e2e/local-fake-flow.sh`
- Modify: `docs/examples/end-to-end-demo.md`

**Interfaces:**
- Consumes: `agentic-cli` command from `go run ./packages/agentic-cli/cmd/agentic-cli`
- Produces: 本地模拟流程证据，证明安装、工作空间初始化、AIAgent 初始化、拉取任务、接管、写证据和反馈报告都能产生 JSON。

- [x] **Step 1: Add local simulation script**

Create `tests/e2e/local-fake-flow.sh`:

```sh
#!/usr/bin/env bash
set -euo pipefail

cmd="go run ./packages/agentic-cli/cmd/agentic-cli"

$cmd --version | grep '"operation":"version"'
$cmd workspace init --workspace tapstate | grep '"operation":"workspace_init"'
$cmd agent init --workspace tapstate | grep '"operation":"agent_init"'
$cmd list-tasks --workspace tapstate | grep '"key":"TAP-123"'
$cmd takeover-task TAP-123 --workspace tapstate | grep '"current_stage":"takeover_started"'
$cmd write-evidence --workspace tapstate --run-id TAP-123-takeover-20260721103012-a8f3 | grep '"operation":"write_evidence"'
$cmd feedback report --workspace tapstate --date 2026-07-21 | grep '"operation":"feedback_report"'
```

Run: `bash tests/e2e/local-fake-flow.sh`
Expected: all commands exit 0.

- [x] **Step 2: Update demo doc with local simulation command**

Modify `docs/examples/end-to-end-demo.md` to add this verification command under demo acceptance:

```sh
bash tests/e2e/local-fake-flow.sh
```

Expected description: 该命令运行第一阶段本地模拟流程，不执行真实 Jira 或 GitHub 写操作。

- [x] **Step 3: Commit**

```bash
git add tests/e2e/local-fake-flow.sh docs/examples/end-to-end-demo.md
git commit -m "Test(e2e): add local fake flow"
```

## 3. 第一阶段完成标准

第一阶段实现完成时，必须满足：

- `go test ./...` 通过。
- `bash scripts/test-init.sh` 通过。
- `bash tests/e2e/local-fake-flow.sh` 通过。
- 所有 CLI 成功输出包含 `ok: true` 和 `operation`。
- 所有 CLI 失败输出包含 `ok: false`、`operation`、`code`、`message`。
- fake takeover 输出包含 `run_id`、`task_type`、`current_stage`、`next_action`。
- 后续真实 Jira 接管门禁必须校验 `assignee`、`current_agent_id`、`task_class` 和 `process_id`；当前本地模拟流程只验证本地最小闭环。
- 没有真实 Jira / GitHub 写操作。
- 没有 secrets、tokens、private keys 或原始敏感日志。

## 4. 后续接真实 Jira 的入口

本计划完成后，再新增第二份计划，范围只包含：

- Jira current user。
- Jira 卡片搜索。
- Jira 卡片读取。
- Jira comment write。
- Workflow Profile中 Jira 字段映射。
- Standard Process Registry 的机器可读契约。
- task class 到 process id 的映射。
- 负责人和 `assignee` 匹配门禁。
- `agent_id` 初始化和持久化。
- `current_agent_id` 接管写入、执行过程校验和完成清理。
- `takeover_at`、`completed_at` 和 `current_agent_id_cleared` 回写。
- AIAgent 结构化事件上报字段扩展，包括 `agent_id`、`current_agent_id`、`task_class`、`process_id` 和 `current_agent_id_cleared`。

不要在第一阶段本地模拟流程中混入真实 Jira 接入。

## 5. 自检记录

- Spec coverage: 覆盖安装、运行资产安装、本地 build / release 打包、工作空间初始化、AIAgent 初始化、新任务接管、evidence 写入、反馈报告。
- Operation scope: 第一批可运行命令只取最小闭环，其他操作保留为后续计划。
- Contract source: 顶层 `contracts/operations/` 是唯一机器可读契约源头。
- Runtime boundary: Go CLI 承载业务逻辑，shell 只做安装引导。
- Human gate: 推送、创建拉取请求、合并、发布不在第一批自动执行范围内。
- 实现说明：第一阶段实现补齐了计划命令范围中的 `preflight`、`agent init`、`resume-takeover`、本地 `assets install`、`scripts/build.sh` 和 `scripts/release.sh`，并创建本地模拟流程所需操作 YAML；真实 Jira / GitHub 写操作仍未接入。
