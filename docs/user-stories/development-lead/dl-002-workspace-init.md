# DL-002 初始化项目 AI 工作空间

作为研发负责人，
我希望能为具体项目初始化 AI 工作空间，
以便 AgenticOps 根据项目配置项找到 Jira 空间、代码仓库集合、本地源码目录、工作流配置和任务执行记录位置。

### 触发方式

```sh
cd <project-ai-workspace>
agentic-cli workspace init --project tapstate --jira-user dev@example.com
```

### 前置条件

- AgenticOps 已安装。
- 研发负责人已确定当前项目配置项，例如 `tapstate` 或 `tapdata`。
- 当前 shell 已进入项目 AI 工作空间目录。
- 本机可以访问对应项目的 GitHub 仓库和 Jira 空间。
- AgenticOps 当前版本中存在对应的项目包 profile，例如 `install-resources/basic/projects/tapdata/profile.yaml`。

### 主流程

1. CLI 读取 `~/.agentic-ops` 中的全局配置和默认模板。
2. CLI 确认当前执行目录是项目 AI 工作空间。
3. CLI 创建项目 AI 工作空间配置。
4. CLI 根据项目配置项加载 workflow profile。
5. CLI 从 workflow profile 读取 Jira project、默认 Jira base URL、JQL、字段映射、状态映射、目标仓库映射、GitHub 组织和本地路径占位默认值。
6. 使用 `--interactive` 时，CLI 从参数、进程环境变量、当前工作空间配置和个人配置读取已有值，已配置项只需回车确认，缺失项才询问；非终端、脚本或 CI 场景使用完整参数形式。
7. CLI 根据研发负责人提供或确认的 Jira 用户、当前项目 AI 工作空间目录和可选 `--source-root` 准备本地 overlay；未提供 `--source-root` 时默认使用 `<project-ai-workspace>/repos/<project>`，此时尚不写入表示初始化完成的受管文件。
8. CLI 优先复用已有真实 Jira 本地配置；没有本地配置时，在提供、确认或读取到项目默认 Jira base URL 后写入个人配置 `$AGENTIC_OPS_HOME/user/config.local.yaml` 的 `projects.<project>.jira` 分段，只保存 `adapter`、`base_url` 和 `email`。Jira API token 只保存到 `$AGENTIC_OPS_HOME/user/.env` 的 `AGENTIC_OPS_JIRA_API_TOKEN`，不写入 YAML。
9. CLI 检查 `source_root`；目录不存在或为空时，从 workflow profile 的 `github.repositories.default` 下载项目代码并显示进度；目录已存在且非空时直接复用，不覆盖、不拉取、不切换分支。
10. CLI 创建工作空间事件和执行日志目录，例如 `<project-ai-workspace>/.agentic-ops/runs/`、`<project-ai-workspace>/.agentic-ops/run-logs/`。
11. CLI 写入 `.agentic-ops/agent.json` 和根目录 `AGENTS.md`，让 AIAgent 能识别当前项目并知道如何调用 `agentic-cli`。
12. CLI 运行工作空间预检。

### 输出

```json
{
  "ok": true,
  "operation": "workspace_init",
  "workspace": "tapstate",
  "workspace_root": "<project-ai-workspace>",
  "source_root": "<project-ai-workspace>/repos/tapstate",
  "source_repo": "tapstate/example-repo",
  "source_repo_url": "git@github.com:tapstate/example-repo.git",
  "source_checkout_status": "cloned",
  "jira_user": "dev@example.com",
  "jira_project": "TAP",
  "profile_ref": "$HOME/.agentic-ops/install-resources/basic/projects/tapstate/profile.yaml",
  "profile_overlay": "<project-ai-workspace>/.agentic-ops/profile.local.yaml",
  "agent_config": "<project-ai-workspace>/.agentic-ops/agent.json",
  "agent_instructions": "<project-ai-workspace>/AGENTS.md",
  "runs_dir": "<project-ai-workspace>/.agentic-ops/runs",
  "run_logs_dir": "<project-ai-workspace>/.agentic-ops/run-logs",
  "jira_config_status": "needs_jira_api_token",
  "jira_config_path": "$HOME/.agentic-ops/user/config.local.yaml",
  "jira_env_file": "$HOME/.agentic-ops/user/.env",
  "jira_token_env": "AGENTIC_OPS_JIRA_API_TOKEN",
  "jira_token_help_url": "https://id.atlassian.com/manage-profile/security/api-tokens",
  "jira_token_setup": "edit $HOME/.agentic-ops/user/.env and set AGENTIC_OPS_JIRA_API_TOKEN=<api-token>",
  "jira_config_next_action": "set_jira_api_token",
  "next_action": "init_agent_capability"
}
```

