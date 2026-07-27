# AgenticCLI 运行时

## 1. 目的

本文定义 AgenticOps AgenticCLI 运行时的终态设计和稳定运行边界。阶段性实现状态、当前实现边界和剩余工作维护在 `plans/` 中。

AgenticCLI 的目标是给 AIAgent 提供稳定、结构化、可审计的操作入口，承载 AgenticOps 成熟经验沉淀后的原子操作，避免 AIAgent 直接面对 Jira / GitHub / Git 的底层事实和高风险动作。

## 2. 运行时形态

统一入口：

```sh
agentic-cli
```

建议安装位置：

```text
~/.agentic-ops/bin/agentic-cli
```

建议源码位置：

```text
packages/agentic-cli/
```

第一阶段采用：

- shell 安装引导：只负责 `gh api | bash` 认证安装引导。
- Go CLI：承载操作、策略、适配器、事件日志、反馈分析和结构化输出。

AgenticCLI 操作是成熟固化交互逻辑的原子化入口。只有输入输出稳定、失败码明确、边界可审计、可以安全重试或恢复的交互逻辑，才应沉淀为操作。脚本入口只做受控编排或调用，不承载业务判断。仍在探索的流程判断先保留在框架、运行手册、工作流配置、策略和反馈建议中，经过复盘和人工确认后再固化。

## 3. 目标目录

```text
packages/agentic-cli/
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
```

以上是目标结构。当前已实现 `cli`、`config`、`contract`、`evidence`、`feedback`、`jira`、`output`、`policy` 和 `workspace` 的本地模拟流程；`git`、`github` 等真实集成目录仍属于后续阶段。

操作契约的机器可读源头在 `install-resources/basic/contracts/operations/`。Go CLI 可以在构建或运行时读取这些契约，但不在 package 内维护第二份契约源头。

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

`agentic-cli` 运行时不得依赖本地 Python、`jq` 或 shell 业务脚本。

## 5. 输入输出规则

CLI 必须遵守：

- stdout 只输出结构化 JSON。
- stderr 输出人类诊断日志。
- 所有失败返回稳定 `code`。
- 退出码有固定语义。
- 写操作必须检查策略、门禁和人工确认。
- secrets 不允许出现在 stdout、stderr 或事件日志中。

示例输出：

```json
{
  "ok": false,
  "operation": "takeover_task",
  "code": "missing_target_repo",
  "message": "Jira 卡片缺少目标仓库信息",
  "required_human_action": "请补充 target_repo 或 工作空间代码仓库 映射"
}
```

## 6. 受控操作

以下动作必须由 CLI 防护管控：

- Jira 写评论。
- Jira 状态推进。
- Git 提交。
- Git 推送。
- Git 合并。
- Git 变基。
- Git 清理。
- GitHub 拉取请求创建。
- GitHub 拉取请求更新。
- 拉取请求审查意见修复后的重新提交。

## 7. 预检

`agentic-cli preflight` 应检查：

- OS 和 CPU 架构。
- 当前二进制版本。
- Git 是否可用。
- GitHub CLI 是否可用。
- GitHub 登录状态。
- Jira 凭证配置。
- 工作流配置完整性。
- `.agentic-ops/agent.json`、`.agentic-ops/profile.local.yaml` 和 `AGENTS.md` 管理块完整性。
- `source_root` 是否存在。
- 当前业务仓库与工作空间是否匹配。

## 8. 发布与修复

AgenticOps 面向公司研发分发，运行时必须支持快速修复和快速升级。

正式使用前的问题分类、诊断、发布、同步和回滚路径见 `docs/runtime/problem-resolution-and-update.md`。

第一阶段安装资源流程应满足：

- `scripts/build.sh` 生成多平台二进制到 `install-resources/<os-arch>/agentic-cli`。
- 安装脚本 clone 或更新 `~/.agentic-ops` managed clone，并按 OS 和 CPU 架构复制已编译二进制到 `bin/agentic-cli`。
- 版本号使用 `STATE-vMAJOR.ITERATION.COMMIT_INDEX-COMMIT` 格式，例如 `INS-v0.1.3-a68372d`。
- `agentic-cli version` 能输出当前版本、`version_state`、`iteration_version`、`commit_index`、commit 和构建时间。
- `version_state` 必须区分 `SRC` 和 `INS`，分别表示源码运行和已编译安装资源。
- build version 由脚本自动生成，不允许手工指定。
- `scripts/install.sh` 能更新到 latest；有新版本时推荐自动更新应用。
- 安装和升级不得覆盖用户本地配置。
- 项目采用 latest-only 支持策略，BUG 只在最新版本修复，不维护旧版本补丁线。
- 安装失败或新版本不可用时，通过 `.local/previous-ref` 回退本地 clone，不作为旧版本修复策略。

## 9. 语言边界

Go 是 AgenticOps 主 CLI 的实现语言。

shell 只允许用于：

- `install.sh` 安装引导。
- 轻量环境检测。
- managed clone 更新、校验安装资源和复制当前平台已编译 Go 二进制。

shell 不允许承载：

- Jira / GitHub / Git 业务操作。
- 操作契约解析。
- 工作流配置校验。
- 策略和人工门禁判断。
- 证据生成。
- 反馈分析。

## 10. 原子操作成熟度

新增或调整 CLI 操作前，必须判断它是否已经足够成熟。

成熟操作应满足：

- 只完成一个清晰动作。
- 输入、输出、失败码和副作用稳定。
- 能通过策略和门禁拒绝高风险动作。
- 能写入结构化事件日志和证据。
- 失败后能说明是否重试、重做或转人工。
- 能被单元测试、契约检查或端到端模拟流程验证。

不成熟逻辑不得直接写成脚本或 CLI 命令。它应先进入运行手册、工作流配置、策略草案或反馈建议，由 AIAgent 在具体任务中执行并沉淀经验；当重复出现且边界清晰后，再升级为原子操作。脚本只能用于安装引导、构建发布、轻量检测或调用受控操作。
