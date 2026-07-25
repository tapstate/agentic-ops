# 端到端演示脚本

## 1. 目的

本文定义 AgenticOps 第一阶段端到端演示脚本。演示主线必须使用真实 Jira 卡片，展示研发负责人如何在真实任务上完成受控接管、开发、验证和证据回写。本地模拟流程只作为自动化回归验证，不作为对外演示主线。

## 2. 演示目标

演示研发负责人如何使用 AIAgent 完成一个受控任务接管：

```text
安装
-> 初始化工作空间
-> 初始化 AIAgent 能力
-> 接管新任务
-> 本地开发和验证
-> 回写证据
-> 等待人工确认拉取请求
-> 上报工作日志
```

## 3. 演示场景

演示任务必须来自真实 Jira 卡片，例如 `TAP-123`。演示前可以使用脱敏标题和描述，但卡片本身必须存在于真实 Jira 空间，并具备负责人、验收标准、目标仓库或仓库映射依据。

示例卡片标题：

```text
CLI 命令 new 改为 create
```

该标题只作为演示故事，不应成为 AgenticOps 架构边界。

## 4. 前置条件

演示前应准备：

- 一个真实 Jira 卡片，可以脱敏展示。
- 明确负责人。
- 明确目标仓库。
- 明确验收标准。
- 明确验证方式。
- 一个本地项目 AI 工作空间，例如 `tapstate`。
- 当前执行目录已经进入项目 AI 工作空间。
- Jira 用户、Jira 空间和该 Jira 空间对应的代码仓库映射已明确。

## 5. 脚本流程

### 步骤 1：全局安装

```sh
gh auth status
gh api -H 'Accept: application/vnd.github.raw' \
  '/repos/tapstate/agentic-ops/contents/scripts/install.sh?ref=main' \
  | AGENTIC_OPS_REPO_URL='git@github.com:tapstate/agentic-ops.git' bash
```

期望说明：

- 安装到 `~/.agentic-ops`。
- 该目录不是具体项目运行目录。
- 安装动作是全局动作，不绑定具体 Jira 空间或代码仓库。
- 安装入口通过 GitHub CLI 认证读取私有仓库脚本，不依赖匿名 raw URL。
- 已安装时，脚本会要求研发负责人确认后才更新 `~/.agentic-ops`。
- 安装脚本使用 `install-resources/<os-arch>/agentic-cli` 中已经编译并提交到仓库的产物，不在研发负责人机器上编译。

### 步骤 2：初始化工作空间

```sh
cd <project-ai-workspace>
agentic-cli workspace init --project tapstate --jira-user dev@example.com
```

期望说明：

- `workspace init` 在项目 AI 工作空间目录内执行。
- 工作空间绑定 Jira 用户和项目配置项。
- 工作空间从项目配置项读取 Jira 空间、GitHub 仓库和本地源码目录。
- 工作空间创建 `.agentic-ops/runs`、`.agentic-ops/run-logs` 和 `.agentic-ops/feedback`。

### 步骤 3：按全局指引启用 AgenticOps

```text
按 ~/.agentic-ops/agent-guides.md 启用 AgenticOps。
```

期望说明：

- AIAgent 先读取 `~/.agentic-ops/agent-guides.md`。
- AIAgent 加载 AI 员工手册。
- AIAgent 加载 操作契约。
- AIAgent 说明人工确认点。

### 步骤 4：接管任务

```text
接管 TAP-123。
```

期望说明：

- 门禁通过后生成 `run_id`。
- 真实卡片的 `target_repo` 来自 Jira 字段或工作流配置中的 `workspace_repo_mapping`。
- 写入接管成功证据。
- AIAgent 输出计划和风险点。

### 步骤 5：开发与验证

```text
请按计划修改并运行最小验证。
```

期望说明：

- AIAgent 只修改当前卡片范围内内容。
- AIAgent 记录测试结果。
- AIAgent 不自动推送或创建拉取请求。

### 步骤 6：写入证据

```text
回写本次开发证据。
```

期望说明：

- 证据包含变更摘要、验证结果、残留风险和下一步。

### 步骤 7：研发负责人确认

```text
我确认本地结果，可以准备 PR。
```

期望说明：

- AIAgent 可以进入 `prepare_pr` 操作。
- 未确认前不得推送或创建拉取请求。

### 步骤 8：任务审计与按需反馈分析

```text
提交 TAP-123 本次执行的任务审计记录。
按需分析 tapstate 工作空间最近的 AI 执行记录，并给出 AgenticOps 改进建议。
```

期望说明：

- 任务级审计记录回写 Jira 卡片、审计服务或目标仓库证据链。
- 需要时生成反馈分析报告。
- 生成改进建议。
- 不自动修改 AgenticOps 源头规则。

## 6. 演示验收

演示成功标准：

- 能解释 `~/.agentic-ops` 和项目 AI 工作空间的区别。
- 能解释 AIAgent 为什么不直接面对 Jira 工作流。
- 能展示任务接管门禁。
- 能展示 `run_id` 和证据。
- 能展示人工确认点。
- 能展示反馈报告的输入和输出。

第一阶段本地模拟流程验证命令：

```sh
bash tests/e2e/local-fake-flow.sh
```

该命令使用模拟 Jira 数据跑通本地 CLI 闭环，不执行真实 Jira 或 GitHub 写操作；它是自动化回归验证，不替代真实 Jira 卡片演示。

第一阶段本地安装闭环验证命令：

```sh
bash tests/e2e/local-install-flow.sh
```

该命令会准备临时 managed clone，通过 `scripts/install.sh` 安装到临时 `~/.agentic-ops`，再使用安装后的 `agentic-cli` 完成工作空间初始化、AIAgent 配置生成、AIAgent 初始化和预检。
