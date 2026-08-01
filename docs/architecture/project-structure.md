# AgenticOps 项目结构

## 1. 目的

本文定义 AgenticOps 仓库的目标结构、资料边界和各目录职责。

## 2. 仓库范围

`tapstate/agentic-ops` 是 AgenticOps 的权威源头仓库，管理全局通用资料：

- 源码设计。
- 项目规则。
- AI 员工手册。
- 操作契约。
- 工作流配置。
- Skills。
- Templates。
- 运行资产源头。
- CLI 运行时设计。
- 文档、示例和测试规划。

## 3. 目标结构

```text
agentic-ops/
  README.md
  agent-init.md
  .gitignore
  .githooks/
    pre-commit
    pre-push
  docs/
    maintainers/
      getting-started.md
    development-engineers/
      getting-started.md
    strategy/
      positioning.md
    architecture/
      agenticops-current-design.md
      project-structure.md
    contracts/
      operation-contract.md
    examples/
      end-to-end-demo.md
    profiles/
      workflow-profile.md
    runtime/
      cli-runtime.md
    templates/
      evidence-templates.md
    user-stories/
      agenticops-user-stories.md
      project-maintainer-stories.md
      development-engineer-stories.md
      project-maintainer/
        pm-001-document-boundary.md
        ...
      development-engineer/
        de-001-install.md
        ...
    workflows/
      feedback-loop.md
    review-checklist.md
    decision-log.md
    ai-working-rules.md
    development-style.md
    project-rules.md
  install-resources/
    basic/
      ai-assets/
      handbooks/
      contracts/
      profiles/
      policies/
      runbooks/
      templates/
      manifest.json
    darwin-arm64/
      agentic-cli
    darwin-amd64/
      agentic-cli
    linux-arm64/
      agentic-cli
    linux-amd64/
      agentic-cli
    checksums.txt
  bin/
    .gitkeep
  .local/
    .gitkeep
  .superpowers/
    # local execution state, ignored by Git
  plans/
    implementation-plan-v1.md
  skills/
  packages/
    agentic-cli/
      cmd/
        agentic-cli/
      internal/
        cli/
        config/
        contract/
        evidence/
        feedback/
        git/
        github/
        jira/
        policy/
        workspace/
      testdata/
  examples/
  tests/
  scripts/
    release.sh
    hotfix.sh
    lib/
      development-workflow.sh
      release-common.sh
```

## 4. 目录职责

| Directory | Responsibility |
| --- | --- |
| `docs/` | 人读文档，包括项目维护者、研发工程师、架构、规则、故事线、流程和设计说明。 |
| `install-resources/basic/` | Git 跟踪的跨平台通用安装资源，包括 AI 资产入口、手册、操作契约、工作流配置、策略、运行手册和模板。 |
| `install-resources/<os-arch>/` | Git 跟踪的平台二进制产物，只放对应平台的 `agentic-cli`。 |
| `install-resources/checksums.txt` | 安装资源校验和，覆盖 `basic` 和平台二进制。 |
| `bin/` | 安装后的本机命令目录；仓库只跟踪 `bin/.gitkeep`，本地 `bin/agentic-cli` 被 `.gitignore` 忽略。 |
| `.local/` | 本机安装和更新状态目录；仓库只跟踪 `.local/.gitkeep`，本地状态文件被 `.gitignore` 忽略。 |
| `.superpowers/` | 项目工作空间中的本地执行状态目录，保存工具检查点、临时分析和缓存；被 `.gitignore` 忽略，不属于项目资料。 |
| `.githooks/` | 源头仓库版本化 Git Hooks，阻止直接提交或推送 `main`。 |
| `plans/` | 面向维护者和项目维护代理的可执行推进计划。 |
| `skills/` | AgenticOps skills，让 AIAgent 知道如何工作。 |
| `packages/agentic-cli/` | Go CLI 运行时源码位置。 |
| `examples/` | 端到端演示样例。 |
| `tests/` | 合同、脚本和文档一致性测试。 |
| `scripts/` | 安装、检查和辅助脚本；`release.sh` 与 `hotfix.sh` 是源头仓库正式发布入口，共享实现位于 `scripts/lib/`。 |

## 5. 安装边界

`~/.agentic-ops` 是用户本机全局安装目录，也是 `tapstate/agentic-ops` 的完整 managed clone。它的目录结构与 GitHub 仓库一致。

`~/.agentic-ops` 不是具体项目或具体任务运行目录。

安装和更新行为：

- 首次安装 clone GitHub 仓库到 `~/.agentic-ops`。
- 更新时暂存 tracked 本地改动，记录 `.local/previous-ref`，再更新到 `origin/main` 最新版本。
- 每次安装或更新都校验 `install-resources/checksums.txt`。
- 当前平台二进制从 `install-resources/<os-arch>/agentic-cli` 复制到 `bin/agentic-cli`。
- 安装和更新状态写入 `.local/current-ref`、`.local/previous-ref`、`.local/install-log.json` 或 `.local/update-stash`。

`bin/agentic-cli` 和 `.local/*` 是本地产生文件，必须被 `.gitignore` 忽略，避免 managed clone 更新时产生冲突。

## 6. 工作空间边界

具体项目运行目录必须是项目 AI 工作空间，例如：

```text
tapstate/
tapdata/
```

项目 AI 工作空间保存该项目的 Jira 用户、Jira 空间、Jira 空间到代码仓库的映射、本地源码、工作流配置、任务执行上下文和反馈日志。`~/.agentic-ops` 只保存全局安装和通用资产，不保存具体项目的运行事实。

建议工作空间内运行资料位置：

```text
<project-ai-workspace>/
  .agentic-ops/
    runs/
    feedback/
  .superpowers/
    # local execution state only
```

`.superpowers/` 只保存当前项目工作空间的本地执行状态，不得承载正式设计、实施计划、项目规范或运行资产。正式设计进入 `docs/` 的对应主题目录，可执行计划进入顶层 `plans/`。工具的默认输出路径与本约定冲突时，以本约定为准；不得创建或提交 `docs/superpowers/`。

## 7. 结构决策

该结构满足 AgenticOps 设计文档、运行资产、计划和运行时代码分层维护要求，不需要额外目录决策。

`plans/` 保留在仓库顶层。原因是推进资料需要独立于设计说明维护，并且需要比 `docs/` 中的设计说明更容易被定位和更新。

`.superpowers/` 保留为工作空间本地目录并由 Git 忽略。它只反映一次或一段本地工具执行过程，不具备项目事实源地位，也不随安装资源发布。

运行时默认资源统一放在 `install-resources/basic/`；不要重新引入旧的顶层运行资源目录或旧的 release 目录作为安装资源源头。
