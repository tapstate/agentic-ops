# AgenticOps 项目结构

> **状态：** Skill + Python Runtime + Shell Bootstrap + Rule 目标结构。当前仓库仍保留 Go CLI、`install-resources/` 和历史 `plans/`；本次重构的计划、进度和验收以 Jira `AO-11` 为准。

## 1. 目的

本文定义 AgenticOps 目标仓库、安装目录和项目 AI 工作空间的结构，确保标准资产、运行时代码、安装状态和具体任务事实不会混用。

## 2. 三个边界

AgenticOps 必须区分三类位置：

| 位置 | 作用 | 可以保存 | 不得保存 |
| --- | --- | --- | --- |
| 源头仓库 | 维护、测试和发布 AgenticOps | Skill、Python Runtime、Rule、标准、模板、项目映射、Bootstrap、文档和测试 | 用户 token、业务任务运行状态 |
| `~/.agentic-ops` | 稳定 `main` 的 managed clone 和本机安装 | 源头仓库稳定资产、锁定 Python 环境、本机配置、安装引用和回滚点 | 具体 Jira 任务的分析、进展和证据 |
| 项目 AI 工作空间 | 执行 Tapdata、AO 等项目任务 | 项目 overlay、本地源码、任务状态、报告、证据、反馈和 worktree 引用 | AgenticOps 全局源码副本、其它项目任务状态 |

## 3. 目标仓库结构

```text
agentic-ops/
  README.md
  AGENTS.md
  agent-init.md
  agent-guides.md
  .python-version
  pyproject.toml
  uv.lock
  .githooks/
    pre-commit
    pre-push

  bootstrap/
    install.sh
    update.sh
    rollback.sh
    agentic-cli
    lib/
      common.sh

  runtime/
    src/
      agentic_ops/
        __init__.py
        __main__.py
        cli.py
        output.py
        config/
        contracts/
        task_state/
        workflow/
        jira/
        git/
        github/
        evidence/
        feedback/
    tests/
      unit/
      contract/
      fixtures/

  skills/
    task-execution/
      SKILL.md
    workspace-init/
      SKILL.md
    feedback-improvement/
      SKILL.md

  rules/
    design-guardrails.md
    ai-execution.md
    source-maintenance.md

  standards/
    company/
      core-hard-rules.md
    contracts/
      operations/
      processes/
    policies/
    runbooks/
    templates/
    projects/
      tapdata/
        profile.yaml
        rules/
        runbooks/
        templates/
      ao/
        profile.yaml
        rules/
        runbooks/
        templates/

  docs/
  examples/
  .superpowers/                 # 本地执行状态、检查点和缓存，不提交
  tests/
    e2e/
    install/
    resources/

  scripts/
    release.sh
    hotfix.sh
    lib/
      development-workflow.sh
      release-common.sh

  bin/
    .gitkeep
  .local/
    .gitkeep
```

## 4. 目录职责

`.superpowers/` 只保存工具的本地执行状态、检查点、临时分析和缓存，由 Git 忽略。正式设计进入 `docs/`；实施计划、进度、阻塞和验收进入 Jira，不在仓库建立第二份计划事实源。

### 4.1 `bootstrap/`

`bootstrap/` 是安装后允许运行的 Shell 边界，只负责：

- clone、fast-forward 更新和回滚 `~/.agentic-ops`。
- 安装或定位 `uv`。
- 根据 `.python-version`、`pyproject.toml` 和 `uv.lock` 准备隔离 Python 环境。
- 保留 `bin/agentic-cli` 入口名称，将其替换为把参数原样传给 Python Runtime 的轻量包装脚本。
- 执行环境存在性、目录权限和安装引用等轻量检查。

`bootstrap/` 不解析工作流 profile、不维护任务状态、不调用 Jira / GitHub 业务 API、不生成证据、不判断人工门禁。

### 4.2 `runtime/`

`runtime/` 是 Python Runtime 的唯一源码位置，负责所有结构化和有状态操作：

- 稳定 CLI、JSON 输出、退出码和失败码。
- 配置、标准流程、操作契约、策略和项目映射解析。
- 项目工作空间与任务状态的原子读写、任务级锁和 schema 迁移。
- Jira Description、Comment、字段与 transition 的受控读写和回读。
- Git 工作区、worktree、分支和提交事实检查。
- GitHub PR、CI、Review 和评论事实读取及受控操作。
- 证据、恢复、反馈和脱敏诊断。

