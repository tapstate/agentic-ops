# AgenticOps

AgenticOps 是把公司事务处理方式沉淀成 AI 可执行标准流程的本地控制体系。

第一阶段先落地研发 Jira 任务：帮助研发操作 AI 员工从 Jira 接管任务到完成任务。它让 Jira 继续管理任务，让研发 owner 继续做关键授权，同时把 AI 员工的执行动作收敛到可审计的命令、操作契约、工作日志、证据和人工门禁里。不同类型的任务可以通过不同 operation、workflow profile、policy、runbook 和 template 进入不同流程；执行过程必须留下记录，并把关键状态、关键信息和证据回写到合适的位置，用于后续分析和优化。

## 这个项目解决什么问题

AI 代理可以写代码、跑测试、整理证据，但如果直接面对 Jira、GitHub、本地仓库和项目规则，很容易出现三个问题：

- 不知道当前任务该做到哪一步。
- 不知道哪些动作需要研发 owner 授权。
- 执行过程缺少可恢复、可复盘的记录。

AgenticOps 的目标是提供一层本地控制面，让 AIAgent 不靠临场猜测，而是按标准流程工作：

```text
Jira 任务
-> 研发 owner 授权
-> agent-task-ops 操作契约
-> AI 代理执行
-> 关键状态和信息回写
-> evidence / feedback / human gate
```

当前实现使用 fake Jira adapter 和本地工作空间文件跑通最小闭环，不读写真实 Jira 或 GitHub。

术语边界：`AgenticOps` 是项目和体系，`agent-task-ops` 是安装后给 AIAgent 和研发 owner 使用的 CLI 二进制。

## 谁会使用

### 项目维护者

项目维护者负责维护 AgenticOps 自身，包括源码结构、操作契约、文档、实施计划和后续发布。

从这里开始：

- [第一阶段实施计划](plans/implementation-plan-v1.md)
- [正式使用前问题修复计划](plans/problem-resolution-plan-v1.md)
- [项目结构](docs/architecture/project-structure.md)
- [项目规则](docs/project-rules.md)
- [问题修复与同步路径](docs/runtime/problem-resolution-and-update.md)
- [CLI 实现](packages/agent-task-ops/)
- [机器可读操作契约](contracts/operations/)

常用验证：

```sh
go test ./...
bash scripts/test-init.sh
bash tests/e2e/local-fake-flow.sh
```

源码调试入口：

```sh
go run ./packages/agent-task-ops/cmd/agent-task-ops --version
```

### AI 研发

AI 研发是使用 AgenticOps 指挥 AI 处理 Jira 任务的人。你不需要关心源码、Go 编译环境或仓库内部结构，只需要面对安装后的 `agent-task-ops` 命令行工具，以及随工具提供的知识、模板和规范。

从这里开始：

- [AI 员工手册](handbooks/ai-employee-handbook.md)
- [端到端演示](docs/examples/end-to-end-demo.md)
- [用户故事](docs/user-stories/agenticops-user-stories.md)

安装后的命令入口：

```sh
agent-task-ops preflight --workspace tapstate
agent-task-ops workspace init --workspace tapstate
agent-task-ops agent init --workspace tapstate
agent-task-ops list-tasks --workspace tapstate
agent-task-ops takeover-task TAP-123 --workspace tapstate
```

### AI 代理

AI 代理不应主要依赖 README 执行任务，也不需要读取 AgenticOps 源码或关心 Go 编译环境。AI 代理面对的是安装后的命令行工具、AI 员工手册、操作契约、模板和工作规则。

执行前读取：

- [AI 员工手册](handbooks/ai-employee-handbook.md)
- [AIAgent 工作规则](docs/ai-working-rules.md)
- [操作契约说明](docs/contracts/operation-contract.md)
- [机器可读操作契约](contracts/operations/)

AI 代理必须按 `task_type`、`current_stage`、`next_action` 推进，不按固定角色推进。

## 当前工程事实

当前仓库包含：

- Go CLI：`packages/agent-task-ops/`。
- CLI 入口：`agent-task-ops`。
- fake Jira adapter：`packages/agent-task-ops/internal/jira/`。
- 本地工作空间目录：`.agentic-ops/runs`、`.agentic-ops/feedback`。
- evidence 写入：`write-evidence`。
- feedback event 和日报：`feedback report`。
- 操作契约 YAML：`contracts/operations/`。
- 运行资产源头：`assets/`。
- 本地资产安装：`assets install --source assets --install-dir <dir> --version <asset_version>`。
- 安装 bootstrap：`scripts/init.sh`。
- 编译脚本：`scripts/build.sh`。
- 本地发版打包脚本：`scripts/release.sh`。
- 本地 e2e：`tests/e2e/local-fake-flow.sh`。

当前边界：

- 不读写真实 Jira。
- 不读写真实 GitHub。
- 不自动 push、创建 PR、merge 或发布。
- 不自动改写 AgenticOps 源头规则。

## 快速开始

项目维护者运行验证：

```sh
go test ./...
bash scripts/test-init.sh
bash scripts/test-build-release.sh
bash tests/e2e/local-fake-flow.sh
```

项目维护者查看源码版 CLI 输出：

```sh
go run ./packages/agent-task-ops/cmd/agent-task-ops --version
go run ./packages/agent-task-ops/cmd/agent-task-ops feedback report --workspace tapstate --date 2026-07-21
```

运行安装引导：

```sh
bash scripts/init.sh
```

`scripts/init.sh` 当前会在 `~/.agentic-ops/bin/agent-task-ops` 写入 bootstrap stub，用于验证安装路径和平台识别；它还不是 release 二进制下载器。

生成本地 release 产物：

```sh
bash scripts/build.sh 0.1.0-dev
AGENTIC_OPS_ASSET_VERSION=2026.07.22.1 bash scripts/release.sh 0.1.0-dev
```

`scripts/release.sh` 当前只在 `dist/release/<version>/` 生成二进制包、资产包、checksum 和 manifest，不会创建 GitHub Release，也不会推送任何内容。

## 工作目录约定

`~/.agentic-ops` 是全局安装和配置目录，不是具体项目运行目录。

具体项目运行目录是项目 AI 工作空间，例如 `tapstate`、`tapdata`。CLI 在工作空间内写入：

```text
.agentic-ops/
  runs/
  feedback/
```

## 目录导航

| 目录 | 用途 |
| --- | --- |
| `packages/agent-task-ops/` | Go CLI 实现。 |
| `assets/` | 发布到 `~/.agentic-ops/assets/<version>/` 的运行资产源头。 |
| `contracts/operations/` | 机器可读操作契约。 |
| `handbooks/` | AI 员工手册。 |
| `docs/` | 项目规则、架构、用户故事、运行时设计和反馈闭环。 |
| `plans/` | 可执行推进计划。 |
| `scripts/` | 安装和本地检查脚本。 |
| `tests/` | 端到端 fake flow。 |
