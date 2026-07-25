# DL-001 安装 AgenticOps

作为研发负责人，
我希望能通过一条安装命令安装 AgenticOps，
以便在本机获得 `agentic-cli`、AI 员工手册、全局配置模板、操作契约和通用技能。

### 触发方式

```sh
curl -fsSL https://raw.githubusercontent.com/tapstate/agentic-ops/main/scripts/install.sh | bash
```

### 前置条件

- 当前系统为 Linux、macOS Intel 或 macOS Apple Silicon。
- 本机可访问 `tapstate/agentic-ops`。
- 本机具备基础 shell 和 `git`，用于执行安装引导和 managed clone 更新。

### 主流程

1. 安装脚本识别 OS 和 CPU 架构。
2. 安装脚本检查 `git` 等 bootstrap 依赖。
3. 首次安装时 clone `tapstate/agentic-ops` 到 `~/.agentic-ops`。
4. 更新时暂存 tracked 本地改动，记录 `.local/previous-ref`，再更新到 `origin/main` 最新版本。
5. 安装脚本校验 `install-resources/checksums.txt`。
6. 安装脚本把 `install-resources/<os-arch>/agentic-cli` 复制到 `~/.agentic-ops/bin/agentic-cli`。
7. 安装脚本写入 `.local/current-ref` 和 `.local/install-log.json`。
8. 安装脚本输出下一步命令。

### 输出

```json
{
  "ok": true,
  "operation": "install",
  "install_dir": "~/.agentic-ops",
  "bin": "~/.agentic-ops/bin/agentic-cli",
  "source": "managed_clone",
  "next_action": "workspace_init"
}
```

### 失败处理

- 如果缺少依赖，输出缺少的工具和安装建议。
- 如果无法访问仓库，输出网络或权限原因。
- 如果 `~/.agentic-ops` 已存在，支持安全更新；更新前暂存 tracked 本地改动，失败时回退到 `.local/previous-ref`。
- 不允许把 secrets 写入安装日志。

### 验收标准

- Linux、macOS Intel 和 macOS Apple Silicon 都能执行安装命令。
- 安装后 `agentic-cli --version` 可用。
- 安装后 `agentic-cli preflight` 可用。
- 安装目录是 `~/.agentic-ops`。
- `~/.agentic-ops` 是 AgenticOps managed clone，不作为具体项目运行目录。
- `bin/agentic-cli` 和 `.local/*` 是本地产生文件，并被 `.gitignore` 忽略。

### 保护行为

- 安装是全局动作，不绑定具体 Jira 空间、代码仓库或项目 AI 工作空间。
- 安装目录固定为 `~/.agentic-ops`。
- 安装只能使用 `install-resources/<os-arch>/agentic-cli` 中已经编译并提交的二进制，不在研发负责人机器上编译。
- 安装不得覆盖用户已有本地配置。
- 安装日志和安装目录不得保存 secrets、tokens、private keys 或原始敏感日志。

### 审核问题

- 安装完成后研发负责人能否明确下一步是初始化项目 AI 工作空间。
- `~/.agentic-ops` 是否保持与 GitHub 仓库一致的 managed clone 结构。
- 安装失败时是否能说明缺失依赖、网络问题或权限问题。
- 安装产物是否能证明来自可验证版本。

### 验收证据

- `agentic-cli --version` 输出。
- `agentic-cli preflight` 输出。
- 安装输出中的 `operation=install`、`install_dir` 和 `next_action`。
- `bash tests/e2e/local-install-flow.sh`

### 关联设计

- `docs/architecture/project-structure.md`
- `docs/runtime/cli-runtime.md`
- `docs/runtime/versioning.md`
- `scripts/install.sh`
