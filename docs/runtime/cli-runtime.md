# Go CLI 运行时

## 1. 目的

本文定义 AgenticOps 第一阶段 Go CLI Runtime 的设计和当前实现边界。当前仓库已实现本地 fake flow；真实 Jira / GitHub 写操作、push、PR、merge 和发布仍未接入。

CLI Runtime 的目标是给 AIAgent 提供稳定、结构化、可审计的操作入口，避免 AIAgent 直接面对 Jira / GitHub / Git 的底层事实和高风险动作。

## 2. 运行时形态

统一入口：

```sh
agent-task-ops
```

建议安装位置：

```text
~/.agentic-ops/bin/agent-task-ops
```

建议源码位置：

```text
packages/agent-task-ops/
```

第一阶段采用：

- shell bootstrap：只负责 `curl | bash` 安装引导。
- Go CLI：承载 operation、policy、adapter、事件日志、反馈分析和结构化输出。

## 3. 目标目录

```text
packages/agent-task-ops/
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
```

以上是目标结构。当前已实现 `cli`、`config`、`contract`、`evidence`、`feedback`、`jira`、`output`、`policy` 和 `workspace` 的本地 fake flow；`git`、`github` 等真实集成目录仍属于后续阶段。

Operation Contract 的机器可读源头在仓库顶层 `contracts/operations/`。Go CLI 可以在构建或运行时读取这些契约，但不在 package 内维护第二份契约源头。

## 4. 平台要求

第一阶段必须支持：

- Linux (linux-amd64 / linux-arm64)。
- macOS Intel (darwin-amd64)。
- macOS Apple Silicon (darwin-arm64)。

主 CLI 发布目标：

```text
darwin-arm64
darwin-amd64
linux-amd64
linux-arm64
```

安装 bootstrap 允许依赖：

```text
bash
curl
tar 或 unzip
```

`agent-task-ops` 运行时不得依赖本地 Python、`jq` 或 shell 业务脚本。

## 5. 输入输出规则

CLI 必须遵守：

- stdout 只输出结构化 JSON。
- stderr 输出人类诊断日志。
- 所有失败返回稳定 `code`。
- 退出码有固定语义。
- 写操作必须检查 policy、gate 和 confirmation。
- secrets 不允许出现在 stdout、stderr 或事件日志中。

示例输出：

```json
{
  "ok": false,
  "operation": "takeover_task",
  "code": "missing_target_repo",
  "message": "Jira issue 缺少目标仓库信息",
  "required_human_action": "请补充 target_repo 或 workspace repo 映射"
}
```

## 6. 受控操作

以下动作必须由 CLI guard 管控：

- Jira 写评论。
- Jira 状态推进。
- Git commit。
- Git push。
- Git merge。
- Git rebase。
- Git clean。
- GitHub PR 创建。
- GitHub PR 更新。
- PR comments 修复后的重新提交。

## 7. 预检

`agent-task-ops preflight` 应检查：

- OS 和 CPU 架构。
- 当前二进制版本。
- Git 是否可用。
- GitHub CLI 是否可用。
- GitHub 登录状态。
- Jira 凭证配置。
- workspace profile 完整性。
- 当前业务仓库与 workspace 是否匹配。

## 8. 发布与修复

AgenticOps 面向公司研发分发，运行时必须支持快速修复和快速升级。

正式使用前的问题分类、诊断、发布、同步和回滚路径见 `docs/runtime/problem-resolution-and-update.md`。

第一阶段发布流程应满足：

- 每次 release 生成多平台二进制。
- 安装脚本按 OS 和 CPU 架构下载对应二进制。
- 版本号使用 `STATE-vMAJOR.ITERATION.COMMIT_INDEX-COMMIT` 格式，例如 `RES-v0.1.3-a68372d`。
- `agent-task-ops version` 能输出当前版本、`version_state`、`iteration_version`、`commit_index`、commit 和构建时间。
- `version_state` 必须区分 `SRC`、`DEV` 和 `RES`，分别表示源码运行、开发版和正式版。
- build version、release version 和 asset version 均由脚本自动生成，不允许手工指定。
- `agent-task-ops self-update` 能升级到最新稳定版本；有新版本时推荐自动更新应用。
- 安装和升级不得覆盖用户本地配置。
- 项目采用 latest-only 支持策略，BUG 只在最新版本修复，不维护旧版本补丁线。
- 如后续实现 rollback，它只用于安装失败或新版本不可用时的本地恢复，不作为旧版本修复策略。

## 9. 语言边界

Go 是 AgenticOps 主 CLI 的实现语言。

shell 只允许用于：

- `init.sh` 安装引导。
- 轻量环境检测。
- 下载或切换 Go release 二进制。

shell 不允许承载：

- Jira / GitHub / Git 业务操作。
- Operation Contract 解析。
- Workflow Profile 校验。
- Policy 和 human gate 判断。
- Evidence 生成。
- Feedback 分析。
