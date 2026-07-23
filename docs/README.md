# AgenticOps 文档

本文是 `docs/` 目录索引，用于帮助审阅 AgenticOps 的终态设计、规则、故事线和实施计划入口。README 只保留终态定位和入口导航；阶段性成果、当前实现边界和剩余工作只在 `plans/` 中维护。

## 核心文档

- [设计审阅清单](review-checklist.md)
- [设计决策记录](decision-log.md)
- [目标定位](strategy/positioning.md)
- [长期定位](strategy/long-term-positioning.md)
- [项目规则](project-rules.md)
- [项目研发期规则](development-phase-rules.md)
- [开发风格](development-style.md)
- [AIAgent 工作规则](ai-working-rules.md)

## 架构文档

- [当前设计](architecture/agenticops-current-design.md)
- [完整设计实现方案](architecture/full-design-implementation-design.md)
- [项目结构](architecture/project-structure.md)

## 推进计划

- [设计实现差距代办](../plans/design-implementation-gap-todo-v1.md)
- [完整设计实现计划](../plans/full-design-implementation-plan-v1.md)
- [第一阶段实施计划](../plans/implementation-plan-v1.md)
- [正式使用前问题修复计划](../plans/problem-resolution-plan-v1.md)

## 产品流程

- [故事线总览](user-stories/agenticops-user-stories.md)
- [项目维护者故事](user-stories/project-maintainer-stories.md)
- [研发负责人故事](user-stories/development-lead-stories.md)
- [标准流程注册处](processes/standard-process-registry.md)
- [反馈闭环](workflows/feedback-loop.md)
- [端到端演示](examples/end-to-end-demo.md)

## 契约与配置

- [操作契约](contracts/operation-contract.md)
- [AI 操作任务表单标准](forms/task-form-standard.md)
- [工作流配置](profiles/workflow-profile.md)
- [CLI 运行时](runtime/cli-runtime.md)
- [版本号设计](runtime/versioning.md)
- [问题修复与同步路径](runtime/problem-resolution-and-update.md)
- [证据模板](templates/evidence-templates.md)

## 外部手册

- [AI 员工手册](../handbooks/ai-employee-handbook.md)

## 阶段计划

- [完整设计实现计划](../plans/full-design-implementation-plan-v1.md)
- [第一阶段实施计划](../plans/implementation-plan-v1.md)
- [正式使用前问题修复计划](../plans/problem-resolution-plan-v1.md)

## 规划规则

所有计划必须基于已确认的故事线和相对稳定的架构拆解。推荐顺序是：

```text
故事线
-> 架构边界
-> 大阶段
-> 中任务
-> 小步骤
-> 验证命令
```

计划文件可以记录阶段目标、checkbox、Implementation note、当前实现边界和当前剩余工作；README 不承担阶段性成果记录职责。
