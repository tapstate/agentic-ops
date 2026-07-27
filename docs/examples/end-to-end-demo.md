# 端到端演示脚本

## 1. 目的

本文定义 AgenticOps 第一阶段端到端演示脚本。演示主线必须使用真实 Jira 卡片，展示研发工程师如何在真实任务上完成受控接管、开发、验证和证据回写。本地模拟流程只作为自动化回归验证，不作为对外演示主线。

## 2. 演示目标

演示研发工程师如何使用 AIAgent 完成一个受控缺陷任务接管：

```text
安装
-> 初始化工作空间
-> 初始化 AIAgent 能力
-> 准入分析与 Jira 补卡
-> 重新检查并接管任务
-> 修复计划写入 Jira 并确认
-> 本地开发和验证
-> 结构化结论和证据回写
-> 等待人工确认拉取请求
-> 上报工作日志
```

## 3. 演示场景

演示任务必须来自真实 Jira 缺陷卡片，例如 `TAP-123`。演示前可以使用脱敏标题和描述，但卡片本身必须存在于真实 Jira 空间。为了展示准入闭环，首次检查时应至少有一项缺陷准入信息不足。

示例卡片标题：

```text
任务启动后重复输出同一告警
```

该标题只作为演示故事，不应成为 AgenticOps 架构边界。

## 4. 前置条件

演示前应准备：

- 一个真实 Jira 卡片，可以脱敏展示。
- 明确负责人。
- 能通过 Jira 事实或 Tapdata 仓库映射定位候选目标仓库。
- 一个本地 Tapdata 项目 AI 工作空间。
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
- 已安装时，脚本会要求研发工程师确认后才更新 `~/.agentic-ops`。
- 安装脚本使用 `install-resources/<os-arch>/agentic-cli` 中已经编译并提交到仓库的产物，不在研发工程师机器上编译。

### 步骤 2：初始化工作空间

```sh
cd <project-ai-workspace>
agentic-cli workspace init --project tapdata --interactive
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

### 步骤 4：首次准入检查

```text
接管 TAP-123。
```

期望说明：

- AIAgent 先执行 `inspect-task`，不直接调用 `takeover-task`。
- AIAgent 一次性列出全部缺失或冲突项，并结合候选仓库和目标分支代码形成准入分析。
- 研发工程师确认真实写入后，AIAgent 使用 `add-task-comment --category analysis` 写入 Jira。
- 写入后 AIAgent 结束本次接管，不自动绑定任务。

### 步骤 5：补卡确认

```text
我确认补卡建议，请更新 Jira。
```

期望说明：

- AIAgent 使用 `update-task-description-sections` 更新问题分支、修复分支、问题现象、复现路径和验收标准。
- AIAgent 使用 `add-task-comment --category decision` 记录确认结果。
- AIAgent 再次结束本次接管。

### 步骤 6：重新检查并接管

```text
重新接管 TAP-123。
```

期望说明：

- AIAgent 重新执行 `inspect-task`，不复用补卡前的判断。
- 准入通过后，AIAgent 执行 `takeover-task` 并获得 `run_id`。
- CLI 只执行负责人、代理所有权、任务分类、标准流程、状态入口和真实 Jira 写入门禁。

### 步骤 7：修复计划确认

期望说明：

- AIAgent 结合 Jira 和代码形成版本化修复计划。
- 计划包含根因与证据、修改和不修改范围、目标模块或文件、实施步骤、测试与验收映射、风险与回滚。
- AIAgent 使用 `add-task-comment --category plan --run-id <run_id>` 写入 Jira，然后停止代码修改。
- 研发工程师确认后，AIAgent 使用 `add-task-comment --category decision` 写入确认结果。

### 步骤 8：开发与验证

```text
请按计划修改并运行最小验证。
```

期望说明：

- AIAgent 只修改当前卡片范围内内容。
- AIAgent 记录测试结果。
- AIAgent 不自动推送或创建拉取请求。

### 步骤 9：写入结构化结论和证据

```text
回写本次开发证据。
```

期望说明：

- AIAgent 使用 `update-task-form` 更新问题分析、修复详情和测试计划。
- AIAgent 使用 `add-task-comment --category evidence` 写入变更摘要、验证结果、验收映射、残留风险和下一步。

### 步骤 10：研发工程师确认

```text
我确认本地结果，可以准备 PR。
```

期望说明：

- AIAgent 可以进入 `prepare_pr` 操作。
- 未确认前不得推送或创建拉取请求。

### 步骤 11：任务审计与按需反馈分析

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
- 能展示准入失败后的代码分析、Jira 补卡评论和重新检查。
- 能展示修复计划与研发工程师确认都写入 Jira，且确认前没有代码修改。
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
