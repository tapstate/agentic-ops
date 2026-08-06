# 工作项级连续执行授权实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将“设计确认后连续推进到拉取请求审查”固化为可审计、可验证的 AgenticOps 标准研发流程。

**Architecture:** 保留现有高风险 gate 的 `required` 语义，在默认策略中新增工作项授权范围注册表，并由操作契约和标准流程引用同一授权。人读规则负责解释授权边界与停止条件，机器可读策略负责声明覆盖动作、绑定事实和失效事实，资源测试与 Go 单元测试共同阻止标准漂移。

**Tech Stack:** Markdown、YAML、Bash、Go、Git、Jira、GitHub Pull Request。

## Global Constraints

- 设计或修复计划必须先经研发工程师确认，确认事实必须可从 Jira 或项目配置的等价任务事实源回读。
- 一次授权只绑定一个 `issue_key`、`agentic_run_id`、仓库、工作分支、目标分支和已确认范围。
- 授权覆盖实现、验证、提交、任务分支推送、必要 Jira 回写以及创建或更新拉取请求。
- 合并、发布、Git Tag、直接修改受保护分支、强推、历史改写和范围变化不在授权范围内。
- 授权失效、必要验证无法完成或出现专业取舍时必须停止。
- 不新增自动合并、自动发布或绕过现有 CLI 显式确认参数的能力。
- 正式设计保存在 `docs/architecture/`，可执行计划保存在顶层 `plans/`，不得创建 `docs/superpowers/`。

---

### Task 1: 用失败测试锁定工作项授权策略模型

**Files:**

- Modify: `packages/agentic-cli/internal/policy/model.go`
- Modify: `packages/agentic-cli/internal/policy/validator.go`
- Modify: `packages/agentic-cli/internal/policy/validator_test.go`
- Modify: `packages/agentic-cli/internal/policy/gate.go`
- Modify: `packages/agentic-cli/internal/policy/gate_test.go`
- Modify: `install-resources/basic/policies/default.yaml`

**Interfaces:**

- Consumes: 现有 `Policy.Gates` 和 `RequiresHumanGate`。
- Produces: `Policy.AuthorizationScopes`、`AuthorizationScope`、`AuthorizationScopeForOperation`；既有 gate 的 `required` 值保持不变。

- [x] **Step 1: 为默认策略授权范围编写失败测试**

在 `validator_test.go` 增加测试，要求默认策略存在 `task_execution`：

```go
func TestValidateAcceptsTaskExecutionAuthorizationScope(t *testing.T) {
	p, err := LoadFile(filepath.Join("..", "..", "..", "..", "install-resources", "basic", "policies", "default.yaml"))
	if err != nil {
		t.Fatalf("LoadFile error = %v", err)
	}
	scope, ok := p.AuthorizationScopes["task_execution"]
	if !ok {
		t.Fatal("task_execution authorization scope is missing")
	}
	if scope.ConfirmationSource != "jira_decision" {
		t.Fatalf("confirmation source = %q", scope.ConfirmationSource)
	}
	for _, operation := range []string{"git_commit", "git_push", "write_jira_comment", "create_pr", "update_pr"} {
		if _, ok := AuthorizationScopeForOperation(p, operation); !ok {
			t.Fatalf("operation %s is not covered", operation)
		}
	}
}
```

在 `gate_test.go` 增加测试，证明授权复用不会关闭原 gate：

```go
func TestTaskAuthorizationDoesNotDisableHumanGate(t *testing.T) {
	p := Policy{
		Gates: map[string]Gate{"git_push": {Required: true}},
		AuthorizationScopes: map[string]AuthorizationScope{
			"task_execution": {CoveredOperations: []string{"git_push"}},
		},
	}
	if !RequiresHumanGate(p, "git_push") {
		t.Fatal("git_push must remain human gated")
	}
	if scope, ok := AuthorizationScopeForOperation(p, "git_push"); !ok || scope != "task_execution" {
		t.Fatalf("authorization scope = %q, %v", scope, ok)
	}
}
```

- [x] **Step 2: 运行测试并确认失败**

Run: `go test ./packages/agentic-cli/internal/policy`

Expected: FAIL，提示 `AuthorizationScopes`、`AuthorizationScope` 或 `AuthorizationScopeForOperation` 尚不存在。

- [x] **Step 3: 实现最小策略模型和查询函数**

在 `model.go` 增加：

