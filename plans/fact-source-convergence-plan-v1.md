# AgenticOps 事实源收敛实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 AgenticOps 当前运行文档、实施计划和项目长期记忆与可执行源码、命令注册、机器可读契约、安装资源和自动化测试保持一致。

**Architecture:** 以源码和命令注册为能力入口证据，以测试和机器可读契约为行为边界证据，逐层校正当前运行文档、当前差距计划、历史实施计划和长期记忆。只修改状态表达和文档责任边界，不新增运行能力，不恢复缺失的发布脚本。

**Tech Stack:** Markdown、Git、`rg`、Go 1.22+ 测试、现有 shell 验证脚本。

## Global Constraints

- 遵循 `docs/architecture/fact-source-convergence-design.md` 的事实判定顺序和状态分类。
- 当前运行能力必须能从源码、命令注册、机器可读契约或自动化测试中找到证据。
- 历史错误结论必须显式撤销，不能静默删除。
- shell 不承载 Jira、GitHub、Git、操作契约、策略门禁、证据、反馈或发布业务逻辑。
- 不修改 Go 源码、机器可读操作契约、安装资源、平台二进制和发布逻辑。
- 不执行真实 Jira 或 GitHub 写操作。
- 不自动提交或推送；完成本地修改和验证后等待研发工程师审阅。

---

### Task 1: 校正 AgenticCLI Git / GitHub 当前能力描述

**Files:**
- Modify: `docs/runtime/cli-runtime.md:36`

**Interfaces:**
- Consumes: `packages/agentic-cli/internal/commandcatalog/zz_generated.go` 中的注册命令，以及 `packages/agentic-cli/internal/git/`、`packages/agentic-cli/internal/github/` 的现有实现。
- Produces: 面向维护者的当前运行时边界；不作为阶段性进度清单。

- [x] **Step 1: 记录修改前的不一致**

Run:

```sh
rg -n 'git.*github.*后续阶段|真实集成目录仍属于后续阶段' docs/runtime/cli-runtime.md
```

Expected: 命中将 `git`、`github` 目录描述为后续阶段的当前文本。

- [x] **Step 2: 核对已注册的受控命令**

Run:

```sh
rg -n 'inspect_workspace|prepare_pr|read_pr_comments|fix_pr_comments|check_ci_status' packages/agentic-cli/internal/commandcatalog/zz_generated.go
```

Expected: 五个命令都存在于生成的命令注册表。

- [x] **Step 3: 修改运行时目录说明**

把 `docs/runtime/cli-runtime.md` 的目录状态说明改为以下语义：

```markdown
以上目录用于表达稳定职责边界，不是阶段性进度清单。当前已实现 `git` 工作区只读检查、拉取请求准备计划、GitHub 拉取请求评论和 CI 状态读取、评论修复计划等受控基线。Git 推送、创建或更新拉取请求、合并和发布等高风险副作用仍必须经过策略门禁、人工确认和审计；未具备受控写入操作时不得由 AIAgent 或 shell 绕过。
```

保留机器可读操作契约唯一源头和 shell 业务边界说明。

- [x] **Step 4: 验证当前能力与未开放副作用被明确区分**

Run:

```sh
rg -n '当前已实现.*Git|高风险副作用|真实集成目录仍属于后续阶段' docs/runtime/cli-runtime.md
```

Expected: 命中当前已实现能力和高风险副作用边界，不再命中“真实集成目录仍属于后续阶段”。

- [x] **Step 5: 检查本任务差异**

Run:

```sh
git diff --check -- docs/runtime/cli-runtime.md
git diff -- docs/runtime/cli-runtime.md
```

Expected: `git diff --check` 无输出；差异只修改目录状态说明。

### Task 2: 把设计实现差距计划恢复为当前跟踪入口

**Files:**
- Modify: `plans/design-implementation-gap-todo-v1.md:1`

**Interfaces:**
- Consumes: `install-resources/basic/contracts/`、`install-resources/basic/projects/`、`install-resources/basic/policies/`、`packages/agentic-cli/internal/` 和 `tests/` 的当前状态。
- Produces: 当前仍可执行的实现差距和待决策事项。

- [x] **Step 1: 记录计划中的矛盾和迁移前路径**

Run:

