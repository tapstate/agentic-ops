# AgenticOps 文档

本文是 `docs/` 目录索引，用于帮助人审阅 AgenticOps 的终态设计、规则、故事线和实施计划入口。README 只保留终态定位和入口导航；阶段性成果、当前实现边界和剩余工作只在 `plans/` 中维护。

`docs/` 面向项目维护者和研发工程师阅读。AIAgent 执行任务前的资产入口见 [AI 资产入口](../install-resources/basic/ai-assets/README.md)。

## 角色入口

- [项目维护者上手](maintainers/getting-started.md)
- [研发工程师上手](development-engineers/getting-started.md)
- [AI 资产入口](../install-resources/basic/ai-assets/README.md)

## 核心文档

- [发布检查清单](review-checklist.md)
- [设计决策记录](decision-log.md)
- [项目目标](strategy/project-goals.md)
- [目标定位](strategy/positioning.md)
- [长期定位](strategy/long-term-positioning.md)
- [AgenticOps 项目全景](strategy/agenticops-project-overview.md)
- [AgenticOps Skill 与 Shell 驱动项目全景](strategy/skill-shell-agenticops-project-overview.md)
- [项目规则](project-rules.md)
- [配置规范](configuration-standards.md)
- [开发风格](development-style.md)
- [AIAgent 工作规则](ai-working-rules.md)

## 架构文档

- [当前设计](architecture/agenticops-current-design.md)
- [完整设计实现方案](architecture/full-design-implementation-design.md)
- [Jira 门禁式缺陷修复流程](architecture/jira-gated-defect-workflow.md)
- [源码发布流程](architecture/source-release-workflow-design.md)
- [项目结构](architecture/project-structure.md)

## 推进计划

- [设计实现差距代办](../plans/design-implementation-gap-todo-v1.md)
- [完整设计实现计划](../plans/full-design-implementation-plan-v1.md)
- [第一阶段实施计划](../plans/implementation-plan-v1.md)
- [正式使用前问题修复计划](../plans/problem-resolution-plan-v1.md)
- [Jira 门禁式缺陷修复流程实施计划](../plans/jira-gated-defect-workflow-plan.md)

## 产品流程

- [故事线总览](user-stories/agenticops-user-stories.md)
- [项目维护者故事](user-stories/project-maintainer-stories.md)
- [研发工程师故事](user-stories/development-engineer-stories.md)
- [标准流程注册处](processes/standard-process-registry.md)
- [反馈闭环](workflows/feedback-loop.md)
- [端到端演示](examples/end-to-end-demo.md)
- [v0.3 AO 真实试运行结果](examples/v0.3-ao-pilot-result.md)

## 契约与配置

- [操作契约](contracts/operation-contract.md)
- [配置规范](configuration-standards.md)
- [AI 操作任务表单标准](forms/task-form-standard.md)
- [工作流配置](profiles/workflow-profile.md)
- [CLI 运行时](runtime/cli-runtime.md)
- [版本号设计](runtime/versioning.md)
- [问题修复与同步路径](runtime/problem-resolution-and-update.md)
- [证据模板](templates/evidence-templates.md)

## 外部手册

- [AI 员工手册](../install-resources/basic/handbooks/ai-employee-handbook.md)

## 阶段计划

- [完整设计实现计划](../plans/full-design-implementation-plan-v1.md)
- [第一阶段实施计划](../plans/implementation-plan-v1.md)
- [正式使用前问题修复计划](../plans/problem-resolution-plan-v1.md)

## 规划规则

涉及设计、优化、计划、架构调整、流程调整、标准资产调整或会影响项目演进方向的变更前，必须先读取 [项目目标](strategy/project-goals.md)。

所有计划必须基于已确认的故事线和相对稳定的架构拆解。推荐顺序是：

```text
故事线
-> 架构边界
-> 大阶段
-> 中任务
-> 小步骤
-> 验证命令
```

计划文件可以记录阶段目标、勾选项、实现说明、当前实现边界和当前剩余工作；README 不承担阶段性成果记录职责。