```go
type Policy struct {
	Policy              string                        `yaml:"policy"`
	Version             int                           `yaml:"version"`
	Gates               map[string]Gate               `yaml:"gates"`
	AuthorizationScopes map[string]AuthorizationScope `yaml:"authorization_scopes"`
}

type AuthorizationScope struct {
	ConfirmationSource string   `yaml:"confirmation_source"`
	RequiredBindings   []string `yaml:"required_bindings"`
	CoveredOperations  []string `yaml:"covered_operations"`
	ExcludedOperations []string `yaml:"excluded_operations"`
	InvalidatedBy      []string `yaml:"invalidated_by"`
}
```

在 `gate.go` 增加纯查询函数：

```go
func AuthorizationScopeForOperation(p Policy, operation string) (string, bool) {
	for name, scope := range p.AuthorizationScopes {
		for _, covered := range scope.CoveredOperations {
			if covered == operation {
				return name, true
			}
		}
	}
	return "", false
}
```

- [x] **Step 4: 在默认策略声明授权范围**

在 `default.yaml` 保留所有现有 `required` 值，并增加：

```yaml
authorization_scopes:
  task_execution:
    confirmation_source: jira_decision
    required_bindings:
      - issue_key
      - agentic_run_id
      - agent_id
      - agentic_id
      - target_repo
      - work_branch
      - base_branch
      - approved_plan_version
      - approved_scope
      - verification_method
    covered_operations:
      - git_commit
      - git_push
      - write_jira_comment
      - create_pr
      - update_pr
    excluded_operations:
      - git_merge
      - release
      - git_tag
      - protected_branch_push
      - force_push
      - history_rewrite
      - scope_change
    invalidated_by:
      - ownership_changed
      - binding_mismatch
      - scope_changed
      - risk_increased
      - verification_blocked
      - repeated_failure
      - ambiguous_external_write
```

- [x] **Step 5: 校验授权范围完整性**

在 `validator.go` 增加稳定错误码：

- `missing_authorization_scopes`
- `invalid_authorization_scope`

校验 `task_execution` 的确认来源、绑定事实、覆盖动作、禁止动作和失效条件都非空；同时拒绝同一个动作同时出现在 `covered_operations` 和 `excluded_operations`。

- [x] **Step 6: 运行策略测试**

Run: `go test ./packages/agentic-cli/internal/policy`

Expected: PASS。

- [x] **Step 7: 提交策略模型**

```bash
git add \
  packages/agentic-cli/internal/policy/model.go \
  packages/agentic-cli/internal/policy/validator.go \
  packages/agentic-cli/internal/policy/validator_test.go \
  packages/agentic-cli/internal/policy/gate.go \
  packages/agentic-cli/internal/policy/gate_test.go \
  install-resources/basic/policies/default.yaml
git commit \
  -m "Feat(policy): AO-2 支持工作项连续执行授权范围" \
  -m "为默认策略增加可验证的 task_execution 授权范围，绑定任务事实并声明覆盖动作、禁止动作和失效条件。

保留现有高风险 gate 的 required 语义，不引入自动合并、自动发布或无人工确认执行。"
```

### Task 2: 将授权语义写入标准流程和操作契约

**Files:**

- Modify: `install-resources/basic/contracts/processes/development-change-v1.yaml`
- Modify: `install-resources/basic/contracts/processes/agenticops-improvement-v1.yaml`
- Modify: `install-resources/basic/contracts/operations/add-task-comment.yaml`
- Modify: `install-resources/basic/contracts/operations/prepare-pr.yaml`
- Modify: `install-resources/basic/contracts/operations/write-pr-evidence.yaml`
- Modify: `docs/contracts/operation-contract.md`

**Interfaces:**

- Consumes: 策略范围 `task_execution`。
- Produces: `execution_authorization`、`authorization_reference`、`fixed_head_sha` 和 `pr_review` 标准节点。

- [x] **Step 1: 先增加资源合同失败断言**

在 `scripts/test-resources.sh` 增加：