```sh
rg -n '没有未勾选项|目录尚未实现|尚无 CLI 路由|scripts/publish-release.sh|(^|`)contracts/|(^|`)profiles/|(^|`)assets/' plans/design-implementation-gap-todo-v1.md
```

Expected: 命中错误总览、Git / GitHub 旧状态、缺失发布脚本和迁移前资源路径。

- [x] **Step 2: 修正计划头部和现有计划状态**

将计划头部改为以下语义：

```markdown
**Architecture:** 本计划是当前设计与实现差距的跟踪入口。已完成任务保留验证过的实现基线；部分实现任务同时列出已完成边界和剩余缺口；涉及产品、流程、权限或事实源取舍的事项保持待决策状态。历史计划只用于追溯，不作为当前能力清单。
```

把“当前 `plans/` 中没有未勾选项”改为带日期的核对结论：

```markdown
截至 2026-07-28，本计划仍包含真实 Jira 恢复校验、更新回滚与兼容治理、受控发布和反馈分析等未完成项。计划状态必须以本文件勾选项和当前仓库验证结果为准。
```

已有计划文件列表增加：

```markdown
- `plans/fact-source-convergence-plan-v1.md`
```

- [x] **Step 3: 重新标定 Task 1 到 Task 5 的当前状态**

按现有勾选项和源码证据修改各任务的 `Current gap`：

- Task 1：本地 run、workspace 和基础状态恢复校验已实现；保留缺口是真实 Jira 模式重新读取卡片并复核 `assignee`、`agentic_id`，恢复并校验 `target_repo`，以及按 Standard Process Registry 判断当前阶段是否允许恢复。
- Task 2：证据模板、策略门禁、完成证据和任务级审计基线已实现；本任务标记为已完成基线，不再保留修改前的缺口陈述。
- Task 3：process loader、任务分类、仓库 fallback 和 process/profile 一致性校验已实现；本任务标记为已完成基线。
- Task 4：Git / GitHub 只读检查、拉取请求计划、评论和 CI 读取、策略门禁已实现；`write-pr-evidence` 只有机器可读契约，没有 CLI 注册入口。自动 push、创建拉取请求和 merge 等副作用也未开放。
- Task 5：preflight 和 doctor 的本地及显式外部检查基线已实现；本任务标记为已完成基线。

Task 4 的 `Files` 不再使用 `Create` 描述已经存在的目录和契约，改为当前实现证据：

```markdown
**Implementation evidence:**
- `packages/agentic-cli/internal/git/`
- `packages/agentic-cli/internal/github/`
- `install-resources/basic/contracts/operations/inspect-workspace.yaml`
- `install-resources/basic/contracts/operations/prepare-pr.yaml`
- `install-resources/basic/contracts/operations/read-pr-comments.yaml`
- `install-resources/basic/contracts/operations/fix-pr-comments.yaml`
- `install-resources/basic/contracts/operations/check-ci-status.yaml`
```

另列契约与运行入口缺口：

```markdown
**Contract-only gap:**
- `install-resources/basic/contracts/operations/write-pr-evidence.yaml` 已存在，但命令注册表中没有 `write-pr-evidence`。
- 需要确认该契约应新增 CLI 入口，还是由现有 `write-evidence` 统一承载拉取请求证据后删除重复契约。
```

- [x] **Step 4: 修正所有当前资源路径**

在本计划中使用以下当前路径：

```text
install-resources/basic/contracts/operations/
install-resources/basic/contracts/processes/
install-resources/basic/projects/tapdata/profile.yaml
install-resources/basic/policies/default.yaml
install-resources/basic/templates/
install-resources/basic/manifest.json
```

不得继续把仓库根目录下不存在的 `contracts/`、`profiles/` 或 `assets/` 写成当前文件入口。

- [x] **Step 5: 重新定义 Task 6 到 Task 8 的剩余状态**

保持 Task 6 的更新回滚与兼容治理为待实现，但把发布权限和最低兼容承诺依赖标记为待决策。

将 Task 7 的 `Current gap` 改为：

```markdown
**Current gap:**
- 当前仓库不存在可用的 `scripts/publish-release.sh`，历史计划中的完成结论已撤销。
- 当前不存在受控 `agentic-cli release publish` 操作、发布权限策略、人工确认记录、发布审计事件和回滚说明。
- 发布属于高风险动作，必须先确认发布责任人、授权方式、审计位置和回滚责任，再进入实现。
- shell 只能作为轻量调用包装，不能直接承载 GitHub 发布业务流程。
```

Task 7 的文件清单删除对缺失脚本的 `Modify`，并把是否需要轻量包装脚本留到治理设计确认后决定；不能在本计划中预设恢复旧脚本。

保持 Task 8 的 `feedback report` 过滤、`feedback analyze`、`feedback propose` 和效果追踪为待实现。

- [x] **Step 6: 更新推进顺序**

将推进顺序改为：

1. 完成 Task 1 剩余的真实 Jira 所有权复核、`target_repo` 恢复和可恢复阶段校验。
2. 在兼容承诺决策后推进 Task 6。
3. 在发布权责和审计位置决策后推进 Task 7。
4. 澄清 Task 4 的 `write-pr-evidence` 契约归属，再新增运行入口或删除重复契约。
5. 推进 Task 8 的反馈分析和改进建议闭环。
6. Task 2、Task 3 和 Task 5 只保留已完成基线和回归验证，不再作为待开发事项。

- [x] **Step 7: 验证当前计划不再自相矛盾**

Run:

```sh
rg -n '没有未勾选项|目录尚未实现|尚无 CLI 路由|scripts/publish-release.sh.*可以|(^|`)contracts/|(^|`)profiles/|(^|`)assets/' plans/design-implementation-gap-todo-v1.md
rg -n '^- \[ \]' plans/design-implementation-gap-todo-v1.md
git diff --check -- plans/design-implementation-gap-todo-v1.md
```

Expected: 第一条无错误当前态命中；第二条只列出 Task 1 的三个恢复缺口、Task 4 的契约归属、Task 6、Task 7、Task 8 和待决策事项；`git diff --check` 无输出。

### Task 3: 把完整设计实现计划标记为历史记录

**Files:**
- Modify: `plans/full-design-implementation-plan-v1.md:1`

**Interfaces:**
- Consumes: 当前仓库不存在 `scripts/publish-release.sh` 的核对结果。
- Produces: 可追溯但不会被误读为当前能力状态的历史实施计划。

- [x] **Step 1: 增加历史计划声明**

在标题后加入：

```markdown
> **历史计划说明（2026-07-28）：** 本文件保留完整设计实施过程和当时的完成记录，不作为当前能力清单。当前实现差距以 `plans/design-implementation-gap-todo-v1.md` 为准；事实判定规则见 `docs/architecture/fact-source-convergence-design.md`。
```

- [x] **Step 2: 显式撤销错误发布完成结论**

把 `## 10. GitHub Release Publish Baseline` 改为“GitHub Release 发布基线历史纠正”，删除三个完成勾选，保留以下核对记录：

