# `resume-takeover` 完整恢复门禁实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 `resume-takeover` 从同一 `agentic_run_id` 恢复可信上下文，在恢复前完成 Jira 所有权、目标仓库、操作阶段和标准流程阶段校验，并为任务级阻塞生成受控 Jira 反馈材料。

**Architecture:** 新增 `internal/runcontext` 统一读取事件上下文，在 `internal/jira` 增加纯 `ResumeGate`，由 CLI handler 负责加载资源、执行 Jira 只读查询、记录本地审计并生成阻塞评论文件。Jira 评论继续通过现有 `add-task-comment` 原子操作和显式人工门禁写入。

**Tech Stack:** Go、`gopkg.in/yaml.v3`、现有 Jira adapter、Operation Contract、Standard Process Registry、Go `testing`、Bash E2E。

## Global Constraints

- 设计源头：`docs/architecture/resume-takeover-recovery-gate-design.md`。
- Jira 是任务事实源；本地事件不能覆盖 Jira 当前负责人、代理绑定、状态或目标仓库。
- `resume-takeover` 只能读取 Jira，不得调用 `AddComment`、`UpdateFields`、`TransitionIssue` 或其它 Jira 写接口。
- 代理绑定为空时返回 `agent_binding_lost`，不得自动重新绑定。
- 目标仓库变化时返回 `target_repo_changed`，不得让同一个 `agentic_run_id` 静默切换仓库。
- 操作阶段与标准流程阶段分别校验，不得直接比较。
- 恢复成功不得推进业务阶段，不得生成新的 `agentic_run_id`。
- 真实 Jira 评论必须通过 `add-task-comment` 和 `--confirm-real-jira-write`。
- 测试只能使用 fake adapter、recording client 或 `httptest`，不得调用真实 Jira 写操作。
- 不引入新的第三方依赖，不实现通用工作流引擎或通用 Jira 评论幂等系统。
- 所有用户、研发工程师和 Jira 可见自然语言使用中文。
- 本计划执行期间不创建分支、不提交、不推送；只有研发工程师后续明确要求时才允许提交，且提交后不得由 AIAgent 推送。

---

## File Structure

### New files

- `packages/agentic-cli/internal/runcontext/context.go`
  - 从事件列表恢复可信的任务运行上下文。
- `packages/agentic-cli/internal/runcontext/context_test.go`
  - 覆盖上下文恢复、冲突、状态事件筛选和旧事件兼容。
- `packages/agentic-cli/internal/jira/resume_gate.go`
  - 纯函数实现恢复门禁决策。
- `packages/agentic-cli/internal/jira/resume_gate_test.go`
  - 覆盖所有权、仓库、契约和流程失败分支。
- `packages/agentic-cli/internal/clihandlers/resume_feedback.go`
  - 生成安全的 Jira 阻塞评论文件和结构化反馈元数据。
- `packages/agentic-cli/internal/clihandlers/resume_feedback_test.go`
  - 验证评论内容、路径、写入资格和敏感信息边界。

### Modified files

- `packages/agentic-cli/internal/clihandlers/task.go`
  - 使用新组件重写 `runResumeTakeover`，移除旧 `resumeRunState`。
- `packages/agentic-cli/internal/clihandlers/evidence_context.go`
  - 改为复用 `internal/runcontext`。
- `packages/agentic-cli/internal/clihandlers/repo_paths.go`
  - 增加操作契约加载入口，并补齐默认流程的 `TaskClasses`。
- `packages/agentic-cli/internal/cli/task_command_test.go`
  - 增加 CLI 成功、失败和 Jira 只读行为测试。
- `packages/agentic-cli/internal/cli/test_helpers_test.go`
  - 仅在测试需要时补充 recording client 调用计数。
- `install-resources/basic/contracts/operations/resume-takeover.yaml`
  - 对齐输出字段、失败码和本地反馈副作用。
- `docs/user-stories/development-engineer/de-005-resume-takeover.md`
  - 对齐双层阶段和两步 Jira 反馈流程。
- `install-resources/basic/handbooks/ai-employee-handbook.md`
  - 增加恢复阻塞后的受控评论指引。
- `install-resources/<os-arch>/agentic-cli`
  - 由 `scripts/test-build.sh` 重新生成四个平台安装二进制。
- `tests/e2e/local-fake-flow.sh`
  - 验证恢复输出保留阶段并包含仓库和标准流程阶段。
