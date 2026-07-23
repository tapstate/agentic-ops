# 研发负责人故事

## 1. 范围

本文记录 AgenticOps 研发负责人故事。研发负责人是在具体业务项目中使用 AgenticOps 管理 AIAgent 执行 Jira 任务的人，负责安装、初始化、配置工作空间、触发任务接管、确认人工门禁并验收任务证据。

本文只记录故事线，不记录实施计划、checkbox、当前完成度或剩余工作。

不同任务可以进入不同流程，但每个流程都必须记录执行过程，并在关键阶段回写状态、信息和证据，便于后续分析和优化。

AgenticOps 的研发负责人故事需要同时约束三类对象：

- 研发负责人：用自然语言或 CLI 快速操作 AI 员工，并确认高风险动作。
- AIAgent：按 AI 员工手册和 操作契约工作。
- `agentic-cli`：作为 Go CLI 运行时执行门禁、策略、流程选择、证据回写和事件记录。

## 2. DL-001 安装 AgenticOps

作为研发负责人，
我希望能通过一条安装命令安装 AgenticOps，
以便在本机获得 `agentic-cli`、AI 员工手册、全局配置模板、操作契约和通用技能。

### 触发方式

```sh
curl -fsSL https://raw.githubusercontent.com/tapstate/agentic-ops/init.sh | bash
```

### 前置条件

- 当前系统为 Linux、macOS Intel 或 macOS Apple Silicon。
- 本机可访问 `tapstate/agentic-ops`。
- 本机具备基础 shell 环境，用于执行安装引导。

### 主流程

1. 安装脚本识别 OS 和 CPU 架构。
2. 安装脚本检查 bootstrap 依赖：`bash`、`curl` 和系统解压工具。
3. 安装脚本创建或更新 `~/.agentic-ops`。
4. 安装脚本下载或更新当前平台对应的 `agentic-cli` Go release 二进制。
5. 安装脚本安装统一入口 `agentic-cli`。
6. 安装脚本初始化全局配置模板。
7. 安装脚本输出下一步命令。

### 输出

```json
{
  "ok": true,
  "operation": "install",
  "install_dir": "~/.agentic-ops",
  "bin": "~/.agentic-ops/bin/agentic-cli",
  "next_action": "workspace_init"
}
```

### 失败处理

- 如果缺少依赖，输出缺少的工具和安装建议。
- 如果无法访问仓库，输出网络或权限原因。
- 如果 `~/.agentic-ops` 已存在，支持安全更新，不覆盖用户本地配置。
- 不允许把 secrets 写入安装日志。

### 验收标准

- Linux、macOS Intel 和 macOS Apple Silicon 都能执行安装命令。
- 安装后 `agentic-cli --version` 可用。
- 安装后 `agentic-cli preflight` 可用。
- 安装目录是 `~/.agentic-ops`。
- `~/.agentic-ops` 只保存全局安装和配置资料，不作为具体项目运行目录。

## 3. DL-002 初始化项目 AI 工作空间

作为研发负责人，
我希望能为具体项目初始化 AI 工作空间，
以便 AgenticOps 知道该项目对应的 Jira 用户、Jira 空间、代码仓库集合、本地源码目录、工作流配置和任务执行记录位置。

### 触发方式

```sh
cd <project-ai-workspace>
agentic-cli workspace init --workspace tapstate --jira-user dev@example.com --jira-project TAP
```

### 前置条件

- AgenticOps 已安装。
- 研发负责人已确定当前项目 AI 工作空间名称，例如 `tapstate` 或 `tapdata`。
- 当前 shell 已进入项目 AI 工作空间目录。
- 本机可以访问对应项目的 GitHub 仓库和 Jira 空间。
- 研发负责人已明确 Jira 用户、Jira 空间，以及该 Jira 空间对应的一组代码仓库。

### 主流程

1. CLI 读取 `~/.agentic-ops` 中的全局配置和默认模板。
2. CLI 确认当前执行目录是项目 AI 工作空间。
3. CLI 创建项目 AI 工作空间配置。
4. CLI 引导研发负责人配置 Jira 用户、Jira 空间、JQL、`owner` 字段、状态映射和目标仓库映射。
5. CLI 引导研发负责人配置 GitHub 组织、默认代码仓库、按 `component` / `label` / `issue_type` 的仓库映射和本地源码根目录。
6. CLI 创建工作空间事件目录，例如 `<project-ai-workspace>/.agentic-ops/runs/`。
7. CLI 运行工作空间预检。

### 输出

```json
{
  "ok": true,
  "operation": "workspace_init",
  "workspace": "tapstate",
  "workspace_root": "<project-ai-workspace>",
  "jira_user": "dev@example.com",
  "jira_project": "TAP",
  "profile": "tapstate",
  "runs_dir": "<project-ai-workspace>/.agentic-ops/runs",
  "next_action": "init_agent_capability"
}
```

### 失败处理

- Jira 配置缺失时，停止并要求补充。
- GitHub 登录状态不可用时，提示执行 `gh auth login`。
- 本地源码目录不存在时，提示克隆或配置正确路径。
- 工作流配置不完整时，输出缺失字段。

