# DE-001 安装 AgenticOps

作为研发工程师，
我希望能通过一条安装命令安装 AgenticOps，
以便在本机获得 `agentic-cli`、AI 员工手册、全局配置模板、操作契约和通用技能。

> 本故事是 AgenticOps 发版验收条件。任何面向研发工程师发布的 latest 版本，都必须先证明本故事通过；如果安装入口、权限、仓库可见性、平台二进制或初始化提示导致本故事失败，不得视为可发布版本。

### 触发方式

```sh
gh api -H 'Accept: application/vnd.github.raw' \
  '/repos/tapstate/agentic-ops/contents/scripts/install.sh?ref=main' \
  | AGENTIC_OPS_REPO_URL='git@github.com:tapstate/agentic-ops.git' bash
```

### 前置条件

- 当前系统为 Linux、macOS Intel 或 macOS Apple Silicon。
- 本机可访问 `tapstate/agentic-ops`。
- 本机具备基础 shell、`git` 和 `gh`，用于执行认证安装引导和 managed clone 更新。
- `gh auth status` 已通过，并且当前 GitHub 账号具备访问 `tapstate/agentic-ops` 私有仓库的权限。
- 当前机器已具备 clone `git@github.com:tapstate/agentic-ops.git` 的 SSH 权限，或研发工程师显式提供可用的 `AGENTIC_OPS_REPO_URL`。

### 主流程

1. 研发工程师用 `gh auth status` 确认 GitHub CLI 登录状态。
2. 研发工程师通过 `gh api` 获取 `scripts/install.sh` 的 raw 内容，并通过 `AGENTIC_OPS_REPO_URL` 显式指定 SSH clone 地址后交给 `bash` 执行。
3. 安装脚本识别 OS 和 CPU 架构。
4. 安装脚本检查 `git` 等 bootstrap 依赖。
5. 首次安装时 clone `tapstate/agentic-ops` 到 `~/.agentic-ops`。
6. 如果 `~/.agentic-ops` 已存在，安装脚本进入更新模式，展示当前 ref 和目标分支，并要求研发工程师确认。
7. 研发工程师确认更新后，安装脚本暂存 tracked 本地改动，记录 `.local/previous-ref`，再更新到 `origin/main` 最新版本。
8. 安装脚本校验 `install-resources/checksums.txt`。
9. 安装脚本把 `install-resources/<os-arch>/agentic-cli` 复制到 `~/.agentic-ops/bin/agentic-cli`。
10. 安装脚本写入 `.local/current-ref` 和 `.local/install-log.json`。
11. 安装脚本输出下一步命令。

### 输出

```json
{
  "ok": true,
  "operation": "install",
  "install_dir": "~/.agentic-ops",
  "bin": "~/.agentic-ops/bin/agentic-cli",
  "source": "managed_clone",
  "path_configured": false,
  "path_entry": "~/.agentic-ops/bin",
  "path_profile": "~/.zshrc",
  "path_profile_configured": true,
  "path_profile_updated": true,
  "agentic_next_action": "workspace_init"
}
```

### 失败处理

- 如果缺少 `git` 或 `gh`，输出缺少的工具和安装建议。
- 如果 `gh auth status` 失败，提示执行 `gh auth login -h github.com -p ssh -s repo`。
- 如果 `gh api` 无法读取安装脚本，输出 GitHub 登录、私有仓库权限或网络原因。
- 如果 SSH clone 失败，提示检查 SSH key、仓库权限，或由研发工程师显式设置 `AGENTIC_OPS_REPO_URL`。
- 如果 `~/.agentic-ops` 已存在但研发工程师未确认更新，安装脚本停止并输出 `update cancelled`。
- 如果 Codex 或 CI 等非交互环境需要更新，必须先获得研发工程师明确确认，再设置 `AGENTIC_OPS_ASSUME_YES=1`。
- 如果 `~/.agentic-ops` 已存在，支持安全更新；更新前暂存 tracked 本地改动，失败时回退到 `.local/previous-ref`。
- 不允许把 secrets 写入安装日志。

### 验收标准

- 本故事必须作为发版验收门禁通过，不能只作为普通回归项。
- Linux、macOS Intel 和 macOS Apple Silicon 都能执行 `gh api ... | AGENTIC_OPS_REPO_URL=... bash` 安装命令。
- 私有仓库安装入口不依赖匿名 raw URL。
- zsh 环境下 GitHub API contents 路径带引号，`?ref=main` 不会触发 shell glob 错误。
- 安装后 `agentic-cli --version` 可用。
- 如果 `~/.agentic-ops/bin` 不在 `PATH` 中，安装输出必须明确提示 `agentic-cli` 的完整路径和当前 shell 的幂等 `PATH` 修复命令。
- 安装脚本必须幂等写入 shell 启动文件；重复安装或更新不得重复追加同一条 PATH 配置。
- 安装输出必须说明管道执行的脚本不能修改父 shell 的当前 `PATH`，当前终端需要临时 PATH 命令、`source` 启动文件或重新打开终端。
- 安装后 `agentic-cli preflight` 可用。
- 安装目录是 `~/.agentic-ops`。
- `~/.agentic-ops` 是 AgenticOps managed clone，不作为具体项目运行目录。
- 已安装 `~/.agentic-ops` 时，更新必须先经过研发工程师确认；未确认不得自动更新。
- 非交互更新必须显式设置 `AGENTIC_OPS_ASSUME_YES=1`，且只能在研发工程师已确认后使用。
- `bin/agentic-cli` 和 `.local/*` 是本地产生文件，并被 `.gitignore` 忽略。

### 保护行为

- 安装是全局动作，不绑定具体 Jira 空间、代码仓库或项目 AI 工作空间。
- 安装目录固定为 `~/.agentic-ops`。
- 安装只能使用 `install-resources/<os-arch>/agentic-cli` 中已经编译并提交的二进制，不在研发工程师机器上编译。
- 安装不得覆盖用户已有本地配置。
- 更新不得静默执行；必须交互确认或通过明确的 `AGENTIC_OPS_ASSUME_YES=1` 表达已确认。
- 安装日志和安装目录不得保存 secrets、tokens、private keys 或原始敏感日志。

### 审核问题

- 安装完成后研发工程师能否明确下一步是初始化项目 AI 工作空间。
- `~/.agentic-ops` 是否保持与 GitHub 仓库一致的 managed clone 结构。
- 安装失败时是否能说明缺失依赖、网络问题或权限问题。
- 安装产物是否能证明来自可验证版本。

### 验收证据

- 发版验收记录必须引用本故事编号 `DE-001`。
- `gh auth status` 输出。
- `gh api ... | AGENTIC_OPS_REPO_URL=... bash` 安装输出。
- 已安装场景下的确认更新输出或 `update cancelled` 输出。
- `agentic-cli --version` 输出。
- `agentic-cli preflight` 输出。
- 安装输出中的 `operation=install`、`install_dir` 和 `agentic_next_action`。
- 安装输出中的 `path_configured`、`path_entry`、`path_profile`、`path_profile_configured` 和 `path_profile_updated`。
- `bash tests/e2e/local-install-flow.sh`

### 关联设计

- `docs/architecture/project-structure.md`
- `docs/runtime/cli-runtime.md`
- `docs/runtime/versioning.md`
- `scripts/install.sh`
