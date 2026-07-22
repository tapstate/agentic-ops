# AgenticOps 用户故事

## 1. 范围

本文记录 AgenticOps 第一阶段用户故事。故事围绕研发 owner 操作 AI 员工从 Jira 接管任务到完成任务的主链路展开，覆盖安装、工作空间初始化、AIAgent 能力初始化、新任务接管、恢复接管任务和工作日志上报。

不同任务可以进入不同流程，但每个流程都必须记录执行过程，并在关键阶段回写状态、信息和证据，便于后续分析和优化。

AgenticOps 的用户故事需要同时约束三类对象：

- 研发 owner：用自然语言或 CLI 快速操作 AI 员工。
- AIAgent：按 AI 员工手册和 Operation Contract 工作。
- `agent-task-ops`：作为 Go CLI Runtime 执行 gate、policy、流程选择、证据回写和事件记录。

## 2. US-001 安装 AgenticOps

作为研发 owner，  
我希望能通过一条安装命令安装 AgenticOps，  
以便在本机获得 `agent-task-ops`、AI 员工手册、全局配置模板、operation contracts 和通用 skills。

### 触发方式

```sh
curl -fsSL https://raw.githubusercontent.com/tapstate/agentic-ops/init.sh | bash
```

### 前置条件

- 当前系统为 Linux、macOS Intel 或 macOS Apple Silicon。
- 本机可访问 `tapstate/agentic-ops`。
- 本机具备基础 shell 环境，用于执行安装 bootstrap。

### 主流程

1. 安装脚本识别 OS 和 CPU 架构。
2. 安装脚本检查 bootstrap 依赖：`bash`、`curl` 和系统解压工具。
3. 安装脚本创建或更新 `~/.agentic-ops`。
4. 安装脚本下载或更新当前平台对应的 `agent-task-ops` Go release 二进制。
5. 安装脚本安装统一入口 `agent-task-ops`。
6. 安装脚本初始化全局配置模板。
7. 安装脚本输出下一步命令。

### 输出

