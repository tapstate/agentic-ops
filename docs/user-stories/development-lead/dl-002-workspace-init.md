# DL-002 初始化项目 AI 工作空间

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

### 保护行为

- `workspace init` 必须在项目 AI 工作空间目录内执行。
- 工作空间配置必须显式绑定 Jira 用户、Jira 空间、仓库映射和本地源码根目录。
- 具体项目运行产物必须写入项目 AI 工作空间，不能写入 `~/.agentic-ops`。
- 目标仓库选择必须来自 workflow profile 或 Jira 字段映射，不能由 AIAgent 临场猜测。

### 审核问题

- 当前目录是否是项目 AI 工作空间，而不是 AgenticOps 源头仓库或 `~/.agentic-ops`。
- Jira 空间到代码仓库的映射是否完整。
- 本地源码目录是否可访问。
- 工作空间预检失败时是否能输出缺失配置。

### 验收证据

- `agentic-cli workspace init --workspace <name> --jira-user <user> --jira-project <project>` 输出。
- `agentic-cli preflight --workspace <name>` 输出。
- 项目 AI 工作空间中的 `.agentic-ops/runs/` 和 `.agentic-ops/feedback/`。
- workflow profile 中的 Jira / GitHub / 本地路径映射。

### 关联设计

- `docs/architecture/project-structure.md`
- `docs/profiles/workflow-profile.md`
- `docs/project-rules.md`
- `docs/runtime/cli-runtime.md`