### 验收标准

- 一个工作空间能绑定一个具体 Jira 空间和一组 GitHub 仓库。
- 不同工作空间可以使用不同 Jira / GitHub / 代码仓库配置。
- 工作空间产物写入项目 AI 工作空间，不写入 `~/.agentic-ops`。
- Jira 空间到代码仓库的映射由工作流配置维护，AIAgent 不得在接管真实卡片时猜测目标仓库。
- `agentic-cli preflight --workspace <name>` 能验证工作空间可用性。

## 4. DL-003 初始化 AIAgent 能力

作为研发负责人，
我希望能初始化当前 AIAgent 的 AgenticOps 能力，
以便 AIAgent 知道 AI 员工手册、可用操作、停止条件、人工确认点和工具调用方式。

### 触发方式

```sh
agentic-cli agent init --workspace tapstate
```

或由研发负责人在 AIAgent 会话中输入：

```text
初始化 AgenticOps 能力，工作空间是 tapstate。
```

### 前置条件

- AgenticOps 已安装。
- 项目 AI 工作空间已初始化。
- AIAgent 当前会话可以读取本地文件并调用 `agentic-cli`。

### 主流程

1. AIAgent 读取 AI 员工手册。
2. AIAgent 读取任务类型、阶段和下一步动作规则。
3. AIAgent 读取 工作流配置摘要。
4. AIAgent 读取 操作契约列表。
5. AIAgent 执行 `agentic-cli preflight --workspace tapstate`。
6. AIAgent 向研发负责人输出当前可用能力、阶段判断方式和限制。

### 输出

```json
{
  "ok": true,
  "operation": "agent_init",
  "workspace": "tapstate",
  "task_model": {
    "type_source": "operation_contract",
    "stage_source": "event_log",
    "next_action_source": "operation_result"
  },
  "capabilities": [
    "list_tasks",
    "takeover_task",
    "resume_takeover",
    "write_evidence",
    "prepare_pr",
    "feedback_report"
  ],
  "human_gates": [
    "push",
    "create_pr",
    "merge",
    "scope_change"
  ]
}
```

### 失败处理

- 如果 AIAgent 无法读取手册，停止并提示安装或路径问题。
- 如果 `agentic-cli` 不可用，提示重新安装或修复 PATH。
- 如果 `workspace preflight` 失败，AIAgent 不能开始接管任务。

### 验收标准

- AIAgent 能明确说明任务类型、阶段判断方式和可执行操作。
- AIAgent 能明确说明哪些动作必须人工确认。
- AIAgent 知道不能直接面对 Jira 字段和状态，必须通过操作契约和 CLI 工作。
- 初始化完成后，研发负责人可以直接说“列出我的任务”或“接管 TAP-123”。

## 5. DL-004 新任务接管

作为研发负责人，
我希望能让 AIAgent 接管一个新的 Jira 卡片，
以便 AI 员工在完成门禁后开始读取上下文、制定计划、开发、验证并回写证据。

### 触发方式

```sh
agentic-cli takeover-task TAP-123 --workspace tapstate
```

或自然语言：

```text
接管 TAP-123。
```

### 前置条件

- AIAgent 能力已初始化。
- Jira 卡片已进入迭代。
- 当前 Jira 用户和卡片负责人匹配。
- Jira 卡片具备需求范围、验收标准、目标仓库和验证方式。

### 主流程

1. AIAgent 调用 `takeover_task` 操作。
2. CLI 执行负责人、迭代、需求、风险、目标仓库、验证方式和权限门禁。
3. 门禁通过后，CLI 生成 `run_id`。
4. CLI 写入接管成功证据。
5. CLI 返回目标仓库、验证命令、任务摘要和下一步。
6. AIAgent 读取目标仓库上下文。
7. AIAgent 输出开发计划和风险点。
8. AIAgent 在允许范围内修改代码。
9. AIAgent 运行最小验证。
10. AIAgent 回写开发证据。
11. AIAgent 停在人工确认点，等待研发负责人确认推送或创建拉取请求。

### 输出

```json
{
  "ok": true,
  "operation": "takeover_task",
  "workspace": "tapstate",
  "issue_key": "TAP-123",
  "run_id": "TAP-123-takeover-20260721103012-a8f3",
  "task_type": "task_takeover",
  "current_stage": "takeover_started",
  "target_repo": "tapstate/example-repo",
  "next_action": "proceed"
}
```

### 失败处理

- 负责人不匹配时，停止，不写开发证据。
- 缺少验收标准、目标仓库或验证方式时，写接管失败证据。
- 权限不足时，返回 `missing_permission`。
- 风险边界不清时，要求人工确认。

### 验收标准

- 单次任务接管只处理一个 Jira 卡片。
- 接管成功和失败都有结构化记录。
- 每次接管都有唯一 `run_id`。
- AIAgent 未经确认不得推送或创建拉取请求。
- 所有操作都写入结构化事件日志。
- 写入 Jira 的接管成功、失败、阻塞和补卡说明必须使用中文。

## 6. DL-005 恢复接管任务

