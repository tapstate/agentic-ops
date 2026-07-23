# DL-001 安装 AgenticOps

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

### 保护行为

- 安装是全局动作，不绑定具体 Jira 空间、代码仓库或项目 AI 工作空间。
- 安装目录固定为 `~/.agentic-ops`。
- 安装不得覆盖用户已有本地配置。
- 安装日志和安装目录不得保存 secrets、tokens、private keys 或原始敏感日志。

### 审核问题

- 安装完成后研发负责人能否明确下一步是初始化项目 AI 工作空间。
- `~/.agentic-ops` 是否只包含全局安装、配置模板和通用运行资产。
- 安装失败时是否能说明缺失依赖、网络问题或权限问题。
- 安装产物是否能证明来自可验证版本。

### 验收证据

- `agentic-cli --version` 输出。
- `agentic-cli preflight` 输出。
- 安装输出中的 `operation=install`、`install_dir` 和 `next_action`。
- `bash tests/e2e/local-release-install-flow.sh`

### 关联设计

- `docs/architecture/project-structure.md`
- `docs/runtime/cli-runtime.md`
- `docs/runtime/versioning.md`
- `scripts/init.sh`