- `plans/design-implementation-gap-todo-v1.md`
  - 完成 Task 1 的三个剩余缺口并更新实现证据。
- `install-resources/checksums.txt`
  - 由 `scripts/update-checksums.sh` 机械更新。

---

### Task 1: 统一运行上下文读取

**Files:**
- Create: `packages/agentic-cli/internal/runcontext/context.go`
- Create: `packages/agentic-cli/internal/runcontext/context_test.go`

**Interfaces:**
- Consumes: `feedback.Event`
- Produces:

```go
type Query struct {
	RunID     string
	Workspace string
	AgentID   string
}

type Context struct {
	Workspace       string
	RunID           string
	IssueKey        string
	AgentID         string
	CurrentAgentID  string
	TaskClass       string
	ProcessID       string
	TargetRepo      string
	CurrentStage    string
	NextAction      string
	Terminal        bool
	HumanGatePending bool
}

var ErrRunNotFound error
var ErrWorkspaceMismatch error
var ErrLocalStateMismatch error

func Read(events []feedback.Event, query Query) (Context, error)
func ReadFile(path string, query Query) (Context, error)
func ErrorCode(err error) string
```

- [x] **Step 1: 写接管基准恢复失败测试**

在 `context_test.go` 增加 `TestReadRestoresTakeoverContext`，构造一个完整成功的 `takeover_task` 事件，断言所有不可变字段、`CurrentStage=takeover_started` 和 `NextAction=proceed`。

```go
got, err := Read([]feedback.Event{{
	Workspace:      "tapstate",
	RunID:          "run-1",
	IssueKey:       "TAP-123",
	Operation:      "takeover_task",
	CurrentStage:   "takeover_started",
	NextAction:     "proceed",
	AgentID:        "agent-1",
	CurrentAgentID: "agent-1",
	TaskClass:      "technical_task",
	ProcessID:      "development_change_v1",
	TargetRepo:     "tapstate/example-repo",
	OK:             true,
}}, Query{RunID: "run-1", Workspace: "tapstate", AgentID: "agent-1"})
```

- [x] **Step 2: 运行测试并确认 RED**

Run:

```sh
go test ./packages/agentic-cli/internal/runcontext -run TestReadRestoresTakeoverContext -v
```

Expected: FAIL，因为 `internal/runcontext` 尚不存在。

- [x] **Step 3: 实现最小接管基准读取**

在 `context.go` 中：

- 查找第一个 `RunID` 匹配、`Operation == "takeover_task"` 且 `OK` 的事件。
- 校验 query 的 workspace 和 agent。
- 要求 `IssueKey`、`AgentID`、`CurrentAgentID`、`TaskClass`、`ProcessID`、`CurrentStage` 和 `NextAction` 非空；`TargetRepo` 允许为空，以兼容后续由当前 Jira/profile 确定性补齐的旧 run。
- 初始化并返回 `Context`。

- [x] **Step 4: 运行测试并确认 GREEN**

Run:

```sh
go test ./packages/agentic-cli/internal/runcontext -run TestReadRestoresTakeoverContext -v
```

Expected: PASS。

- [x] **Step 5: 写字段冲突和错误码失败测试**

增加：

- `TestReadRejectsWorkspaceMismatch`
- `TestReadRejectsIncompleteTakeoverContext`
- `TestReadRejectsImmutableFieldConflict`
- `TestErrorCodeReturnsStableCodes`

冲突测试在后续事件中改变 `IssueKey`、`AgentID`、`TaskClass`、`ProcessID` 或 `TargetRepo`，期望 `ErrLocalStateMismatch`。

- [x] **Step 6: 实现不可变字段冲突校验**

扫描接管基准后的同 run 事件。事件中的非空不可变字段必须等于基准；空字段表示该事件没有重复携带该事实，不构成冲突。`TargetRepo` 特殊处理：基准为空时，第一个非空值用于补齐上下文；基准已有值后再出现不同非空值才返回 `ErrLocalStateMismatch`。

- [x] **Step 7: 写恢复点筛选失败测试**

增加：

- `TestReadIgnoresAuxiliaryJiraWriteEvents`
- `TestReadUsesLatestStateBearingEvent`
- `TestReadIgnoresFailedResumeAttempt`
- `TestReadIgnoresLegacyTakeoverResumedStage`
- `TestReadMarksTerminalState`
- `TestReadMarksHumanGatePending`

状态操作集合必须精确为：

