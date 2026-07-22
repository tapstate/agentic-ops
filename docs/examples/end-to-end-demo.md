# 端到端演示脚本

## 1. 目的

本文定义 AgenticOps 第一阶段端到端演示脚本。当前已提供本地 fake flow 验证脚本，用于演示 CLI 最小闭环；真实 Jira / GitHub 写操作仍不执行。

## 2. 演示目标

演示研发 owner 如何使用 AIAgent 完成一个受控任务接管：

```text
安装
-> 初始化工作空间
-> 初始化 AIAgent 能力
-> 接管新任务
-> 本地开发和验证
-> 回写 evidence
-> 等待人工确认 PR
-> 上报工作日志
```

## 3. 演示场景

示例任务：

```text
CLI 命令 new 改为 create
```

该任务只作为演示故事，不应成为 AgenticOps 架构边界。

## 4. 前置条件

演示前应准备：

- 一个脱敏 Jira issue。
- 明确 owner。
- 明确目标仓库。
- 明确验收标准。
- 明确验证方式。
- 一个本地项目 AI 工作空间，例如 `tapstate`。

## 5. 脚本流程

### 步骤 1：安装

```sh
curl -fsSL https://raw.githubusercontent.com/tapstate/agentic-ops/init.sh | bash
```

期望说明：

- 安装到 `~/.agentic-ops`。
- 该目录不是具体项目运行目录。

### 步骤 2：初始化工作空间

```sh
agent-task-ops workspace init --workspace tapstate
```

期望说明：

- workspace 绑定 Jira 空间。
- workspace 绑定 GitHub organization / repo。
- workspace 创建 `.agentic-ops/runs` 和 `.agentic-ops/feedback`。

### 步骤 3：初始化 AIAgent

```text
初始化 AgenticOps 能力，工作空间是 tapstate。
```

期望说明：

- AIAgent 加载 AI 员工手册。
- AIAgent 加载 Operation Contract。
- AIAgent 说明人工确认点。

### 步骤 4：接管任务

```text
接管 TAP-123。
```

期望说明：

- gate 通过后生成 `run_id`。
- 写入接管成功 evidence。
- AIAgent 输出计划和风险点。

### 步骤 5：开发与验证

```text
请按计划修改并运行最小验证。
```

期望说明：

- AIAgent 只修改当前 issue 范围内内容。
- AIAgent 记录测试结果。
- AIAgent 不自动 push / PR。

### 步骤 6：写入证据

```text
回写本次开发 evidence。
```

期望说明：

- evidence 包含变更摘要、验证结果、残留风险和下一步。

### 步骤 7：研发负责人确认

```text
我确认本地结果，可以准备 PR。
```

期望说明：

- AIAgent 可以进入 `prepare_pr` operation。
- 未确认前不得执行 push / PR。

### 步骤 8：每日反馈

```text
汇总今天 tapstate 工作空间的 AI 执行日志，并给出 AgenticOps 改进建议。
```

期望说明：

- 生成 feedback report。
- 生成 proposal。
- 不自动修改 AgenticOps 源头规则。

## 6. 演示验收

演示成功标准：

- 能解释 `~/.agentic-ops` 和项目 AI 工作空间的区别。
- 能解释 AIAgent 为什么不直接面对 Jira workflow。
- 能展示任务接管 gate。
- 能展示 `run_id` 和 evidence。
- 能展示人工确认点。
- 能展示 feedback report 的输入和输出。

第一阶段本地 fake flow 验证命令：

```sh
bash tests/e2e/local-fake-flow.sh
```

该命令使用 fake Jira 数据跑通本地 CLI 闭环，不执行真实 Jira 或 GitHub 写操作。