```markdown
2026-07-28 核对当前仓库后确认：

- 当前不存在 `scripts/publish-release.sh`。
- `scripts/test-build.sh` 只验证安装资源构建和校验和，不验证 GitHub Release 发布。
- 因此本节原有“发布脚本、创建或更新 GitHub Release、无网络发布测试均已完成”的结论无有效实现支撑，完成状态撤销。
- 受控发布继续由 `plans/design-implementation-gap-todo-v1.md` Task 7 跟踪，并等待发布权责和审计位置决策。
```

- [x] **Step 3: 验证历史和当前状态边界**

Run:

```sh
rg -n '历史计划说明|发布基线历史纠正|完成状态撤销|Added `scripts/publish-release.sh`' plans/full-design-implementation-plan-v1.md
git diff --check -- plans/full-design-implementation-plan-v1.md
```

Expected: 命中历史声明和撤销说明，不再命中英文完成声明；`git diff --check` 无输出。

### Task 4: 校正 AgenticOps 项目长期记忆

**Files:**
- Modify: `/Users/lhs/wiki/30-projects/agentic-ops.md`

**Interfaces:**
- Consumes: 当前仓库源码、命令注册、测试和本次文档核对结论。
- Produces: 后续任务可复用的稳定项目阶段和发布边界记忆。

- [x] **Step 1: 修正顶部当前阶段摘要**

将“当前只接 fake Jira、本地 evidence 和本地 feedback”改为当前稳定边界：

```markdown
- 当前保留 fake Jira 本地回归入口，同时已具备显式激活的真实 Jira 读取与受控写入基线；真实字段写入、评论和 transition 必须经过配置、所有权校验、策略门禁和显式确认。
- Git / GitHub 已具备工作区只读检查、拉取请求准备计划、审查评论与 CI 状态读取等受控基线；自动 push、创建或更新拉取请求、merge 和发布仍未开放。
```