```go
map[string]bool{
	"takeover_task":  true,
	"resume_takeover": true,
	"write_evidence": true,
	"prepare_pr":     true,
	"release_agent":  true,
}
```

`add_task_comment`、`update_task_form` 和 `update_task_description_sections` 事件不得覆盖恢复点。

- [x] **Step 8: 实现恢复点选择和文件读取**

实现 `ReadFile` 调用 `feedback.ReadEvents` 后复用 `Read`。恢复点选择规则：

- 按事件顺序更新。
- 跳过失败的 `resume_takeover`。
- 跳过 `CurrentStage == "takeover_resumed"` 的旧版 resume 成功事件。
- `CurrentStage == "completed"` 或 `NextAction == "task_audit_submitted"` 标记 `Terminal`。
- 最新状态事件 `RequiresHumanAction` 为真时标记 `HumanGatePending`。

- [x] **Step 9: 运行包测试**

Run:

```sh
go test ./packages/agentic-cli/internal/runcontext -v
```

Expected: PASS。

- [x] **Step 10: 检查本任务变更**

Run:

```sh
gofmt -w packages/agentic-cli/internal/runcontext/context.go packages/agentic-cli/internal/runcontext/context_test.go
git diff --check
git status --short
```

Expected: 仅出现本任务新增文件和此前已确认的设计、计划文件；不提交。

---

### Task 2: 实现纯 `ResumeGate`

**Files:**
- Create: `packages/agentic-cli/internal/jira/resume_gate.go`
- Create: `packages/agentic-cli/internal/jira/resume_gate_test.go`
- Modify: `packages/agentic-cli/internal/clihandlers/repo_paths.go`

**Interfaces:**
- Consumes: `runcontext.Context`、`jira.Issue`、`profile.Profile`、`contract.Operation`、`map[string]process.Process`
- Produces:

```go
type ResumeInput struct {
	Context         runcontext.Context
	Issue           Issue
	CurrentUser     string
	AgentID         string
	AdapterMode     string
	Profile         profile.Profile
	Contract        contract.Operation
	ProcessRegistry map[string]process.Process
}

type ResumeDecision struct {
	OK                       bool
	Code                     string
	Message                  string
	RequiredHumanAction      string
	StandardProcessStage     string
	TargetRepo               string
	JiraFeedbackRequired     bool
	JiraFeedbackWriteAllowed bool
}

func ValidateResume(input ResumeInput) ResumeDecision
```

- [x] **Step 1: 写成功门禁失败测试**

在 `resume_gate_test.go` 构造：

- 历史阶段 `takeover_started`。
- 契约允许 `takeover_started`。
- Jira 状态 `To Do` 映射为 `waiting_takeover`。
- `development_change_v1` 包含 `technical_task` 和 `waiting_takeover`。
- real 模式下 assignee、当前用户和代理绑定均匹配。
- 当前仓库等于历史仓库。

断言 `OK=true` 且 `StandardProcessStage=waiting_takeover`。

- [x] **Step 2: 运行成功测试并确认 RED**

Run:

```sh
go test ./packages/agentic-cli/internal/jira -run TestValidateResumeAllowsMatchingFacts -v
```

Expected: FAIL，因为 `ValidateResume` 尚不存在。

- [x] **Step 3: 实现契约、仓库和流程最小校验**

实现以下顺序：

1. `Context.Terminal` → `terminal_run`。
2. `Context.HumanGatePending` → `human_gate_pending`。
3. 操作阶段不在 `Contract.AllowedStages` → `resume_stage_not_allowed`。
4. Jira issue key 不一致 → `issue_mismatch`。
5. 使用现有 `targetRepoFor` 解析当前仓库。
6. 当前仓库缺失 → `target_repo_missing`。
7. 历史仓库非空且与当前值不同 → `target_repo_changed`。
8. 历史仓库为空时使用当前确定性映射补齐，并通过 decision 返回。
9. 流程不存在 → `standard_process_not_found`。
10. `TaskClass` 不属于流程 → `task_class_process_mismatch`。
11. Jira 状态无映射 → `lifecycle_mapping_gap`。
12. 映射阶段不属于流程 → `invalid_process_stage`。
13. 映射阶段为 `completed` → `terminal_run`。

- [x] **Step 4: 运行成功测试并确认 GREEN**

Run:

```sh
go test ./packages/agentic-cli/internal/jira -run TestValidateResumeAllowsMatchingFacts -v
```

Expected: PASS。

- [x] **Step 5: 写真实 Jira 所有权失败测试**