Python Runtime 不保存项目私有规则；项目差异必须来自 `standards/projects/<project>/` 或项目工作空间 overlay。

### 4.3 `skills/`

`skills/` 保存面向 AIAgent 的流程入口。Skill 负责识别任务、读取 Rule 和标准资产、调用 Python 操作、解释结果并在能力缺口时转入 AI 判断或人工确认。

Skill 不复制 Python 实现，不直接通过 `curl`、`git` 或 `gh` 绕过 Runtime 已提供的操作。

### 4.4 `rules/`

`rules/` 保存安装后直接约束 AIAgent 的运行规则：

- `design-guardrails.md`：AgenticOps 源头设计和规划的红线，只在 `source_maintenance` 加载。
- `ai-execution.md`：业务任务中的语言、事实源、人工门禁、证据和停止条件，只在 `project_execution` 加载。
- `source-maintenance.md`：维护 AgenticOps 源头仓库时的 Jira、worktree、分支、提交、推送和发布规则。

公司与项目业务规则不放在这里混写，分别进入 `standards/company/` 和 `standards/projects/`。

### 4.5 `standards/`

`standards/` 是安装后标准资产的唯一版本化源头：

- `company/`：跨项目公司硬规定。
- `contracts/operations/`：Python 原子操作输入、输出、失败码、副作用和门禁。
- `contracts/processes/`：任务分类、标准阶段和推进规则。
- `policies/`：外部写入、授权和风险门禁。
- `runbooks/`：已知异常的排查、恢复和转人工路径。
- `templates/`：通用 Jira、证据和反馈模板。
- `projects/<project>/`：Tapdata、AO 等项目 profile、规则、runbook 和模板。

目标结构不再维护重复的 `install-resources/basic/` 副本。`~/.agentic-ops` 是完整 managed clone，顶层标准资产本身就是安装后的运行资产。

### 4.6 `scripts/`

`scripts/` 只服务 AgenticOps 源头仓库维护：

- `release.sh` 和 `hotfix.sh` 编排 Git、GitHub、固定验证和发布审计。
- 不作为安装后业务任务的运行入口。
- 不与 `bootstrap/` 混用。

### 4.7 `tests/`

- `runtime/tests/`：Python 单元、契约和 fixture 测试，与 Runtime 模块一起维护。
- `tests/install/`：无 Go 环境下的安装、更新、配置保留和回滚测试。
- `tests/resources/`：Skill、Rule、标准、模板和项目映射一致性测试。
- `tests/e2e/`：本地 fixture、Tapdata 受控验收、AO 试验和问题修复闭环。

## 5. Python 项目与依赖边界

Python Runtime 固定使用：

```text
.python-version   Python 3.12 主次版本
pyproject.toml    包元数据、直接依赖、入口和工具配置
uv.lock           跨平台锁定的完整依赖图
```

规则：

- 使用 `uv sync --locked` 准备环境；锁文件漂移时停止安装或验证。
- Python 环境位于 `~/.agentic-ops/.venv`，由安装流程管理并由 Git 忽略。
- 不复用业务项目的 `.venv`、Conda 环境或系统 site-packages。
- 首选 Python 标准库；需要 YAML、HTTP 重试、文件锁等第三方依赖时必须进入锁文件。
- Runtime 源码更新不需要构建项目自有二进制；依赖未变化时更新后直接生效。
- 不把 `.venv`、wheel、缓存或下载的 Python 运行时提交到仓库。

## 6. 安装后的 `~/.agentic-ops`

`~/.agentic-ops` 是源头仓库稳定 `main` 的完整 managed clone。除 Git 跟踪内容外，本机增加：

```text
~/.agentic-ops/
  .venv/                 # uv 管理，Git 忽略
  bin/
    agentic-cli          # Bootstrap 生成的稳定入口
  user/                  # Git 忽略
    config.local.yaml
    .env
  .local/                # Git 忽略
    current-ref
    previous-ref
    install-log.json
    update-stash/
```

安装入口执行：

```text
clone / fetch main
-> 校验仓库和目标提交
-> 安装或定位 uv
-> uv sync --locked
-> 生成 bin/agentic-cli
-> 执行 Python preflight
-> 写入 current-ref 和 previous-ref
```

