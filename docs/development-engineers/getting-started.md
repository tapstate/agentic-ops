# 研发工程师上手

本文面向使用 AgenticOps 指导研发员处理业务 Jira 任务的研发工程师。这里使用 developer 工作面；不得进入 `maintainer`、加载 AgenticOps 源头规则或调用 `ao-maint`。

本文提供稳定 `main` 的生产安装主线。需要在源码目录中初始化、验证指定分支安装，或查看更短的初始化清单时，参阅 [初始化 AgenticOps 研发员](agent-init.md)。

## 1. 准备清单

安装前确认：

- 可访问 `tapstate/agentic-ops` 的 GitHub 账户和 `gh` 登录状态。
- 业务项目工作空间目录，例如 `~/agentic-ops-tapdata`。
- Jira 项目空间或 Project Profile，例如 `tapdata` / `TAP`。
- 该研发员唯一的 Jira 邮箱和 API token。
- 默认源码仓库或明确的本地源码目录。

不得从其它工作空间、本机全局 `.env`、个人记忆或旧聊天中自动补齐凭证和项目事实。

首次 Bootstrap 只能使用调用者当前账户下载脚本和 clone。安装授权时必须明确选择复用机器全局 Git/SSH/`gh`，或在安装目录创建隔离授权；公司网络环境继续使用 SSH，安装级模式通过 `ssh.github.com:443` 提升连通性，不改用 HTTPS。

## 2. 安装 developer 工作面

提供两种安装方式；默认把稳定 `main` 的 developer-only managed clone 安装到 `~/.agentic-ops`，
需要隔离时可显式指定其它目录。

### 方式一：极简参数（推荐）

适合在终端交互完成安装和首次授权：

```sh
gh auth login -h github.com -p ssh -s repo

(
  set -e
  bootstrap="$(gh api -H 'Accept: application/vnd.github.raw' \
    '/repos/tapstate/agentic-ops/contents/developer/bootstrap/install.sh?ref=main')"
  printf '%s\n' "$bootstrap" | bash
)
```

不传授权参数时，有终端会直接进入 `ao-work auth` 引导；无终端只完成安装并输出授权待办。

### 指定安装目录

需要为不同研发员或测试环境隔离安装时，使用 `--install-home`。该参数优先于旧的
`AGENTIC_OPS_HOME` 环境变量；后者仍兼容，供已有自动化脚本使用。自定义目录不会修改 shell
profile，当前会话需自行加入 PATH：

```sh
INS_HOME="$HOME/.agentic-ops-custom"

(
  set -e
  bootstrap="$(gh api -H 'Accept: application/vnd.github.raw' \
    '/repos/tapstate/agentic-ops/contents/developer/bootstrap/install.sh?ref=main')"
  printf '%s\n' "$bootstrap" | bash -s -- \
    --install-home "$INS_HOME"
)

export PATH="$INS_HOME/bin:$PATH"
```

之后调用该自定义安装中的 `update.sh` 或 `rollback.sh` 时，也要显式传入同一
`AGENTIC_OPS_HOME="$INS_HOME"`，避免这些独立 Bootstrap 回退到默认目录。

### 方式二：全参数（非交互）

适合脚本或 CI。先把远程 Bootstrap 下载到临时文件，再通过该脚本的标准输入传入 token；不能直接把脚本文本和 token 同时通过一个管道传给 `bash`：

```sh
gh auth login -h github.com -p ssh -s repo
INS_HOME="$HOME/.agentic-ops-custom"

(
  set -eu
  bootstrap_file="$(mktemp)"
  trap 'rm -f "$bootstrap_file"' EXIT
  gh api -H 'Accept: application/vnd.github.raw' \
    '/repos/tapstate/agentic-ops/contents/developer/bootstrap/install.sh?ref=main' \
    > "$bootstrap_file"
  test -s "$bootstrap_file"
  printf '%s\n' "$JIRA_API_TOKEN" | bash "$bootstrap_file" \
    --install-home "$INS_HOME" \
    --agent-id <agent-id> \
    --jira-email <jira-account-email> \
    --git-name <git-author-and-committer-name> \
    --git-email <git-author-and-committer-email> \
    --github-login <github-actor-login> \
    --execution-auth-mode global \
    --token-stdin \
    --non-interactive
)
```

两种方式都会校验 origin 必须是 `tapstate/agentic-ops`，普通使用不能用 `AGENTIC_OPS_REPO_URL` 等环境变量改写受信仓库；正常文件树只包含 developer 生产资产、只读的 `shared/integration/` JSON 协议及运行所需的根版本元数据，不包含 `maintainer/`、`developer/tests/`、fixture 或 fake producer。

安装完成后可使用：

```sh
source "$HOME/.zshrc"
ao-work --help
ao-work version
```

`ao-work version` 是只读安装查询：返回 developer Runtime 发行版本、受管安装目录、首次安装时间、精确 Git HEAD/短 SHA，以及存在时的 Tag 描述。它不读取凭证、不要求业务工作空间，也不会检查更新、修改安装或变更 Git 状态；安装时间元数据缺失或无效时会失败关闭，不能通过文件时间戳猜测。

