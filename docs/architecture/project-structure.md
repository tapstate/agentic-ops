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
      project-maintainer-stories.md
      development-lead-stories.md
      project-maintainer/
        pm-001-document-boundary.md
        ...
      development-lead/
        dl-001-install.md
        ...
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
```

## 4. 目录职责

| Directory | Responsibility |
| --- | --- |
| `docs/` | 架构、规则、故事线、流程和设计说明。 |
| `handbooks/` | AI 员工手册，面向 AIAgent 和研发负责人。 |
| `assets/` | 安装后交付给研发负责人和 AIAgent 使用的运行资产源头。 |
| `plans/` | 面向维护者和项目维护代理的可执行推进计划。 |
| `contracts/` | 可机器读取的 操作契约，以 YAML / JSON 管理。 |
| `profiles/` | 工作流配置示例和默认配置。 |
| `skills/` | AgenticOps skills，让 AIAgent 知道如何工作。 |
| `templates/` | Jira / 拉取请求 / 证据回写模板。 |
| `packages/agentic-cli/` | Go CLI 运行时源码位置。 |
| `examples/` | 端到端演示样例。 |
| `tests/` | 合同、脚本和文档一致性测试。 |
| `scripts/` | 安装、检查和辅助脚本。 |

## 5. 安装边界

`~/.agentic-ops` 是用户本机全局安装和配置目录，可保存从 release 安装得到的 Go 二进制、安装元数据、全局配置、通用手册、通用 skills 和可安全重建的缓存。

`~/.agentic-ops` 不是具体项目或具体任务运行目录。

本仓库使用目录区分源码、设计、计划和运行资产，不使用不同分支分管资料。发布时再按交付对象拆分：

- 维护者面对完整仓库。
- 研发负责人和 AIAgent 面对安装后的 `agentic-cli`、`~/.agentic-ops/current.json` 和 `~/.agentic-ops/assets/<version>/`。
- 设计文档和推进资料不进入普通使用者的日常入口。

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
```

## 7. 结构决策

该结构满足 AgenticOps 设计文档、运行资产、计划和运行时代码分层维护要求，不需要额外目录决策。

`plans/` 保留在仓库顶层。原因是推进资料需要独立于设计说明维护，并且需要比 `docs/` 中的设计说明更容易被定位和更新。

如果要改变 `contracts/`、`profiles/`、`skills/`、`templates/` 的职责边界，需要先确认对应内容属于“设计样例”还是“运行时默认配置”；该取舍应由用户决策。