增加：

- `TestValidateResumeRejectsChangedAssignee`
- `TestValidateResumeRejectsLostAgentBinding`
- `TestValidateResumeRejectsOtherAgent`
- `TestValidateResumeSkipsRemoteBindingCheckForFakeAdapter`

预期：

| 条件 | code | feedback required | write allowed |
| --- | --- | --- | --- |
| assignee 改变 | `assignee_changed` | true | false |
| `agentic_id` 为空 | `agent_binding_lost` | true | true |
| 绑定其他代理 | `agent_ownership_conflict` | true | false |
| fake adapter 绑定为空 | success | false | false |

- [x] **Step 6: 实现真实 Jira 所有权分支**

只有 `AdapterMode == "real"` 时执行：

```go
if input.Issue.Assignee != input.CurrentUser { ... }
if input.Issue.CurrentAgentID == "" { ... }
if input.Issue.CurrentAgentID != input.AgentID { ... }
```

失败 decision 必须携带中文 `Message` 和 `RequiredHumanAction`。

- [x] **Step 7: 写仓库、契约和流程矩阵测试**

增加并逐一断言稳定 code：

- `target_repo_missing`
- `target_repo_changed`
- 历史 target repo 缺失但当前映射可确定时成功补齐
- `resume_stage_not_allowed`
- `standard_process_not_found`
- `task_class_process_mismatch`
- `lifecycle_mapping_gap`
- `invalid_process_stage`
- `terminal_run`
- `human_gate_pending`

- [x] **Step 8: 补齐默认流程任务分类**

修改 `defaultProcessRegistry()`，为三个默认流程增加与 YAML 一致的 `TaskClasses`：

```go
TaskClasses: []string{"feature_change", "bug_fix", "technical_task"}
```

以及 investigation、process improvement 对应分类。

- [x] **Step 9: 运行 Jira 和 clihandlers 包测试**

Run:

```sh
gofmt -w packages/agentic-cli/internal/jira/resume_gate.go packages/agentic-cli/internal/jira/resume_gate_test.go packages/agentic-cli/internal/clihandlers/repo_paths.go
go test ./packages/agentic-cli/internal/jira ./packages/agentic-cli/internal/clihandlers -v
```

Expected: PASS。

- [x] **Step 10: 检查本任务变更**

Run:

```sh
git diff --check
git status --short
```

Expected: 无范围外文件；不提交。

---

### Task 3: 集成 `resume-takeover` 只读门禁

**Files:**
- Modify: `packages/agentic-cli/internal/clihandlers/task.go`
- Modify: `packages/agentic-cli/internal/clihandlers/repo_paths.go`
- Modify: `packages/agentic-cli/internal/cli/task_command_test.go`
- Modify: `packages/agentic-cli/internal/cli/test_helpers_test.go`

**Interfaces:**
- Consumes: `runcontext.ReadFile`、`jira.ValidateResume`
- Produces: 更新后的 `runResumeTakeover(args []string, stdout io.Writer) int`

- [x] **Step 1: 更新 fake 成功测试并确认 RED**

修改 `TestResumeTakeoverReturnsRunIDAndNextAction` 的事件，补齐：

```json
"target_repo":"tapstate/example-repo"
```

新断言：

```go
assertJSONField(t, stdout.String(), "previous_stage", "takeover_started")
assertJSONField(t, stdout.String(), "current_stage", "takeover_started")
assertJSONField(t, stdout.String(), "agentic_next_action", "proceed")
assertJSONField(t, stdout.String(), "target_repo", "tapstate/example-repo")
assertJSONField(t, stdout.String(), "standard_process_stage", "waiting_takeover")
```

Run:

```sh
go test ./packages/agentic-cli/internal/cli -run TestResumeTakeoverReturnsRunIDAndNextAction -v
```

Expected: FAIL，当前实现仍返回 `takeover_resumed`。

- [x] **Step 2: 写 real 模式只读成功测试**

新增 `TestResumeTakeoverRechecksRealJiraWithoutWriting`：

- 使用 `realModeBoundIssue()`。
- 历史 target repo 与 issue 一致。
- recording client 增加 Jira 写调用计数或复用现有字段。
- 断言 `AddComment`、`UpdateFields`、`TransitionIssue` 均未调用。

- [x] **Step 3: 增加操作契约加载入口**

在 `repo_paths.go` 增加：

