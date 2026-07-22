# AgenticOps 文档

本文是 `docs/` 目录索引，用于帮助审阅当前设计。

## 核心文档

- [设计审阅清单](review-checklist.md)
- [设计决策记录](decision-log.md)
- [目标定位](strategy/positioning.md)
- [长期定位](strategy/long-term-positioning.md)
- [项目规则](project-rules.md)
- [开发风格](development-style.md)
- [AIAgent 工作规则](ai-working-rules.md)

## 架构文档

- [当前设计](architecture/agenticops-current-design.md)
- [项目结构](architecture/project-structure.md)

## 推进计划

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

## 当前阶段

当前仓库已进入第一阶段本地实现。`agentic-cli` Go CLI 已支持本地 fake flow、本地资产安装和本地 release 打包；真实 Jira / GitHub 写操作、push、PR、merge 和发布仍未接入。本文档集合中的能力以当前源码、测试和命令输出为准。