没有 `agentic-cli` 兼容别名；看到旧命令说明正在阅读冻结迁移基线或使用旧版本。

安装后也可以随时交互配置或轮换：

```sh
<install-root>/bin/ao-work auth
<install-root>/bin/ao-work auth --show
```

若安装已完成，自动化或无交互场景也可单独使用完整授权参数；以下 `global` 示例只复用并验证机器已有 Git/SSH/`gh`，不会改写它们。Jira token 只通过标准输入传递：

```sh
printf '%s\n' "$JIRA_API_TOKEN" | ao-work auth \
  --agent-id <agent-id> \
  --jira-email <jira-account-email> \
  --git-name <git-author-and-committer-name> \
  --git-email <git-author-and-committer-email> \
  --github-login <github-actor-login> \
  --execution-auth-mode global \
  --token-stdin \
  --non-interactive
```

需要隔离授权时，在终端运行 `<install-root>/bin/ao-work auth --execution-auth-mode installation`，按提示完成官方设备登录和安装专属 SSH 公钥登记。首次安装级 GitHub 登录不支持非交互方式，也不会复用 Jira token 或全局 `gh`。安装级 SSH 只使用当前安装私钥、禁用全局 Agent，并通过 SSH-over-443 访问 GitHub。

交互配置使用目标安装的 `<install-root>/bin/ao-work auth`，并以 `<install-root>/bin/ao-work auth --show` 回读脱敏身份、模式、路径状态和公钥指纹。已有授权不同时先审查 `change_digest`，再用 `--confirm-replace-authorization <change_digest>` 精确确认；不同安装 `gh` 账户、既有私钥、自定义 `core.sshCommand` 或非受管文件仍失败关闭。安装脚本只负责调用 Runtime。新工作空间从当前安装继承身份与凭证并生成 schema v5 绑定与本地入口。

## 3. 初始化业务项目工作空间

提供两种初始化方式；两者都从当前 developer 安装继承研发员身份和凭证。

### 方式一：极简参数（推荐）

适合在终端交互完成首次初始化。只需进入独立的业务项目 AI 工作空间，Runtime 会从安装配置和 Project Profile 补齐确定性信息，仅在信息缺失或冲突时提问：

```sh
mkdir -p ~/agentic-ops-tapdata
cd ~/agentic-ops-tapdata
<install-root>/bin/ao-work workspace init
```

### 方式二：全参数（非交互）

适合脚本、CI 或希望明确记录工作空间、项目和源码池位置的场景。`--workspace-root` 是 `ao-work` 顶层参数，必须放在 `workspace` 之前：

```sh
<install-root>/bin/ao-work \
  --workspace-root ~/agentic-ops-tapdata \
  workspace init \
  --non-interactive \
  --project tapdata \
  --source-pool-root ~/agentic-ops-source-pool
```

若要覆盖已有、完整且不同的工作空间配置，额外传入 `--confirm-existing-config`；非交互模式未传该参数会失败关闭。`workspace init` 不接受 `agent_id`、Jira email/token 或 Git/GitHub 身份参数，这些信息只能由安装级 `ao-work auth` 提供。

首次初始化会核对：

- `agent_id`：从当前安装读取，只能包含 `[0-9a-zA-Z_-]`。
- Jira 项目空间 / Project Profile。
- Jira 站点、从安装身份继承的研发员账户和授权状态。
- 默认仓库和源码目录。
- Git、GitHub、Jira 访问等前置检查。
- Git author/committer 与 GitHub actor login；从安装身份继承，不读取全局 Git/GitHub 身份作为事实。
- Git 远端 SSH 与 `gh` API 分别按授权模式执行；`gh` 回读不能单独证明 SSH push actor。

只有缺失或冲突的项才需要额外参数；Connection 默认由 Project Profile 推导，不要求普通用户传 `--connection-id`。源码池来自 `--source-pool-root` 或安装目录 `user/config.yaml` 的 `source_pool_root`；不存在时由 Runtime 创建并写入容器 README。

这不是每个 Jira 任务都要重复填写的清单。配置来源固定为：

| 来源 | 自动提供的内容 | 需要人工动作 |
| --- | --- | --- |
| developer 安装 | 研发员、Jira 账户、Git/GitHub 执行身份 | 首次配置或凭证轮换 |
| 业务工作空间 | Project Profile、安装身份引用、源码仓库 | 首次配置或明确重绑 |
| Project Profile | Jira 站点、Project、状态/字段映射、默认仓库和固定策略 | 只有项目配置变化时审查 |
| Jira 卡片 | Issue ID、经办人、状态、标题、描述和已配置业务字段 | 卡片缺失或冲突时决策 |

完整 task-to-PR manifest 是后台机器审计合同，不是用户配置表。普通任务只需给出 Jira key；AI 汇总待审查计划后，研发工程师只确认计划、范围、验证与高风险操作。

