# DE-001 安装 AgenticOps developer 工作面

作为研发工程师，
我希望能通过一条安装命令获得 developer 工作面，
以便在业务项目工作空间使用 `ao-work`、业务 Skill、Rule 和标准资产，而不会加载项目维护能力。

> 本故事是发版验收条件。developer 安装边界、入口或更新回滚失败时不得发布。

### 触发方式

```sh
gh api -H 'Accept: application/vnd.github.raw' \
  '/repos/tapstate/agentic-ops/contents/developer/bootstrap/install.sh?ref=main' \
  | bash
```

### 前置条件

- 当前系统为 Linux 或 macOS。
- `gh auth status` 已通过且账户可访问私有仓库。
- 本机具备 shell、Git、GitHub CLI 和可用 clone 凭证。

### 主流程

1. 管道安装脚本通过已登录的 `gh` 读取 Bootstrap 公共库，再验证 GitHub 登录与仓库访问。
2. 首次安装时在 `~/.agentic-ops` 创建 managed clone，并启用 sparse checkout。
3. sparse checkout 只检出 `developer/`、只读 `shared/` JSON 协议及运行所需根版本元数据。
4. Bootstrap 安装或定位 `uv`，按 `.python-version` 与 `developer/uv.lock` 准备 developer 独立 `.venv`。
5. Bootstrap 生成 `~/.agentic-ops/bin/ao-work`。
6. Bootstrap 验证 `ao-work --help`，写入 `.local/current-ref` 和安装审计。
7. 默认安装幂等写入 `~/.agentic-ops/bin` 的 PATH；自定义安装目录不修改真实用户 shell profile，只输出当前会话指引。
8. 更新必须调用独立 `developer/bootstrap/update.sh`，先展示当前 ref 与目标 ref 并得到确认；失败时通过 `rollback.sh` 回滚到 `previous-ref`。重复执行 `install.sh` 只修复当前 checkout，不代替更新确认。

### 验收标准

- `~/.agentic-ops/bin/ao-work --help` 可执行。
- 安装不要求 Go，也不构建 AgenticOps 自有平台二进制。
- `~/.agentic-ops` 正常文件树不含 `maintainer/` 运行资产。
- 不生成 `agentic-cli` 兼容别名。
- developer Runtime 不能导入 `ao_maint`，`ao-work` 不暴露故事门禁或发布子命令。
- 安装不绑定具体 Jira 空间或研发员身份；凭证只在业务项目工作空间保存。
- 更新需要人工确认，回滚不破坏业务项目工作空间状态。
- 日志不包含 secrets、tokens、private keys 或原始敏感响应。
- `gh api ... install.sh | bash` 的真实管道入口可执行；首次默认安装与重复安装不会重复写 PATH，自定义安装不污染 shell profile。

### 保护行为

- `~/.agentic-ops` 只是 developer 能力安装，不是具体项目运行目录。
- 安装过程不得检出或复制 maintainer Skill、Rule、授权、配置、状态、脚本或 Runtime。
- Shell Bootstrap 不承载 Jira、任务状态、证据或门禁业务逻辑。

### 验收证据

- 安装命令与输出。
- sparse checkout 配置和 developer-only 文件树检查。
- `~/.agentic-ops/bin/ao-work --help` 输出。
- 工作面边界合同测试。
- 更新确认与回滚测试。

### 关联设计

- `docs/architecture/project-structure.md`
- `docs/runtime/python-runtime.md`
- `developer/bootstrap/install.sh`