更新失败时，回退 Git 引用并重新执行 `uv sync --locked`；不得覆盖 `user/` 和项目 AI 工作空间中的任务状态。

## 7. 项目 AI 工作空间

Tapdata、AO 等项目分别使用独立工作空间：

```text
<project-ai-workspace>/
  AGENTS.md
  repos/
    <repository>/
  .agentic-ops/
    agent.json
    config.local.yaml
    profile.local.yaml
    locks/
      <ISSUE-KEY>.lock
    tasks/
      <ISSUE-KEY>/
        task.json
        progress.json
        decisions.ndjson
        sync.json
        journal.ndjson
        reports/
          analysis.md
          plan.md
          blocked.md
          verification.md
          review.md
          completion.md
        feedback/
          observation.md
        runs/
          <agentic_run_id>/
            summary.json
            evidence/
    worktrees/
      <ISSUE-KEY>/
```

运行状态使用 JSON / NDJSON，因为它由 Python Runtime 维护并需要严格 schema、原子写入和恢复；Markdown 只保存需要人审阅和外部汇报的内容。YAML 只用于人工维护的本机配置与项目 overlay。

同一 Jira 任务同时只允许一个活动运行；历史 `runs/` 只读保留。任务级锁阻止两个本地操作同时更新状态，但不能替代 Jira 所有权、GitHub 分支保护或人工门禁。

## 8. 从当前结构迁移

| 当前位置 | 目标位置或处理 |
| --- | --- |
| `packages/agentic-cli/`、`go.mod` | 提取仍需保留的契约、门禁和 fixture 后，按重构需要删除；替代实现进入 `runtime/src/agentic_ops/` |
| `install-resources/basic/` | 按类别迁移到 `standards/`、`rules/`、`skills/`；消除重复副本 |
| `install-resources/<os-arch>/agentic-cli` | Python 主链路验收后删除 |
| `install-resources/checksums.txt` | 改为 Git 提交、锁文件和安装审计校验后删除 |
| `scripts/install.sh` | 重写并迁移为 `bootstrap/install.sh` |
| `scripts/build.sh`、`scripts/test-build.sh` | Go 移除时删除 |
| `bin/agentic-cli` Go 二进制 | 保留命令名，替换为调用 Python Runtime 的 Shell 包装入口 |
| `.local/` | 保留本机状态职责，字段改为 Git/Python 安装语义 |
| `docs/` | 保留长期目标、架构、规则说明和决策，不保存阶段进度 |
| `plans/` | 长期事实迁入正式资料，未完成工作转入 Jira，其余由 Git 历史保留，最终删除顶层目录 |

旧 Go 由版本分支、Tag 和 Git 历史保留，不维护 Go/Python 双轨。实施中可根据工程需要删除旧实现，但删除前必须提取仍需保留的行为、契约、错误码、fixture 和安全门禁；合入 `develop` 前 Python 主链路必须整体可验证。

## 9. 运行模式加载

`source_maintenance` 用于 AgenticOps 源头仓库和改进 worktree，加载设计红线、源头维护规则、项目目标、公司标准和维护 Skill。

`project_execution` 用于 Tapdata 等业务任务，加载业务仓库规则、`ai-execution.md`、公司标准、项目标准、任务 Skill 和项目 overlay；不得加载 AgenticOps 设计红线、源头发布规则或 AO 专用工作流。

每个 Skill 必须声明 `allowed_modes`。Runtime 结合 `agent.json`、Git remote、仓库根目录、Profile 和操作要求验证模式，不一致时返回 `workspace_mode_mismatch`。

## 10. 结构验收

目标结构完成必须证明：

- `~/.agentic-ops` 更新后 Skill、Rule、标准和 Python 源码立即生效。
- 没有本机 Go 环境也能安装、运行、更新和回滚。
- Bootstrap 不包含 Jira、GitHub、Git 业务判断。
- Python Runtime 不硬编码 Tapdata 或 AO 项目差异。
- 具体任务资料只存在于项目 AI 工作空间，并按 Jira 编号隔离。
- 本地状态损坏、并发更新和外部写入不确定时能够阻断或恢复。
- `main`、`develop`、Hotfix 和发布 PR 治理不因结构调整而弱化。
- 顶层 `plans/` 已退出当前事实源，Jira 承担计划、进度和验收管理。
- `source_maintenance` 与 `project_execution` 的加载集合可自动验证且不会交叉污染。