初始化成功后会写入 schema v5 `.agentic-ops/agent.json`、当前工作空间 `AGENTS.md` 和 `.agents/skills/`。工作空间只保存项目事实、安装身份引用和本地 `ao-work` 入口，不保存 Jira token，也不生成 `.agentic-ops/.env`。`AGENTS.md` 和 `.agents/skills/` 是 AI 可直接发现的受管副本；`workspace preflight` 会检查缺失、漂移、额外资产和 maintainer 污染。

业务项目 AI 工作空间与源码仓库必须使用两个独立目录，不能相同，也不能互相嵌套。不要手工创建指向安装根的 symlink，业务仓库也不需要创建不存在的 `developer/...` 相对路径。

不要在下列位置初始化业务工作空间：

- `~/.agentic-ops`。
- `tapstate/agentic-ops` 源头仓库或其 worktree。
- 另一个研发员的业务项目工作空间。
- 业务源码仓库本身或其任意子目录。

## 4. 授权与验证

```sh
<install-root>/bin/ao-work auth --show
./.agentic-ops/bin/ao-work workspace preflight
./.agentic-ops/bin/ao-work capability list
```

授权属于 developer 安装，不属于单个工作空间。`ao-work auth` 在终端进入引导；token 不通过命令行参数传递。同一安装下的业务工作空间继承同一身份和凭证，不同研发员必须使用隔离安装。授权模式、执行身份或安装 SSH 公钥指纹变化后必须明确重绑工作空间。项目验证子进程不会继承 SSH 私钥、Agent、`GH_CONFIG_DIR` 或 GitHub 凭证。只有授权已配置且 preflight 通过后，才能操作真实 Jira 任务。

调用具体操作前运行 `./.agentic-ops/bin/ao-work capability show <operation>`；只有 `status=implemented` 且列出明确命令路径时才能调用。`capability_gap` 表示当前版本没有安全原子操作，应按中文 `next_action` 转人工，不能尝试旧命令。

## 5. 启动 AIAgent

在已初始化的业务项目工作空间中启动 Codex 或其它受支持 AIAgent。AI 应从当前目录的 `AGENTS.md` 自动进入 developer 工作面，不需要读取 AgenticOps 根 `AGENTS.md`。

初始化后可以发送：

```text
列出我名下可以接管的 Jira 任务。
接管 TAP-123；信息不足时先结合代码形成补卡建议并写回 Jira，接管后先把修复计划写入 Jira，等我确认。
确认该设计，并授权在当前 Jira 工作项、仓库、任务分支、目标分支和验证范围内连续推进到拉取请求审查；范围或风险变化时停下。
回写本次执行证据。
提交 TAP-123 本次执行的任务审计记录。
```

这些自然语言需求不代表对应自动化都已实现。AI 必须先查询能力目录；任务释放、部分 PR / CI 协作、分支对齐和完成审计等仍可能返回 `capability_gap`，应由研发工程师按目录指引处理。正式接管必须使用统一 takeover，不能用内部 `task init` 或 `task start` 冒充；developer 不提供 Agentic Custom Field 写入。

## 6. 问题反馈与快速改进

任务处理中出现无法自动完成、人工干预过多或输出质量不足时：

1. 先由研发工程师校对，确保当前业务任务正确完成。
2. 让 AI 形成脱敏的问题总结、期望行为、建议沉淀位置和回归方法。
3. 人工确认改进方案。
4. 在独立 AgenticOps worktree 中切换到 maintainer 工作面完成改进并创建 `develop` PR。

业务工作空间不得直接修改 `~/.agentic-ops` 或调用 `ao-maint`。工作面切换必须通过独立目录和独立 AI 入口完成。

## 7. 更新与回滚

更新使用安装目录中的独立 developer Bootstrap；不要通过重复执行安装命令静默替代更新确认：

```sh
~/.agentic-ops/developer/bootstrap/update.sh
```

需要回滚时显式执行：

```sh
~/.agentic-ops/developer/bootstrap/rollback.sh
```

更新和回滚只改变 developer-only managed clone 与锁定 Python 环境，不修改各业务项目工作空间的 Jira 身份和任务状态。`installation` 模式授权完成后，managed clone 的后续更新使用安装专属 SSH；回滚不联网。更新目标与当前 ref 必须先展示并由研发工程师确认；非交互模式不得静默接受。

## 8. 常见问题

### 找不到 `ao-work`

```sh
source "$HOME/.zshrc"
~/.agentic-ops/bin/ao-work --help
```

### GitHub 权限不足

```sh
gh auth status
gh auth login -h github.com -p ssh -s repo
```

### 工作面不匹配

如果输出 `workplane_mismatch`，检查当前目录是否为已初始化的业务项目工作空间、AI 是否读取了本目录 `AGENTS.md`、调用入口是否为 `ao-work`。不要用 mode 参数或复制维护规则规避阻断。