```bash
grep 'authorization_scopes:' install-resources/basic/policies/default.yaml >/dev/null
grep 'task_execution:' install-resources/basic/policies/default.yaml >/dev/null
grep 'execution_authorization' install-resources/basic/contracts/processes/development-change-v1.yaml >/dev/null
grep 'execution_authorization' install-resources/basic/contracts/processes/agenticops-improvement-v1.yaml >/dev/null
grep 'authorization_reference' install-resources/basic/contracts/operations/add-task-comment.yaml >/dev/null
grep 'authorization_scope' install-resources/basic/contracts/operations/prepare-pr.yaml >/dev/null
grep 'fixed_head_sha' install-resources/basic/contracts/operations/prepare-pr.yaml >/dev/null
grep 'authorization_reference' install-resources/basic/contracts/operations/write-pr-evidence.yaml >/dev/null
```

- [x] **Step 2: 运行资源测试并确认失败**

Run: `bash scripts/test-resources.sh`

Expected: FAIL，首个尚未实现的授权合同断言返回非零。

- [x] **Step 3: 更新两个标准流程**

在 `implementation.output_fields` 增加：

```yaml
      - execution_authorization
      - authorization_reference
```

增加拉取请求审查阶段：

```yaml
  - id: pr_review
    responsible_role: development_engineer
    input_fields:
      - execution_authorization
      - authorization_reference
      - implementation_summary
      - verification_result
      - residual_risk
      - pr_url
      - fixed_head_sha
    output_fields:
      - review_decision
      - agentic_next_action
    review_gate: development_engineer_review
```

- [x] **Step 4: 更新三个操作契约**

- `add-task-comment.yaml`：增加可选 `authorization_reference` 输入；前置条件改为真实 Jira 写入已由当前动作确认或由有效 `task_execution` 授权覆盖。
- `prepare-pr.yaml`：输出 `authorization_scope`、`authorization_reference`、`fixed_head_sha`；仍保持 `must_not_push_git` 和 `must_not_create_pr`。
- `write-pr-evidence.yaml`：增加 `authorization_reference` 输入和输出；前置条件要求授权仍有效或当前动作已经独立确认。

操作契约必须明确：复用授权只表示人工确认已存在，不能跳过所有权、策略、输入、幂等和事实回读门禁。

- [x] **Step 5: 更新人读操作契约**

在 `docs/contracts/operation-contract.md` 增加“工作项级连续执行授权”章节，写明：

- 每个高风险操作仍声明 `human_gate.required: true` 或等价策略 gate。
- 操作可以消费同一份有效 `task_execution` 授权，不再重复询问。
- 操作必须回读授权绑定事实和失效条件。
- 无授权记录的旧任务继续逐项确认。
- 合并、发布、Tag、强推、历史改写和范围变化不能消费该授权。

- [x] **Step 6: 运行合同和资源测试**

Run: `go test ./packages/agentic-cli/internal/contract ./packages/agentic-cli/internal/process`

Expected: PASS。

Run: `bash scripts/test-resources.sh`

Expected: 如果 checksums 尚未更新，只因 `install-resources/checksums.txt` 漂移失败；其它新增合同断言通过。

- [x] **Step 7: 提交流程和契约**

```bash
git add \
  scripts/test-resources.sh \
  install-resources/basic/contracts/processes/development-change-v1.yaml \
  install-resources/basic/contracts/processes/agenticops-improvement-v1.yaml \
  install-resources/basic/contracts/operations/add-task-comment.yaml \
  install-resources/basic/contracts/operations/prepare-pr.yaml \
  install-resources/basic/contracts/operations/write-pr-evidence.yaml \
  docs/contracts/operation-contract.md
git commit \
  -m "Docs(workflow): AO-2 固化连续执行授权契约" \
  -m "把工作项授权引用、拉取请求固定 HEAD 和 PR 审查节点写入标准流程与操作契约。

保留原子操作的所有权、策略、幂等和事实回读门禁，未授权旧任务继续逐项确认。"
```

### Task 3: 同步人读标准、决策记录和证据模板

**Files:**

- Modify: `AGENTS.md`
- Modify: `install-resources/basic/company/standards/core-hard-rules.md`
- Modify: `install-resources/basic/handbooks/ai-employee-handbook.md`
- Modify: `docs/project-rules.md`
- Modify: `docs/architecture/agenticops-current-design.md`
- Modify: `docs/development-engineers/getting-started.md`
- Modify: `docs/templates/evidence-templates.md`
- Modify: `docs/decision-log.md`

**Interfaces:**

- Consumes: 设计文档和 `task_execution` 策略范围。
- Produces: 研发工程师自然语言授权方式、AIAgent 停止条件、授权记录模板和 PR 审查包模板。