```go
func repoOperationContract(operation string) (contract.Operation, error) {
	root, err := repoRoot()
	if err != nil {
		return contract.Operation{}, err
	}
	path := filepath.Join(
		repoBasicResourcesPath(root),
		"contracts",
		"operations",
		strings.ReplaceAll(operation, "_", "-")+".yaml",
	)
	return contract.LoadFile(path)
}
```

- [x] **Step 4: 重写 `runResumeTakeover`**

处理顺序必须与设计一致：

1. 读取 `agentic_run_id` 和 workspace root。
2. `runcontext.ReadFile`。
3. 加载 profile、operation contract 和 process registry。
4. 选择 Jira adapter。
5. `GetIssueByKey` 和 `CurrentUser`。
6. 调用 `jira.ValidateResume`。
7. 成功时写入保留原阶段和原 `agentic_next_action` 的 `resume_takeover` 事件。
8. 输出 decision 中经过校验或补齐的 `target_repo` 和 `standard_process_stage`。
9. 成功恢复事件写入经过校验或补齐的 `target_repo`。

删除：

- `resumeRunState`
- `resumableRunState`
- 本地重复错误变量和旧 `takeover_resumed` 输出逻辑。

保留 `resumeErrorCode` 的调用方时，改为委托 `runcontext.ErrorCode`。

- [x] **Step 5: 写 CLI 失败矩阵测试**

使用表驱动测试覆盖：

- `assignee_changed`
- `agent_binding_lost`
- `agent_ownership_conflict`
- `target_repo_changed`
- `resume_stage_not_allowed`
- `standard_process_not_found`
- `lifecycle_mapping_gap`
- `terminal_run`

每个用例断言：

- exit code 为 1。
- `operation=resume_takeover`。
- code 稳定。
- 没有 Jira 写调用。

- [x] **Step 6: 运行 CLI 定向测试**

Run:

```sh
gofmt -w packages/agentic-cli/internal/clihandlers/task.go packages/agentic-cli/internal/clihandlers/repo_paths.go packages/agentic-cli/internal/cli/task_command_test.go packages/agentic-cli/internal/cli/test_helpers_test.go
go test ./packages/agentic-cli/internal/cli -run 'TestResumeTakeover' -v
```

Expected: PASS。

- [x] **Step 7: 检查本任务变更**

Run:

```sh
git diff --check
git status --short
```

Expected: 无真实 Jira 写入、无范围外文件；不提交。

---

### Task 4: 生成 Jira 阻塞反馈材料

**Files:**
- Create: `packages/agentic-cli/internal/clihandlers/resume_feedback.go`
- Create: `packages/agentic-cli/internal/clihandlers/resume_feedback_test.go`
- Modify: `packages/agentic-cli/internal/clihandlers/task.go`
- Modify: `packages/agentic-cli/internal/cli/task_command_test.go`

**Interfaces:**
- Consumes: `runcontext.Context`、`jira.ResumeDecision`
- Produces:

```go
type resumeFeedback struct {
	Required     bool
	WriteAllowed bool
	File         string
	Category     string
	NextAction   string
}

func writeResumeFeedback(
	root string,
	context runcontext.Context,
	decision jira.ResumeDecision,
) (resumeFeedback, error)
```

- [x] **Step 1: 写安全评论文件失败测试**

测试 `agent_binding_lost`：

- 文件写入 `<root>/.agentic-ops/runs/run-1/resume-blocked-agent_binding_lost.md`。
- 返回给 CLI 的路径为相对路径 `.agentic-ops/runs/...`。
- 内容包含 `AgenticOps 恢复阻塞`、反馈编号、run、issue、code 和中文处理动作。
- 内容不包含 workspace root 的绝对路径。

- [x] **Step 2: 运行测试并确认 RED**

Run:

```sh
go test ./packages/agentic-cli/internal/clihandlers -run TestWriteResumeFeedback -v
```

Expected: FAIL，因为 helper 尚不存在。

- [x] **Step 3: 实现反馈文件生成**

文件名只使用经过既有 run id 安全约束的 `agentic_run_id` 和稳定 code。内容固定使用以下结构：

```text
# AgenticOps 恢复阻塞

- 反馈编号: resume-blocked:<agentic_run_id>:<code>
- 工作空间: <workspace>
- Jira 卡片: <issue_key>
- agentic_run_id: <agentic_run_id>
- 错误码: <code>
- 说明: <message>
- 需要处理: <required_human_action>
```

使用 `os.MkdirAll` 和 `os.WriteFile`，权限分别为 `0o755` 和 `0o644`。