作为研发负责人，
我希望能恢复一个已接管但未完成的任务，
以便 AIAgent 继续同一个 `run_id` 的上下文，而不是重新开始或混淆多次执行记录。

### 触发方式

```sh
agentic-cli resume-takeover --run-id TAP-123-takeover-20260721103012-a8f3 --workspace tapstate
```

或自然语言：

```text
恢复 TAP-123 上次的接管任务。
```

### 前置条件

- 已存在接管记录。
- `run_id` 对应的 `issue`、`workspace`、`task_type`、`current_stage` 和目标仓库可验证。
- 本地工作区仍能定位到相关代码状态。

### 主流程

1. AIAgent 调用 `resume_takeover` 操作。
2. CLI 读取 `run_id` 对应的 run summary 和 events。
3. CLI 校验当前 `workspace`、`issue`、负责人、目标仓库和本地分支状态。
4. CLI 返回上次阶段、已完成动作、失败原因、下一步建议。
5. AIAgent 向研发负责人简短说明恢复点。
6. AIAgent 从恢复点继续执行，而不是重新生成新的接管记录。

### 输出

```json
{
  "ok": true,
  "operation": "resume_takeover",
  "workspace": "tapstate",
  "issue_key": "TAP-123",
  "run_id": "TAP-123-takeover-20260721103012-a8f3",
  "previous_stage": "verification_failed",
  "current_stage": "verification_failed",
  "next_action": "fix_and_verify"
}
```

### 失败处理

- `run_id` 不存在时，提示可恢复的最近 run。
- 当前 `workspace` 与 `run_id` 不匹配时，拒绝恢复。
- 本地代码状态不一致时，要求研发负责人确认。
- 如果上次失败原因属于人工确认点，AIAgent 不能自动继续。

### 验收标准

- 恢复任务不会创建新的 `run_id`。
- 恢复前必须校验 `workspace`、`issue`、负责人和目标仓库一致。
- AIAgent 能说明从哪个阶段恢复。
- 恢复过程继续写入同一个 run 的事件日志。

## 7. DL-006 任务完成审计与反馈分析

作为研发负责人，
我希望 AIAgent 完成、阻塞或交接一个任务时立即提交任务级审计记录，
以便团队能按任务事实源追踪 AI 员工执行情况，并在需要时分析阻塞点、重复问题和 AgenticOps 改进机会。

### 触发方式

```sh
agentic-cli write-evidence --workspace tapstate --run-id <run_id>
agentic-cli release-agent --workspace tapstate --run-id <run_id> --issue-key TAP-123 --completion-evidence evidence.md
agentic-cli feedback bundle --workspace tapstate --run-id <run_id> --redact
agentic-cli feedback report --workspace tapstate --date 2026-07-21
agentic-cli feedback analyze --workspace tapstate --date 2026-07-21
agentic-cli feedback propose --workspace tapstate --date 2026-07-21
```

或自然语言：

```text
提交 TAP-123 本次执行的任务审计记录。
按需分析 tapstate 工作空间最近的 AI 执行记录，并给出 AgenticOps 改进建议。
```

### 前置条件

- 工作空间中存在对应 `run_id` 的事件日志和证据。
- Jira 卡片、审计服务或目标仓库证据链可作为任务级审计记录提交目标。
- 事件日志使用安全摘要，不包含 secrets、原始敏感日志、完整 Jira 描述或敏感代码片段。

### 主流程

1. AIAgent 在完成、阻塞或交接节点整理当前 `run_id` 的任务审计摘要。
2. CLI 写入证据，并在完成或交接后执行 `release-agent` 清理 `current_agent_id`。
3. AIAgent 将审计记录提交到 Jira 卡片、审计服务或目标仓库证据链。
4. 需要诊断时，CLI 生成脱敏 `feedback bundle`。
5. 需要复盘时，CLI 按需生成 `feedback report`。
6. AIAgent 基于任务审计记录和按需报告输出改进建议。
7. 改进建议进入 `Observation -> Proposal -> Accepted Change` 流程。
8. 未经人工确认，不自动修改 AgenticOps 源头规则。

### 输出

```json
{
  "ok": true,
  "operation": "feedback_report",
  "workspace": "tapstate",
  "date": "2026-07-21",
  "runs": 8,
  "succeeded": 5,
  "blocked": 2,
  "failed": 1,
  "report": "<project-ai-workspace>/.agentic-ops/feedback/reports/2026-07-21.md",
  "next_action": "review_proposals"
}
```

### 失败处理

- 没有事件日志时，提示检查工作空间反馈日志。
- 发现疑似敏感内容时，停止生成报告并提示需要脱敏。
- 发现重复失败码时，生成 proposal，但不自动修改源头规则。

### 验收标准

- 完成、阻塞或交接时能提交任务级审计记录。
- 能按需按 `workspace`、时间范围、失败码或任务类型生成反馈分析报告。
- 报告包含成功、失败、阻塞、人工确认点和重复问题。
- 报告不包含 secrets 或敏感原始内容。
- 写入 Jira 的工作日志必须使用中文。
- 改进建议必须经过人工确认后才能进入 AgenticOps 源头仓库。
