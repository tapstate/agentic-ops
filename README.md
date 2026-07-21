# AgenticOps 项目说明

AgenticOps 是面向研发流程的 AI 执行控制体系，用于让 AIAgent 在现有 Jira-centered 研发体系中可控地接管任务、完成开发、运行验证并回写证据。

当前阶段只做文档和设计落地，不实现运行代码。本文档中的命令是第一阶段目标接口，除非仓库中出现对应文件或命令输出证明，否则不得视为已经实现。

## 当前定位

AgenticOps 不替代 Jira、不替代研发 owner、不替代 PR Review，也不以全自动开发作为第一阶段目标。

它的第一阶段目标是跑通一条真实、可控、可复用的主链路：

```text
Jira issue 已进入迭代
-> 研发 owner 手动触发 AI
-> AI 拉取 owner 名下待办
-> 研发 owner 选择一个 issue
-> AI 执行任务接管 gate
-> AI 生成 run_id 和接管记录
-> AI 本地开发与验证
-> AI 回写 Jira 证据
-> 研发 owner 确认
-> 授权 push / PR
-> 进入既有 CI / Review / 合入流程
```

## 设计组件

```text
AgenticOps
= AI 员工手册
+ 项目规则
+ Operation Contract
+ Workflow Profile
+ Go CLI Runtime
+ Evidence Templates
+ Feedback Loop
```

## 文档导航

- [文档索引](docs/README.md)
- [设计审阅清单](docs/review-checklist.md)
- [设计决策记录](docs/decision-log.md)
- [目标定位](docs/strategy/positioning.md)
- [项目规则](docs/project-rules.md)
- [当前设计](docs/architecture/agenticops-current-design.md)
- [项目结构](docs/architecture/project-structure.md)
- [第一阶段实施计划](plans/implementation-plan-v1.md)
- [开发风格](docs/development-style.md)
- [AIAgent 工作规则](docs/ai-working-rules.md)
- [用户故事](docs/user-stories/agenticops-user-stories.md)
- [AI 员工手册](handbooks/ai-employee-handbook.md)
- [操作契约](docs/contracts/operation-contract.md)
- [工作流配置](docs/profiles/workflow-profile.md)
- [CLI 运行时](docs/runtime/cli-runtime.md)
- [证据模板](docs/templates/evidence-templates.md)
- [反馈闭环](docs/workflows/feedback-loop.md)
- [端到端演示](docs/examples/end-to-end-demo.md)

## 安装方向

第一阶段设计中的安装入口：

```sh
curl -fsSL https://raw.githubusercontent.com/tapstate/agentic-ops/init.sh | bash
```

安装目录约定为：

```text
~/.agentic-ops
```

`~/.agentic-ops` 是全局安装和配置目录，不是具体项目运行目录。具体项目运行目录应是项目 AI 工作空间，例如 `tapstate`、`tapdata`。

## 当前状态

当前仓库优先沉淀：

- 项目定位和规则。
- 项目规范、约束和开发风格。
- AIAgent 防幻觉工作规则。
- 用户故事。
- AI 员工工作方式。
- Operation Contract。
- Workflow Profile。
- 项目 AI 工作空间边界。
- Feedback Loop。

在设计审阅确认前，不开始实现代码。
