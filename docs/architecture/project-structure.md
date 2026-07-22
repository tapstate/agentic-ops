# AgenticOps 项目结构

## 1. 目的

本文定义 AgenticOps 仓库的目标结构、资料边界和各目录职责。当前仓库已进入第一阶段本地实现，真实 Jira / GitHub 写操作仍未接入。

## 2. 仓库范围

`tapstate/agentic-ops` 是 AgenticOps 的权威源头仓库，管理全局通用资料：

- 源码设计。
- 项目规则。
- AI 员工手册。
- Operation Contract。
- Workflow Profile。
- Skills。
- Templates。
- 运行资产源头。
- CLI Runtime 设计。
- 文档、示例和测试规划。

## 3. 目标结构

```text
agentic-ops/
  README.md
  docs/
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
    workflows/
      feedback-loop.md
    review-checklist.md
    decision-log.md
    ai-working-rules.md
    development-style.md
    project-rules.md
  handbooks/
    ai-employee-handbook.md
  assets/
    manifest.json
    handbooks/
    contracts/
    profiles/
    policies/
    runbooks/
    templates/
  plans/
    implementation-plan-v1.md
  contracts/
    operations/
  profiles/
  skills/
  templates/
    evidence/
    jira-comments/
    pr-comments/
  packages/
    agent-task-ops/
      cmd/
        agent-task-ops/
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
```

## 4. 目录职责

| Directory | Responsibility |
| --- | --- |
| `docs/` | 架构、规则、用户故事、流程和设计说明。 |
| `handbooks/` | AI 员工手册，面向 AIAgent 和研发 owner。 |
| `assets/` | 安装后交付给研发 owner 和 AIAgent 使用的运行资产源头。 |
| `plans/` | 面向 AIAgent 和研发 owner 的可执行推进计划，使用 checkbox 跟踪实施进度。 |
| `contracts/` | 可机器读取的 Operation Contract，后续以 YAML / JSON 管理。 |
| `profiles/` | Workflow Profile 示例和默认配置。 |
| `skills/` | AgenticOps skills，让 AIAgent 知道如何工作。 |
| `templates/` | Jira / PR / evidence 回写模板。 |
| `packages/agent-task-ops/` | Go CLI Runtime 当前实现位置。 |
| `examples/` | 端到端演示样例。 |
| `tests/` | 合同、脚本和文档一致性测试。 |
| `scripts/` | 安装、检查和辅助脚本。 |

## 5. 安装边界

`~/.agentic-ops` 是用户本机全局安装和配置目录，可保存从 release 安装得到的 Go 二进制、安装元数据、全局配置、通用手册、通用 skills 和可安全重建的缓存。

`~/.agentic-ops` 不是具体项目或具体任务运行目录。

当前仓库使用目录区分源码、设计、计划和运行资产，不使用不同分支分管资料。发布时再按交付对象拆分：

- 维护者面对完整仓库。
- 研发 owner 和 AIAgent 面对安装后的 `agent-task-ops`、`~/.agentic-ops/current.json` 和 `~/.agentic-ops/assets/<version>/`。
- 设计文档和实施计划不进入普通使用者的日常入口。

## 6. 工作空间边界

具体项目运行目录必须是项目 AI 工作空间，例如：

```text
tapstate/
tapdata/
```

项目 AI 工作空间保存该项目的 Jira 空间、GitHub 仓库、本地源码、workflow profile、任务执行上下文和反馈日志。

建议工作空间内运行资料位置：

```text
<project-ai-workspace>/
  .agentic-ops/
    runs/
    feedback/
```

## 7. 结构决策

当前结构可以支持第一阶段文档和设计落地，不需要额外目录决策。

`plans/` 保留在仓库顶层。原因是实施计划不是普通说明文档，而是给 AIAgent 按步骤执行、给研发 owner 跟踪推进状态的工作入口；它需要比 `docs/` 中的设计说明更容易被定位和更新。

如果后续要把 `contracts/`、`profiles/`、`skills/`、`templates/` 提前填入可执行配置，需要先确认这些内容属于“设计样例”还是“运行时默认配置”。