- [x] **Step 4: 写反馈资格矩阵测试**

断言：

- `agent_binding_lost` → required true、write allowed true、`add_task_comment`。
- `target_repo_changed` → required true、write allowed true、`add_task_comment`。
- `assignee_changed` → required true、write allowed false、`ask_owner_to_add_task_comment`。
- `agent_ownership_conflict` → required true、write allowed false、`ask_owner_to_add_task_comment`。
- `terminal_run`、本地错误和 Jira 读取错误 → required false，不创建文件。

- [x] **Step 5: 集成失败输出**

在 `runResumeTakeover` 收到失败 decision 后：

1. 写本地失败审计事件。
2. 调用 `writeResumeFeedback`。
3. 将以下字段加入失败 JSON：

```go
result["jira_feedback_required"] = feedback.Required
result["jira_feedback_write_allowed"] = feedback.WriteAllowed
result["jira_feedback_file"] = feedback.File
result["jira_feedback_category"] = feedback.Category
```

4. `agentic_next_action` 使用 feedback 决定值。
5. 文件写入失败返回 `feedback_write_failed`，不尝试 Jira 写入。

- [x] **Step 6: 写 CLI 反馈闭环测试**

增加：

- `TestResumeTakeoverCreatesWritableJiraFeedbackForLostBinding`
- `TestResumeTakeoverCreatesOwnerOnlyFeedbackForOwnershipConflict`
- `TestResumeTakeoverDoesNotCreateFeedbackForUntrustedLocalFailure`
- `TestGeneratedResumeFeedbackCanBePassedToAddTaskComment`

最后一个测试使用 fake/recording client 调用现有 `add-task-comment`，携带同一 `agentic_run_id` 和生成的文件，断言 category 为 `blocked`。

- [x] **Step 7: 运行定向测试**

Run:

```sh
gofmt -w packages/agentic-cli/internal/clihandlers/resume_feedback.go packages/agentic-cli/internal/clihandlers/resume_feedback_test.go packages/agentic-cli/internal/clihandlers/task.go packages/agentic-cli/internal/cli/task_command_test.go
go test ./packages/agentic-cli/internal/clihandlers ./packages/agentic-cli/internal/cli -run 'ResumeFeedback|ResumeTakeover' -v
```

Expected: PASS。

- [x] **Step 8: 检查本任务变更**

Run:

```sh
git diff --check
git status --short
```

Expected: 无真实 Jira 写调用；不提交。

---

### Task 5: 迁移证据和通用 Jira 写操作的运行上下文

**Files:**
- Modify: `packages/agentic-cli/internal/clihandlers/evidence_context.go`
- Modify: `packages/agentic-cli/internal/clihandlers/write_evidence.go`
- Modify: `packages/agentic-cli/internal/clihandlers/release_agent.go`
- Modify: `packages/agentic-cli/internal/clihandlers/jira_write.go`
- Modify: `packages/agentic-cli/internal/cli/evidence_release_test.go`
- Modify: `packages/agentic-cli/internal/cli/jira_write_command_test.go`

**Interfaces:**
- Consumes: `runcontext.ReadFile`
- Produces: `write-evidence`、`release-agent` 和带 `--run-id` 的 Jira 原子写操作共享相同本地上下文语义。

- [x] **Step 1: 写辅助 Jira 写事件不覆盖上下文测试**

在 CLI 测试中准备：

1. 成功接管事件。
2. 同 run 的 `add_task_comment` / `jira_write_completed` 事件。
3. 调用 `write-evidence` 或带 run 的 `add-task-comment`。

断言仍读取接管阶段、目标仓库和 issue，不返回 `local_state_mismatch`。

- [x] **Step 2: 运行测试并确认 RED**

Run:

```sh
go test ./packages/agentic-cli/internal/cli -run 'AuxiliaryJiraWriteEvent|EvidenceRunContext' -v
```

Expected: 至少一个新增测试 FAIL，证明旧重复读取逻辑未使用新规则。

- [x] **Step 3: 删除重复上下文扫描**

将 `evidenceRunState` 改为 `runcontext.ReadFile` 的薄适配，或直接让调用方使用 `runcontext.Context`。删除：

- 独立扫描 takeover/resume 事件的循环。
- 重复的字段完整性判断。
- 重复的 target repo 累积逻辑。

`write-evidence` 模板中的 `previous_stage` 改用 `context.CurrentStage`。

