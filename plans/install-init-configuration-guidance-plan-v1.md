# Install And Init Configuration Guidance Implementation Plan

> **状态：** 历史计划 / 已完成基线（2026-08-01）。本计划记录安装与工作空间初始化配置治理的实施过程，不再作为当前待执行计划。实际实现采用 `install-resources/basic/projects/<project>/profile.yaml` 加工作空间 `.agentic-ops/profile.local.yaml` overlay；当前状态和剩余差距以 `plans/design-implementation-gap-todo-v1.md` 为准。

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 防止安装资源携带维护者本机配置，并让工作空间初始化显式生成和确认研发工程师本地配置。

**Architecture:** 共享 `install-resources/basic/projects/<project>/profile.yaml` 只保存项目标准流程、表单、仓库和模板映射；`workspace init` 在项目 AI 工作空间中写入本地 `profile.local.yaml` overlay、Jira 用户和本地目录。若目标工作空间已有 AgenticOps 本地配置，初始化默认阻断并要求研发工程师通过显式参数确认覆盖。

**Tech Stack:** Go `agentic-cli`、YAML profile、Bash installer、Markdown docs、Go unit tests、shell e2e tests。

## Global Constraints

- `install-resources/basic/` 不得包含维护者本机路径、个人 Jira 用户、secrets、tokens、private keys 或原始敏感日志。
- 标准流程、表单映射、Jira 字段映射、仓库映射、策略、运行手册和模板仍在项目开发时适配完成，并可随安装资源发布。
- 已有本地工作空间配置必须由用户显式确认后才能覆盖。
- 不改变 shell 安装脚本承载边界；shell 只做安装、更新、校验和 PATH 引导。
- 面向用户、研发工程师和审阅者的文档正文使用中文。

---

### Task 1: Workspace Init Local Configuration Materialization

**Files:**
- Modify: `packages/agentic-cli/internal/cli/workspace_agent_test.go`
- Modify: `packages/agentic-cli/internal/clihandlers/workspace.go`
- Modify: `install-resources/basic/projects/tapdata/profile.yaml`
- Modify: `packages/agentic-cli/internal/profile/validator_test.go`

**Interfaces:**
- Consumes: `workspace.Info{Name,Root,RunsDir,RunLogsDir,FeedbackDir}` from `packages/agentic-cli/internal/workspace/workspace.go`.
- Produces: `workspace init --confirm-existing-config`, which permits rewriting existing `.agentic-ops/agent.json`, `.agentic-ops/profile.local.yaml`, or AgenticOps managed `AGENTS.md` block.
- Produces: `prepareWorkspaceProfile(info workspace.Info, jiraUser string, jiraProjectOverride string, sourceRootOverride string) (workspaceProfilePlan, error)` writes a local overlay with paths derived from `info`.

- [x] **Step 1: Write failing tests**

```go
func TestWorkspaceInitMaterializesLocalPathsFromCurrentWorkspace(t *testing.T) {
	root := t.TempDir()
	t.Setenv("AGENTIC_OPS_WORKSPACE_ROOT", root)
	var stdout bytes.Buffer
	var stderr bytes.Buffer
	code := Run([]string{"workspace", "init", "--project", "tapdata", "--jira-user", "lead@example.com"}, &stdout, &stderr)
	if code != 0 {
		t.Fatalf("code = %d stdout = %s stderr = %s", code, stdout.String(), stderr.String())
	}
	data, err := os.ReadFile(filepath.Join(root, ".agentic-ops", "profile.local.yaml"))
	if err != nil {
		t.Fatalf("profile was not materialized: %v", err)
	}
	for _, want := range []string{
		"workspace_root: " + root,
		"source_root: " + filepath.Join(root, "repos", "tapdata"),
		"runs_dir: " + filepath.Join(root, ".agentic-ops", "runs"),
		"run_logs_dir: " + filepath.Join(root, ".agentic-ops", "run-logs"),
		"feedback_dir: " + filepath.Join(root, ".agentic-ops", "feedback"),
	} {
		if !strings.Contains(string(data), want) {
			t.Fatalf("materialized profile missing %s: %s", want, string(data))
		}
	}
}

func TestWorkspaceInitRequiresConfirmationBeforeReplacingExistingConfig(t *testing.T) {
	root := t.TempDir()
	t.Setenv("AGENTIC_OPS_WORKSPACE_ROOT", root)
	Run([]string{"workspace", "init", "--project", "tapdata", "--jira-user", "lead@example.com"}, &bytes.Buffer{}, &bytes.Buffer{})
	var stdout bytes.Buffer
	var stderr bytes.Buffer
	code := Run([]string{"workspace", "init", "--project", "tapdata", "--jira-user", "other@example.com"}, &stdout, &stderr)
	if code != 1 {
		t.Fatalf("code = %d stdout = %s stderr = %s", code, stdout.String(), stderr.String())
	}
	assertJSONField(t, stdout.String(), "code", "existing_config_confirmation_required")
	if !strings.Contains(stdout.String(), "--confirm-existing-config") {
		t.Fatalf("stdout missing confirmation guidance: %s", stdout.String())
	}
}
```

