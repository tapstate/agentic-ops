# AgenticOps 文档

本文是 `docs/` 目录索引，用于帮助审阅当前设计、阶段状态和实施计划。README 只保留 AgenticOps 的终态定位和入口导航；阶段性成果、当前实现边界和剩余工作在本文档、运行时文档和 `plans/` 中维护。

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

- [完整设计实现计划](../plans/full-design-implementation-plan-v1.md)
- [第一阶段实施计划](../plans/implementation-plan-v1.md)
- [正式使用前问题修复计划](../plans/problem-resolution-plan-v1.md)

## 产品流程

- [用户故事](user-stories/agenticops-user-stories.md)
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

## 当前状态

当前仓库已进入第一阶段本地实现，阶段性事实以源码、测试、命令输出和实施计划 checkbox 为准。不要把尚未有源码、测试或命令输出支撑的能力描述为已实现。

阶段性实现边界见：

- [项目研发期规则](development-phase-rules.md)
- [问题修复与同步路径：当前实现边界](runtime/problem-resolution-and-update.md#13-当前实现边界)
- [完整设计实现计划](../plans/full-design-implementation-plan-v1.md)
- [第一阶段实施计划](../plans/implementation-plan-v1.md)
- [正式使用前问题修复计划](../plans/problem-resolution-plan-v1.md)

## 规划规则

所有计划必须基于已确认并相对稳定的架构拆解。推荐顺序是：

```text
架构边界
-> 大阶段
-> 中任务
-> 小步骤
-> 验证命令
```

计划文件可以记录阶段目标、checkbox、Implementation note 和当前剩余工作；README 不承担阶段性成果记录职责。