- [x] **Step 1: 增加人读标准失败断言**

在 `scripts/test-resources.sh` 增加：

```bash
grep '工作项级连续执行授权' install-resources/basic/company/standards/core-hard-rules.md >/dev/null
grep '工作项级连续执行授权' install-resources/basic/handbooks/ai-employee-handbook.md >/dev/null
grep '工作项级连续执行授权' docs/project-rules.md >/dev/null
grep '工作项级连续执行授权' docs/architecture/agenticops-current-design.md >/dev/null
grep '拉取请求审查包' docs/templates/evidence-templates.md >/dev/null
grep 'D-033' docs/decision-log.md | grep '连续执行授权' >/dev/null
```

- [x] **Step 2: 运行资源测试并确认失败**

Run: `bash scripts/test-resources.sh`

Expected: FAIL，首个尚未同步的人读标准断言返回非零。

- [x] **Step 3: 更新公司规则和 AI 员工手册**

公司规则明确：高风险动作必须先确认；同一工作项已有可回读、未失效的授权时，后续覆盖动作不重复询问。手册增加：

- 计划确认即建立授权窗口。
- 连续执行覆盖动作。
- 授权绑定事实和失效条件。
- 普通可恢复失败自动处理。
- 正常结束统一停在 PR 审查。
- 合并和发布仍独立确认。

- [x] **Step 4: 更新项目规则、当前设计和 AGENTS**

把主链路更新为：

```text
确认版本化设计或修复计划并授予工作项级连续执行授权
-> 实现、验证、提交、推送、必要 Jira 回写和创建 PR
-> 输出拉取请求审查包并暂停
```

明确源头仓库维护也可以在研发工程师明确授权的任务范围内连续提交、推送和创建 PR，但不能直接修改 `main`，不能把工作项授权扩展到发布或 Tag。

- [x] **Step 5: 更新研发工程师指南和证据模板**

增加推荐自然语言：

```text
确认该设计，并授权在当前 Jira 工作项、仓库、任务分支、目标分支和验证范围内连续推进到拉取请求审查；范围或风险变化时停下。
```

增加“工作项连续执行授权”和“拉取请求审查包”模板，必须包含授权引用、固定 Head SHA、验证结果、CI 事实、Jira 回写引用、残留风险和下一步人工动作。

- [x] **Step 6: 记录长期设计决策**

在 `docs/decision-log.md` 增加：

```text
D-033 | 设计确认形成工作项级连续执行授权 | 授权绑定 Jira、运行、仓库、分支、范围和验证事实，覆盖实现到创建或更新 PR；统一停在 PR 审查。合并、发布、Tag、范围变化和授权失效仍单独确认。
```

同时从“当前无需决策事项”删除“是否允许低风险任务自动推送或自动创建拉取请求”，因为本设计不是按低风险自动放行，而是已形成明确的工作项授权机制。

- [x] **Step 7: 运行资源测试**

Run: `bash scripts/test-resources.sh`

Expected: 除 checksums 漂移外，新增语义断言全部通过。

- [x] **Step 8: 提交人读标准**

```bash
git add \
  AGENTS.md \
  install-resources/basic/company/standards/core-hard-rules.md \
  install-resources/basic/handbooks/ai-employee-handbook.md \
  docs/project-rules.md \
  docs/architecture/agenticops-current-design.md \
  docs/development-engineers/getting-started.md \
  docs/templates/evidence-templates.md \
  docs/decision-log.md \
  scripts/test-resources.sh
git commit \
  -m "Docs(workflow): AO-2 采用工作项连续执行流程" \
  -m "统一公司规则、AI 员工手册、项目规则、当前设计、研发指南、证据模板和长期决策记录。

设计确认后连续推进到 PR 审查；授权失效、合并、发布和范围变化仍停下处理。"
```

### Task 4: 更新资源校验和并执行完整验证

**Files:**

- Modify: `install-resources/checksums.txt`
- Modify: `plans/task-level-continuous-execution-authorization-plan.md`
- Modify: `plans/v0.3-ao-pilot-and-v0.4-planning-plan.md`

**Interfaces:**

- Consumes: 前三项全部实现。
- Produces: 可复查的完整验证记录和已完成计划状态。

- [x] **Step 1: 更新安装资源校验和**

Run: `bash scripts/update-checksums.sh`

Expected: `install-resources/checksums.txt` 只因本计划修改的 `install-resources/basic/` 文件发生对应变化。