- [x] **Step 2: Run tests to verify failure**

Run: `go test ./packages/agentic-cli/internal/cli -run 'TestWorkspaceInit(MaterializesLocalPathsFromCurrentWorkspace|RequiresConfirmationBeforeReplacingExistingConfig)' -count=1`

Expected: FAIL because `tapdata.yaml` still carries `/Users/lhs/...`, and `workspace init` silently rewrites existing local configuration.

- [x] **Step 3: Implement minimal code and profile cleanup**

```go
confirmExistingConfig := hasFlag(args, "--confirm-existing-config")
if existing := existingWorkspaceConfigPaths(info); len(existing) > 0 && !confirmExistingConfig {
	return writeJSON(stdout, output.FailureWithContext("workspace_init", output.FailureContext{
		Code:                "existing_config_confirmation_required",
		Message:             "工作空间已有 AgenticOps 本地配置",
		RequiredHumanAction: "请确认是否覆盖已有配置；确认后使用 --confirm-existing-config 重新执行 workspace init",
		TaskType:            "workspace_initialization",
		CurrentStage:        "config_confirmation",
		NextAction:          "confirm_existing_config",
		Details:             map[string]any{"existing_config": existing},
	}))
}
```

Then set:

```go
loadedProfile.Jira.User = jiraUser
loadedProfile.Local.WorkspaceRoot = info.Root
loadedProfile.Local.SourceRoot = filepath.Join(info.Root, "repos", info.Name)
loadedProfile.Local.RunsDir = info.RunsDir
loadedProfile.Local.RunLogsDir = info.RunLogsDir
loadedProfile.Local.FeedbackDir = info.FeedbackDir
```

Change shared `install-resources/basic/projects/tapdata/profile.yaml` to use `user: "<jira-user>"` and `<project-ai-workspace>` placeholders for `local.*`; write user-specific values to the workspace overlay.

- [x] **Step 4: Run tests to verify pass**

Run: `go test ./packages/agentic-cli/internal/cli ./packages/agentic-cli/internal/profile -count=1`

Expected: PASS.

### Task 2: Documentation And E2E Alignment

**Files:**
- Modify: `docs/development-engineers/getting-started.md`
- Modify: `docs/profiles/workflow-profile.md`
- Modify: `tests/e2e/local-install-flow.sh`
- Modify: `tests/e2e/local-fake-flow.sh`
- Modify: `tests/e2e/problem-resolution-flow.sh`

**Interfaces:**
- Consumes: `workspace init --confirm-existing-config`.
- Produces: documentation that distinguishes shared standard assets from local user configuration.

- [x] **Step 1: Write failing e2e/doc checks**

Update e2e expectations so repeated initialization uses `--confirm-existing-config`, and add checks that materialized `profile.local.yaml` contains the temporary workspace root instead of `/Users/lhs`.

- [x] **Step 2: Run e2e subset to verify failure**

Run: `bash tests/e2e/local-install-flow.sh`

Expected before implementation alignment: FAIL if local path expectations still point to `/Users/lhs/...`.

- [x] **Step 3: Update docs and e2e scripts**

Document that:

- installation confirms existing global installation before update;
- `workspace init` asks the user to provide Jira user and confirms before replacing existing local config;
- shared project profiles may use placeholders for user and local paths;
- standards, forms, processes and repo mappings remain pre-adapted standard assets.

- [x] **Step 4: Run final verification**

Run:

```sh
git status --short
find . -maxdepth 3 -type f | sort
rg -n "TBD|TODO|待补充|占位|/Users/lhs/works/spaces|harsen@tapdata.io" README.md docs install-resources/basic packages tests
go test ./...
bash scripts/test-install.sh
bash tests/e2e/local-install-flow.sh
bash tests/e2e/local-fake-flow.sh
bash tests/e2e/problem-resolution-flow.sh
```

Expected: Go tests and shell flows pass. The `rg` command may still show legitimate documentation examples for `harsen@tapdata.io`; install resources must not contain that personal value or `/Users/lhs/...`.

## Self-Review

- Spec coverage: plan covers shared profile cleanup, local profile materialization, explicit confirmation for existing config, docs, and e2e checks.
- Placeholder scan: placeholders are intentionally described only as shared profile values; there are no unspecified implementation placeholders.
- Type consistency: new flag name and helper behavior are consistent across tests, implementation and docs.