命令摘要不维护容易过时的完整枚举，改为说明核心主链和命令注册表是当前命令证据。

- [x] **Step 2: 撤销发布脚本错误记忆**

把 `2026-07-23 GitHub release publish baseline` 小节改为带当前核对日期的历史纠正：

```markdown
## 2026-07-28 GitHub release publish baseline correction

- 当前仓库不存在此前记忆声明的 `scripts/publish-release.sh` 和对应发布测试，原“GitHub Release 发布基线已完成”结论失效。
- `scripts/test-build.sh` 只验证安装资源构建与校验和，不能作为发布流程证据。
- 受控发布仍是待决策、待实现能力；发布责任人、授权方式、审计位置和回滚责任确认前，不恢复旧 shell 发布路径。
```

- [x] **Step 3: 验证长期记忆与当前仓库一致**

Run:

```sh
rg -n '当前只接 fake Jira|已新增 `scripts/publish-release.sh`|publish baseline correction|受控发布仍是待决策' /Users/lhs/wiki/30-projects/agentic-ops.md
```

Expected: 不再命中旧当前态声明，命中 2026-07-28 历史纠正。

### Task 5: 执行全局一致性和回归验证

**Files:**
- Modify: `plans/fact-source-convergence-plan-v1.md`
- Verify only: `README.md`
- Verify only: `docs/`
- Verify only: `plans/`
- Verify only: `install-resources/basic/`
- Verify only: `packages/agentic-cli/`
- Verify only: `scripts/`
- Verify only: `tests/`

**Interfaces:**
- Consumes: Task 1 到 Task 4 的文档和记忆修改。
- Produces: 可复核的完成勾选、验证结果和干净的范围边界。

- [x] **Step 1: 检查缺失发布脚本的所有引用**

Run:

```sh
rg -n 'publish-release\.sh' README.md docs plans install-resources/basic packages/agentic-cli scripts tests
```

Expected: 只命中事实收敛设计、事实收敛计划、历史纠正和当前差距说明；不得命中当前可执行入口或已完成声明。

- [x] **Step 2: 检查 Git / GitHub 旧状态和迁移前当前路径**

Run:

```sh
rg -n 'git.*github.*后续阶段|真实集成目录仍属于后续阶段|目录尚未实现|尚无 CLI 路由' docs plans --glob '!fact-source-convergence-plan-v1.md'
rg -n '`(contracts|profiles|assets)/' plans/design-implementation-gap-todo-v1.md
```

Expected: 两条命令都无错误当前态命中。

- [x] **Step 3: 检查常见占位词和 Markdown 差异**

Run:

```sh
fact_source_placeholder_pattern='T[B]D|T[O]DO|待''补充|implement ''later|fill ''in'
rg -n "$fact_source_placeholder_pattern" docs/architecture/fact-source-convergence-design.md plans/fact-source-convergence-plan-v1.md
git diff --check
```

Expected: 占位词检查无输出；`git diff --check` 无输出。

- [x] **Step 4: 运行 Go 全量测试**

Run:

```sh
go test ./...
```

Expected: 所有 Go package 测试通过。

- [x] **Step 5: 验证安装资源**

Run:

```sh
bash scripts/test-resources.sh
```

Expected: 输出 `{"ok":true,"operation":"test_resources"}`。

- [x] **Step 6: 验证本地 fake 主流程**

Run:

```sh
bash tests/e2e/local-fake-flow.sh
```

Expected: 脚本退出码为 0，完成安装、配置、接管、恢复、证据、释放和反馈主流程。

- [x] **Step 7: 检查最终改动范围**

Run:

```sh
find . -maxdepth 3 -type f | sort
git status --short
git diff --stat
```

Expected: 文件清单中没有意外生成物；仓库改动只包含本设计列出的文档和计划；平台二进制、`install-resources/checksums.txt`、源码和安装资源没有变化。

- [x] **Step 8: 更新计划完成状态并停止在提交前**

将本计划已完成步骤逐项勾选，记录实际验证结果。再次运行：

```sh
git diff --check
git status --short
```

Expected: 无格式错误，改动保持未暂存、未提交、未推送，等待研发工程师审阅。