- [x] **Step 4: 统一错误码映射**

`evidenceStateErrorCode` 和相关调用统一委托 `runcontext.ErrorCode`。保持现有外部稳定错误码：

- `run_not_found`
- `workspace_mismatch`
- `local_state_mismatch`
- `event_read_failed`

- [x] **Step 5: 运行证据、释放和 Jira 写测试**

Run:

```sh
gofmt -w packages/agentic-cli/internal/clihandlers/evidence_context.go packages/agentic-cli/internal/clihandlers/write_evidence.go packages/agentic-cli/internal/clihandlers/release_agent.go packages/agentic-cli/internal/clihandlers/jira_write.go packages/agentic-cli/internal/cli/evidence_release_test.go packages/agentic-cli/internal/cli/jira_write_command_test.go
go test ./packages/agentic-cli/internal/cli -run 'WriteEvidence|ReleaseAgent|AddTaskComment|UpdateTask' -v
```

Expected: PASS。

- [x] **Step 6: 运行全部 Go 测试检查共享行为**

Run:

```sh
go test ./...
```

Expected: PASS。

- [x] **Step 7: 检查本任务变更**

Run:

```sh
git diff --check
git status --short
```

Expected: 无范围外文件；不提交。

---

### Task 6: 对齐契约、用户故事、手册和 E2E

**Files:**
- Modify: `install-resources/basic/contracts/operations/resume-takeover.yaml`
- Modify: `docs/user-stories/development-engineer/de-005-resume-takeover.md`
- Modify: `install-resources/basic/handbooks/ai-employee-handbook.md`
- Modify: `tests/e2e/local-fake-flow.sh`
- Modify: `plans/design-implementation-gap-todo-v1.md`
- Modify: `install-resources/checksums.txt`

**Interfaces:**
- Consumes: Tasks 1-5 已实现的 CLI 行为。
- Produces: 与实现一致的安装资源、用户说明、进度状态和自动化验收。

- [x] **Step 1: 先更新 E2E 断言并确认 RED**

将恢复断言改为检查：

```text
"previous_stage":"takeover_started"
"current_stage":"takeover_started"
"agentic_next_action":"proceed"
"target_repo":"tapstate/example-repo"
"standard_process_stage":"waiting_takeover"
```

Run:

```sh
bash tests/e2e/local-fake-flow.sh
```

Expected: 在契约/资源尚未同步时 FAIL，或至少证明旧输出断言已被替换。

- [x] **Step 2: 更新 `resume-takeover.yaml`**

保持 `allowed_stages` 明确列出第一阶段允许恢复的操作阶段。输出新增：

- `target_repo`
- `standard_process_stage`
- `jira_feedback_required`
- `jira_feedback_write_allowed`
- `jira_feedback_file`
- `jira_feedback_category`

失败码与设计第 7 节完全一致。副作用增加：

```yaml
side_effects:
  - may_write_local_event
  - may_write_local_feedback_artifact
  - must_not_write_jira
  - must_not_create_pr
```

- [x] **Step 3: 更新 DE-005 用户故事**

明确：

- 操作阶段与标准流程阶段分别返回。
- 恢复不推进业务阶段。
- real 模式重新读取所有权和仓库。
- 绑定丢失、仓库变化和流程不一致的停止行为。
- 两步 `resume-takeover -> add-task-comment` Jira 反馈流程。

- [x] **Step 4: 更新 AI 员工手册**

在恢复命令后增加行为规则：

1. `jira_feedback_required=false` 时按返回的 `agentic_next_action` 处理。
2. `jira_feedback_required=true` 且 write allowed 时，研发工程师确认后调用 `add-task-comment`。
3. write allowed 为 false 时，停止并把评论材料交给研发工程师或当前负责人。
4. 写入前先 `inspect-task` 检查反馈编号，避免重复评论。

- [x] **Step 5: 更新实现缺口计划**

在 `plans/design-implementation-gap-todo-v1.md` 的 Task 1：

- 勾选真实 Jira 所有权复核。
- 勾选 `target_repo` 恢复与校验。
- 勾选 Standard Process Registry 阶段校验。
- 补充 `RunContextReader`、`ResumeGate`、Jira 反馈文件和相关测试为实现证据。
- 不修改其它任务状态。

- [x] **Step 6: 更新安装资源校验和**

Run:

```sh
bash scripts/update-checksums.sh
```

Expected: 仅 `install-resources/checksums.txt` 的受影响资源摘要变化。