- [x] **Step 2: 执行完整验证**

Run: `go test ./...`

Expected: PASS。

Run: `bash scripts/test-resources.sh`

Expected: 输出 `{"ok":true,"operation":"test_resources"}`。

Run: `bash scripts/test-release-workflow.sh`

Expected: 53 个用例全部通过。

Run: `git diff --check origin/develop...HEAD`

Expected: 无输出，退出码为 0。

- [x] **Step 3: 回读关键约束**

确认：

- `git_push`、`create_pr`、`update_pr`、`git_merge` 和 `scope_change` 的 `required` 值没有被关闭。
- `task_execution` 只覆盖确认的连续动作。
- 合并、发布、Tag、强推、历史改写和范围变化均在排除列表中。
- 流程正常结束于 `pr_review`，不直接进入合并或完成状态。
- 所有 commit body 使用真实换行，不含字面量 `\n`。

- [x] **Step 4: 更新计划执行状态和 AO-2 记录**

把本计划已完成步骤标记为 `[x]`；在 `plans/v0.3-ao-pilot-and-v0.4-planning-plan.md` 记录新增设计、策略模型、标准资产、验证结果、提交和后续 PR 事实，删除已经完成的“待推送”表述。

- [x] **Step 5: 提交校验和与执行记录**

```bash
git add \
  install-resources/checksums.txt \
  plans/task-level-continuous-execution-authorization-plan.md \
  plans/v0.3-ao-pilot-and-v0.4-planning-plan.md
git commit \
  -m "Docs(plan): AO-2 记录连续执行流程验证结果" \
  -m "更新安装资源校验和与 AO-2 执行记录，确认策略、契约、文档和完整测试结果一致。

后续在同一授权窗口内推送任务分支、回写 Jira 并创建 develop PR。"
```

### Task 5: 推送、回写 Jira 并创建拉取请求

**Files:**

- Read: `docs/architecture/task-level-continuous-execution-authorization-design.md`
- Read: `plans/task-level-continuous-execution-authorization-plan.md`
- Read: `plans/v0.3-ao-pilot-and-v0.4-planning-plan.md`

**Interfaces:**

- Consumes: 当前 AO-2 连续执行授权和完整验证结果。
- Produces: 远端任务分支、AO-2 中文证据评论和目标为 `develop` 的拉取请求。

- [x] **Step 1: 执行推送前事实检查**

检查工作区干净、当前分支为 `harsen/AO-2/develop`、上游为同名远端分支、`origin/develop` 可达且没有开放的重复 PR。

- [x] **Step 2: 推送任务分支**

Run: `git push origin harsen/AO-2/develop`

Expected: 远端分支 HEAD 与本地 HEAD 完全一致。

- [x] **Step 3: 回写 AO-2 推送与授权证据**

写入一条中文 Jira 评论，说明工作项级授权、标准资产变更、验证结果和下一步 PR 审查；写入后按评论 ID 回读。失败时只重试 Jira 评论，不重复推送。

- [x] **Step 4: 创建或复用 develop 拉取请求**

Title: `Docs(workflow): AO-2 采用工作项连续执行流程`

Base: `develop`

Head: `harsen/AO-2/develop`

PR 正文必须包含授权边界、全部变更、提交、验证、CI 事实、固定 Head SHA、残留风险和明确非范围。

- [x] **Step 5: 回读并输出拉取请求审查包**

回读 PR URL、状态、Base、Head、固定 Head SHA、可合并状态、check runs、commit statuses、Reviews、普通评论和行级评论；把 PR 证据写入 AO-2 后按评论 ID 回读。然后暂停，等待研发工程师审查和人工 Merge commit。

执行记录：AO-2 工作项级连续执行授权已写入 Jira 评论 `46524`，推送总结已写入评论 `46525`，两条评论均回读一致。任务分支首次推送后远端 HEAD 与本地 `97f38bf5f02a95a5d3bfa7a21f2cd9e8cf16614d` 一致；PR #4 已创建为 `harsen/AO-2/develop -> develop`，状态开放、非草稿、可合并且 `mergeable_state=clean`。首次 PR 回读没有 check run、commit status、Review、普通评论或行级评论，不能声称 CI 通过。本执行记录提交推送后，以 PR #4 和 AO-2 最终 PR 证据评论回读的 Head SHA 作为固定审查事实。
