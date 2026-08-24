# DE-001 安装 AgenticOps developer 工作面

作为研发工程师，
我希望能通过一条安装命令获得 developer 工作面，
以便在业务项目工作空间使用 `ao-work`、业务 Skill、Rule 和标准资产，而不会加载项目维护能力。

> 本故事是发版验收条件。developer 安装边界、入口或更新回滚失败时不得发布。

### 触发方式

```sh
(
  set -e
  bootstrap="$(gh api -H 'Accept: application/vnd.github.raw' \
    '/repos/tapstate/agentic-ops/contents/developer/bootstrap/install.sh?ref=main')"
  printf '%s\n' "$bootstrap" | bash
)
```

远程 `develop` 验证安装（无需预先 checkout 源码）：

```sh
(
  set -e
  bootstrap="$(gh api -H 'Accept: application/vnd.github.raw' \
    '/repos/tapstate/agentic-ops/contents/developer/bootstrap/install-verify-branch.sh?ref=develop')"
  printf '%s\n' "$bootstrap" | bash -s -- --source-branch develop --json
)
```

验收补充：

- 验证入口与生产入口严格分离：远程 API 启动必须先确认脚本下载成功，再交给 `bash`，并按 `--source-branch` 从同一分支取得 Bootstrap 公共库；默认从官方远端按指定分支克隆，写入 `verification-only` 标记，产物是可运行的验证安装，可用其 `ao-work workspace init` 初始化研发员做端到端验证。
- 验证模式禁止写入 `~/.agentic-ops`；`--keep` 仅用于保留排障目录。
- 提供 `--source-worktree` 时降级为本地流程验证：只校验安装流程，origin 是本地路径，不可运行。
- 验证入口显式支持 `--source-branch`、`--source-worktree`、`--install-home`、`--log`、`--json`、`--keep`。

### 前置条件

- 当前系统为 Linux 或 macOS。
- 本机已安装 `git`、`gh`、`uv`；`AGENTIC_OPS_UV` 可指向可信的 `uv` 可执行文件。
- `gh auth status` 已通过且账户可访问私有仓库。
- 本机具备 shell、Git、GitHub CLI 和可用 clone 凭证。

### 主流程

1. 调用侧先通过已登录的 `gh` 完整下载脚本并确认成功，再交给 `bash`；404、未授权或路径错误响应不得进入 Shell。安装脚本在任何安装写入前统一检查 `git`、`gh`、`uv`，一次列出全部缺失项并以 `install_dependencies_missing` 停止。
2. 首次安装时在 `~/.agentic-ops` 创建 managed clone，并启用 sparse checkout；`--install-home <path>` 可显式指定隔离安装目录，且优先于兼容用的 `AGENTIC_OPS_HOME`。
3. sparse checkout 只检出 `developer/`、只读 `shared/` JSON 协议及运行所需根版本元数据。
4. Bootstrap 定位 `uv`，按 `.python-version` 与 `developer/uv.lock` 准备 developer 独立 `.venv`，并验证 `.venv/bin/python` 的运行前缀确实属于该安装 venv。
5. Bootstrap 生成 `~/.agentic-ops/bin/ao-work`；启动器固定设置该安装的 `VIRTUAL_ENV`，把 venv `bin` 放到 `PATH` 首位，并只用 venv Python 启动 Runtime。
6. Bootstrap 验证 `ao-work --help`，写入 `.local/current-ref` 和安装审计。
7. 默认安装幂等写入 `~/.agentic-ops/bin` 的 PATH；自定义安装目录不修改真实用户 shell profile，只输出当前会话指引。
8. 安装参数包含完整研发员授权时，Bootstrap 只把参数和标准输入转交 `ao-work auth`；未传授权参数时，有终端进入 Runtime 授权引导，无终端完成安装并输出 `authorization_status=pending` 与下一步。
9. 更新必须调用独立 `developer/bootstrap/update.sh`，先展示当前 ref 与目标 ref 并得到确认；失败时通过 `rollback.sh` 回滚到 `previous-ref`。重复执行 `install.sh` 只修复当前 checkout，不代替更新确认。

### 验收标准

- `~/.agentic-ops/bin/ao-work --help` 可执行。
- 缺少 `git`、`gh`、`uv` 中任意一项时，安装在创建或修改安装目录前一次返回全部缺失项并停止。
- 安装输出包含 `python_venv`；`ao-work version` 返回的 `python_executable` 和 `python_venv` 必须属于当前 developer 安装的 `developer/.venv`。
- 安装不要求 Go，也不构建 AgenticOps 自有平台二进制。
- `~/.agentic-ops` 正常文件树不含 `maintainer/` 运行资产。
- 不生成 `agentic-cli` 兼容别名。
- developer Runtime 不能导入 `ao_maint`，`ao-work` 不暴露故事门禁或发布子命令。
- 安装不绑定具体 Jira Project，但可以可选配置当前安装唯一研发员身份与 Jira 凭证；授权业务逻辑只在 Python Runtime。
- 未传授权参数时，有终端进入授权引导；无终端安装成功并明确提示 `ao-work auth`。
- 安装级身份和凭证写入 `user/identity.yaml` 与 `user/.env`（0600），不写入业务工作空间。
- 更新需要人工确认，回滚不破坏业务项目工作空间状态。
- 日志不包含 secrets、tokens、private keys 或原始敏感响应。
- `gh api ... install.sh | bash` 的真实管道入口可执行；生产入口显式支持 `--install-home <path>`，首次默认安装与重复安装不会重复写 PATH，自定义安装不污染 shell profile。

### 保护行为

- `~/.agentic-ops` 只是 developer 能力安装，不是具体项目运行目录。
- 安装过程不得检出或复制 maintainer Skill、Rule、授权、配置、状态、脚本或 Runtime。
- Shell Bootstrap 不承载身份、凭证、Jira、任务状态、证据或门禁业务逻辑；只允许调用 Runtime 完成授权。

### 验收证据

- 安装命令与输出。
- 安装依赖完整与多项缺失时的统一失败输出。
- sparse checkout 配置和 developer-only 文件树检查。
- `~/.agentic-ops/bin/ao-work --help` 与 `ao-work version` 的 Python venv 输出。
- 可选安装授权、交互引导和无终端 pending 输出。
- 工作面边界合同测试。
- 更新确认与回滚测试。

### 关联设计

- `docs/architecture/project-structure.md`
- `docs/runtime/python-runtime.md`
- `developer/bootstrap/install.sh`