### 失败处理

- Jira base URL 未提供且项目 profile 也没有默认值时，初始化继续完成，但输出 `jira_config_status: needs_configuration` 和补齐指引；后续 `list-tasks` 仍会在真实 Jira 配置缺失时阻断。
- 非终端环境使用 `--interactive` 时，返回 `interactive_terminal_required`，要求改用完整参数形式。
- GitHub 登录状态、SSH key 或仓库权限不可用导致源码下载失败时，返回 `source_checkout_failed`，提示修复 GitHub 权限或使用 `--source-root` 指向已有本地源码目录。
- 源码下载失败时，已输入的 Jira 本机配置和 token 保持有效；workspace overlay、`agent.json` 和 `AGENTS.md` 管理块只在源码准备完成后写入。
- 本地源码目录已存在且非空时，初始化复用该目录，不覆盖、不拉取、不切换分支。
- 已有完整本地 AgenticOps 受管配置时，停止并要求研发负责人确认；确认覆盖时使用 `--confirm-existing-config`。只留下部分受管文件时允许同项目初始化直接修复。
- 工作流配置不完整时，输出缺失字段。

### 验收标准

- 一个工作空间能绑定一个具体 Jira 空间和一组 GitHub 仓库。
- 不同工作空间可以使用不同 Jira / GitHub / 代码仓库配置。
- 初始化时研发负责人必须提供项目配置项和 Jira 用户，并确认项目 AI 工作空间目录；本地源码目录可通过 `--source-root` 显式指定，未指定时使用默认目录并下载项目代码。
- 共享安装资源中的 workflow profile 不包含研发负责人个人 Jira 用户或本机绝对路径；本地 overlay 由 `workspace init` 写入项目 AI 工作空间。
- 初始化后，项目 AI 工作空间中存在 `.agentic-ops/agent.json` 和 `AGENTS.md`。
- 初始化后，默认 `source_root` 存在并可作为项目源码目录使用。
- `agent init` 和 `preflight` 会检查工作空间受管文件与 `source_root`，半初始化状态不能通过任务接管前预检。
- 工作空间产物写入项目 AI 工作空间，不写入 `~/.agentic-ops`。
- Jira API token 不写入 YAML；初始化缺失 token 时只引导写入 `$AGENTIC_OPS_HOME/user/.env` 的 `AGENTIC_OPS_JIRA_API_TOKEN`。
- Jira 空间到代码仓库的映射由工作流配置维护，AIAgent 不得在接管真实卡片时猜测目标仓库。
- `agentic-cli preflight --workspace <name>` 能验证工作空间可用性。

### 保护行为

- `workspace init` 必须在项目 AI 工作空间目录内执行。
- 工作空间配置必须通过项目配置项绑定 Jira 用户、Jira 空间、仓库映射和本地源码根目录。
- 覆盖一组完整的 `.agentic-ops/agent.json`、`.agentic-ops/profile.local.yaml` 和 AgenticOps 管理的 `AGENTS.md` 配置块前，必须由研发负责人显式确认；不完整的受管文件组允许同项目初始化修复。
- 具体项目运行产物必须写入项目 AI 工作空间，不能写入 `~/.agentic-ops`。
- 目标仓库选择必须来自 workflow profile 或 Jira 字段映射，不能由 AIAgent 临场猜测。

### 审核问题

- 当前目录是否是项目 AI 工作空间，而不是 AgenticOps 源头仓库或 `~/.agentic-ops`。
- Jira 空间到代码仓库的映射是否完整。
- 本地源码目录是否可访问。
- 工作空间预检失败时是否能输出缺失配置。

### 验收证据

- `agentic-cli workspace init --project <project-name> --jira-user <user>` 输出。
- `agentic-cli profile resolve --project <project-name>` 输出。
- `agentic-cli preflight --workspace <name>` 输出。
- 项目 AI 工作空间中的 `.agentic-ops/runs/`、`.agentic-ops/run-logs/`、`.agentic-ops/feedback/`、`.agentic-ops/agent.json` 和 `AGENTS.md`。
- workflow profile 中的 Jira / GitHub / 本地路径映射。

### 关联设计

- `docs/architecture/project-structure.md`
- `docs/profiles/workflow-profile.md`
- `docs/project-rules.md`
- `docs/runtime/cli-runtime.md`