- [x] **Step 7: 运行资源和 E2E 测试**

Run:

```sh
bash scripts/test-resources.sh
bash tests/e2e/local-fake-flow.sh
```

Expected: PASS。

- [x] **Step 8: 检查文档与契约一致性**

Run:

```sh
rg -n "takeover_resumed|continue_development" docs/user-stories/development-engineer/de-005-resume-takeover.md install-resources/basic/contracts/operations/resume-takeover.yaml install-resources/basic/handbooks/ai-employee-handbook.md tests/e2e/local-fake-flow.sh
rg -n "T[B]D|T[O]DO|待[定]|占[位]" docs/architecture/resume-takeover-recovery-gate-design.md docs/user-stories/development-engineer/de-005-resume-takeover.md plans/resume-takeover-recovery-gate-plan-v1.md
```

Expected:

- 第一条没有把 `takeover_resumed` 或固定 `continue_development` 描述为新恢复输出。
- 第二条无结果。

- [x] **Step 9: 检查本任务变更**

Run:

```sh
git diff --check
git status --short
```

Expected: 仅计划列出的文件变化；不提交。

---

### Task 7: 全量验证和实现复核

**Files:**
- Review only: 本计划列出的全部文件

**Interfaces:**
- Consumes: Tasks 1-6 的完整变更。
- Produces: 可交付但未提交的本地实现和验证证据。

- [x] **Step 1: 运行格式化**

Run:

```sh
gofmt -w packages/agentic-cli/internal/runcontext/context.go packages/agentic-cli/internal/runcontext/context_test.go packages/agentic-cli/internal/jira/resume_gate.go packages/agentic-cli/internal/jira/resume_gate_test.go packages/agentic-cli/internal/clihandlers/resume_feedback.go packages/agentic-cli/internal/clihandlers/resume_feedback_test.go packages/agentic-cli/internal/clihandlers/task.go packages/agentic-cli/internal/clihandlers/evidence_context.go packages/agentic-cli/internal/clihandlers/repo_paths.go packages/agentic-cli/internal/cli/task_command_test.go
```

Expected: 命令成功。

- [x] **Step 2: 运行全部 Go 测试**

Run:

```sh
go test ./...
```

Expected: PASS。

- [x] **Step 3: 验证发布构建**

Run:

```sh
bash scripts/test-build.sh
```

Expected: PASS，四个平台目标均可构建，安装二进制与当前源码一致。

- [x] **Step 4: 验证资源和 fake 主链**

Run:

```sh
bash scripts/test-resources.sh
bash tests/e2e/local-fake-flow.sh
```

Expected: PASS。

- [x] **Step 5: 执行研发期结构检查**

Run:

```sh
git status --short
find . -maxdepth 3 -type f
git diff --check
```

Expected:

- 变更文件均在本计划范围内。
- 没有 secrets、token、原始敏感日志、计划外二进制或临时测试产物。
- 无空白错误。

- [x] **Step 6: 对照设计逐项复核**

人工核对：

- `resume-takeover` 没有 Jira 写调用。
- real 模式重新读取 issue 和 current user。
- `agent_binding_lost` 不自动重绑。
- `target_repo_changed` 不替换历史仓库。
- 成功恢复保留 stage 和 next action。
- 操作契约与 Standard Process Registry 分层校验。
- 任务级阻塞生成安全评论文件。
- 失去所有权时不返回可直接写 Jira 的动作。
- `write-evidence` 和 Jira 原子写操作使用统一运行上下文。

- [x] **Step 7: 输出本地交付摘要**

向研发工程师报告：

- 变更摘要和关键行为。
- 变更文件清单。
- 所有验证命令及结果。
- 未执行真实 Jira 写入。
- 未提交、未推送。
- 剩余风险或未覆盖场景。

---

## Plan Self-Review

- Spec coverage: 组件边界、双层阶段、所有权、仓库、失败码、Jira 反馈、输出契约和测试策略均有对应任务。
- Scope: 计划没有扩张到通用工作流引擎、自动 Jira 写入或评论幂等系统。
- TDD: 每个运行行为任务都先写失败测试，再实现，再运行定向和全量测试。
- Type consistency: `runcontext.Context`、`jira.ResumeInput`、`jira.ResumeDecision` 和 `resumeFeedback` 的字段在后续任务中保持一致。
- Project rules: 计划使用顶层 `plans/`，不创建 `docs/superpowers/`，不自动提交或推送。