```json
{
  "ok": true,
  "operation": "install",
  "install_dir": "~/.agentic-ops",
  "bin": "~/.agentic-ops/bin/agent-task-ops",
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
- 安装后 `agent-task-ops --version` 可用。
- 安装后 `agent-task-ops preflight` 可用。
- 安装目录是 `~/.agentic-ops`。
- `~/.agentic-ops` 只保存全局安装和配置资料，不作为具体项目运行目录。

## 3. US-002 初始化项目 AI 工作空间

作为研发 owner，  
我希望能为具体项目初始化 AI 工作空间，  
以便 AgenticOps 知道该项目对应的 Jira 空间、GitHub 组织/仓库、本地源码目录、workflow profile 和任务执行记录位置。

### 触发方式

```sh
agent-task-ops workspace init --workspace tapstate
```

### 前置条件

- AgenticOps 已安装。
- 研发 owner 已确定当前项目 AI 工作空间名称，例如 `tapstate` 或 `tapdata`。
- 本机可以访问对应项目的 GitHub 仓库和 Jira 空间。

### 主流程

1. CLI 读取 `~/.agentic-ops` 中的全局配置和默认模板。
2. CLI 创建项目 AI 工作空间配置。
3. CLI 引导研发 owner 配置 Jira project、JQL、owner 字段、状态映射和目标仓库映射。
4. CLI 引导研发 owner 配置 GitHub organization、默认 repo 和本地源码根目录。
5. CLI 创建工作空间事件目录，例如 `<project-ai-workspace>/.agentic-ops/runs/`。
6. CLI 运行 workspace preflight。

### 输出

```json
{
  "ok": true,
  "operation": "workspace_init",
  "workspace": "tapstate",
  "profile": "tapstate",
  "runs_dir": "<project-ai-workspace>/.agentic-ops/runs",
  "next_action": "init_agent_capability"
}
```

### 失败处理

- Jira 配置缺失时，停止并要求补充。
- GitHub 登录状态不可用时，提示执行 `gh auth login`。
- 本地源码目录不存在时，提示 clone 或配置正确路径。
- profile 不完整时，输出缺失字段。

### 验收标准

- 一个工作空间能绑定一个具体 Jira 空间和一组 GitHub 仓库。
- 不同工作空间可以使用不同 Jira / GitHub / repo 配置。
- 工作空间产物写入项目 AI 工作空间，不写入 `~/.agentic-ops`。
- `agent-task-ops preflight --workspace <name>` 能验证 workspace 可用性。

## 4. US-003 初始化 AIAgent 能力

作为研发 owner，  
我希望能初始化当前 AIAgent 的 AgenticOps 能力，  
以便 AIAgent 知道 AI 员工手册、可用 operation、停止条件、人工确认点和工具调用方式。

### 触发方式

```sh
agent-task-ops agent init --workspace tapstate
```

或由研发 owner 在 AIAgent 会话中输入：

```text
初始化 AgenticOps 能力，工作空间是 tapstate。
```

### 前置条件

- AgenticOps 已安装。
- 项目 AI 工作空间已初始化。
- AIAgent 当前会话可以读取本地文件并调用 `agent-task-ops`。

### 主流程

1. AIAgent 读取 AI 员工手册。
2. AIAgent 读取任务类型、阶段和下一步动作规则。
3. AIAgent 读取 workspace profile 摘要。
4. AIAgent 读取 Operation Contract 列表。
5. AIAgent 执行 `agent-task-ops preflight --workspace tapstate`。
6. AIAgent 向研发 owner 输出当前可用能力、阶段判断方式和限制。

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
- 如果 `agent-task-ops` 不可用，提示重新安装或修复 PATH。
- 如果 workspace preflight 失败，AIAgent 不能开始接管任务。

### 验收标准

- AIAgent 能明确说明任务类型、阶段判断方式和可执行操作。
- AIAgent 能明确说明哪些动作必须人工确认。
- AIAgent 知道不能直接面对 Jira 字段和状态，必须通过 Operation Contract / CLI 工作。
- 初始化完成后，研发 owner 可以直接说“列出我的任务”或“接管 TAP-123”。

## 5. US-004 新任务接管

作为研发 owner，  
我希望能让 AIAgent 接管一个新的 Jira issue，  
以便 AI 员工在完成 gate 后开始读取上下文、制定计划、开发、验证并回写证据。

### 触发方式

```sh
agent-task-ops takeover-task TAP-123 --workspace tapstate
```

或自然语言：

```text
接管 TAP-123。
```

### 前置条件

- AIAgent 能力已初始化。
- Jira issue 已进入迭代。
- 当前 Jira 用户和 issue owner 匹配。
- Jira issue 具备需求范围、验收标准、目标仓库和验证方式。

### 主流程

1. AIAgent 调用 `takeover_task` operation。
2. CLI 执行 owner、迭代、需求、风险、目标仓库、验证方式和权限 gate。
3. gate 通过后，CLI 生成 `run_id`。
4. CLI 写入接管成功 evidence。
5. CLI 返回目标仓库、验证命令、任务摘要和下一步。
6. AIAgent 读取目标仓库上下文。
7. AIAgent 输出开发计划和风险点。
8. AIAgent 在允许范围内修改代码。
9. AIAgent 运行最小验证。
10. AIAgent 回写开发 evidence。
11. AIAgent 停在人工确认点，等待研发 owner 确认 push / PR。

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

- owner 不匹配时，停止，不写开发 evidence。
- 缺少验收标准、目标仓库或验证方式时，写接管失败 evidence。
- 权限不足时，返回 `missing_permission`。
- 风险边界不清时，要求人工确认。

### 验收标准

- 单次任务接管只处理一个 Jira issue。
- 接管成功和失败都有结构化记录。
- 每次接管都有唯一 `run_id`。
- AIAgent 未经确认不得 push / PR。
- 所有 operation 都写入结构化事件日志。

## 6. US-005 恢复接管任务

作为研发 owner，  
我希望能恢复一个已接管但未完成的任务，  
以便 AIAgent 继续同一个 `run_id` 的上下文，而不是重新开始或混淆多次执行记录。

### 触发方式

```sh
agent-task-ops resume-takeover --run-id TAP-123-takeover-20260721103012-a8f3 --workspace tapstate
```

或自然语言：

```text
恢复 TAP-123 上次的接管任务。
```

### 前置条件

- 已存在接管记录。
- `run_id` 对应的 issue、workspace、task_type、current_stage 和目标仓库可验证。
- 本地工作区仍能定位到相关代码状态。

### 主流程

1. AIAgent 调用 `resume_takeover` operation。
2. CLI 读取 `run_id` 对应的 run summary 和 events。
3. CLI 校验当前 workspace、issue、owner、目标仓库和本地分支状态。
4. CLI 返回上次阶段、已完成动作、失败原因、下一步建议。
5. AIAgent 向研发 owner 简短说明恢复点。
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
- 当前 workspace 与 `run_id` 不匹配时，拒绝恢复。
- 本地代码状态不一致时，要求研发 owner 确认。
- 如果上次失败原因属于人工确认点，AIAgent 不能自动继续。

### 验收标准

- 恢复任务不会创建新的 `run_id`。
- 恢复前必须校验 workspace、issue、owner 和目标仓库一致。
- AIAgent 能说明从哪个阶段恢复。
- 恢复过程继续写入同一个 run 的事件日志。

## 7. US-006 工作日志上报

作为研发 owner，  
我希望 AIAgent 能上报每天的工作日志，  
以便团队分析 AI 员工执行情况、识别阻塞点，并持续优化 AgenticOps 手册、contracts、profiles 和 Go CLI。

### 触发方式

```sh
agent-task-ops feedback collect --workspace tapstate --date 2026-07-21
agent-task-ops feedback analyze --workspace tapstate --date 2026-07-21
agent-task-ops feedback report --workspace tapstate --date 2026-07-21
agent-task-ops feedback propose --workspace tapstate --date 2026-07-21
```

或自然语言：

```text
汇总今天 tapstate 工作空间的 AI 执行日志，并给出 AgenticOps 改进建议。
```

### 前置条件

- 工作空间中存在当天 run events。
- 事件日志使用安全摘要，不包含 secrets、原始敏感日志、完整 Jira 描述或敏感代码片段。

### 主流程

1. CLI 收集当天所有 `events.ndjson`。
2. CLI 做脱敏和聚合。
3. CLI 统计执行次数、成功率、失败码、阻塞原因、人工确认点和耗时。
4. CLI 生成日报 JSON。
5. AIAgent 基于日报生成 Markdown 总结。
6. AIAgent 输出改进建议。
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
  "report": "<project-ai-workspace>/.agentic-ops/feedback/daily/2026-07-21.md",
  "next_action": "review_proposals"
}
```

### 失败处理

- 没有事件日志时，输出空报告，不报错。
- 发现疑似敏感内容时，停止生成报告并提示需要脱敏。
- 发现重复失败码时，生成 proposal，但不自动修改源头规则。

### 验收标准

- 每天能按 workspace 生成反馈报告。
- 报告包含成功、失败、阻塞、人工确认点和重复问题。
- 报告不包含 secrets 或敏感原始内容。
- 改进建议必须经过人工确认后才能进入 AgenticOps 源头仓库。
