# 研发工程师上手

本文面向使用 AgenticOps 指导研发员处理业务 Jira 任务的研发工程师。这里使用 developer 工作面；不得进入 `maintainer`、加载 AgenticOps 源头规则或调用 `ao-maint`。

本文提供稳定 `main` 的生产安装主线。需要在源码目录中初始化、验证指定分支安装，或查看更短的初始化清单时，参阅 [初始化 AgenticOps 研发员](agent-init.md)。

## 1. 准备清单

安装前确认：

- 本机已安装 `git`、`gh`、`uv`；若使用隔离的可信 `uv`，可通过 `AGENTIC_OPS_UV` 指定。
- 可访问 `tapstate/agentic-ops` 的 GitHub 账户和 `gh` 登录状态。
- 业务项目工作空间目录，例如 `~/agentic-ops-tapdata`。
- Jira 项目空间或 Project Profile，例如 `tapdata` / `TAP`。
- 该研发员唯一的 Jira 邮箱和 API token。
- 默认源码仓库或明确的本地源码目录。

不得从其它工作空间、本机全局 `.env`、个人记忆或旧聊天中自动补齐凭证和项目事实。

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
    --token-stdin \
    --non-interactive
)
```

两种方式都会在安装写入前统一检查 `git`、`gh`、`uv`；缺少多项时一次列出全部缺失程序并停止。随后校验 origin 必须是 `tapstate/agentic-ops`，普通使用不能用 `AGENTIC_OPS_REPO_URL` 等环境变量改写受信仓库；正常文件树只包含 developer 生产资产、只读的 `shared/integration/` JSON 协议及运行所需的根版本元数据，不包含 `maintainer/`、`developer/tests/`、fixture 或 fake producer。

安装完成后可使用：

```sh
source "$HOME/.zshrc"
ao-work --help
ao-work version
```

`ao-work version` 是只读安装查询：`version` 使用 `<current_branch>-<tag>-<commit_count>-g<last_commit_hash>` 格式，例如 `main-v0.6-25-g1b23a60`。它同时返回 developer Runtime 发行版本、受管安装目录、首次安装时间、实际 `python_executable`、`python_venv`、精确 Git HEAD/短 SHA 与拆分后的 Git 描述字段。它不读取凭证、不要求业务工作空间，也不会检查更新、修改安装或变更 Git 状态；安装时间元数据缺失或无效、当前 HEAD 无法由 Tag 描述时会失败关闭，不能通过文件时间戳或猜测版本补齐。

没有 `agentic-cli` 兼容别名；看到旧命令说明正在阅读冻结迁移基线或使用旧版本。

安装后也可以随时交互配置或轮换：

```sh
<install-root>/bin/ao-work auth
<install-root>/bin/ao-work auth --show
```

若安装已完成，自动化或无交互场景也可单独使用完整授权参数；token 只通过标准输入传递：

```sh
printf '%s\n' "$JIRA_API_TOKEN" | ao-work auth \
  --agent-id <agent-id> \
  --jira-email <jira-account-email> \
  --git-name <git-author-and-committer-name> \
  --git-email <git-author-and-committer-email> \
  --github-login <github-actor-login> \
  --token-stdin \
  --non-interactive
```

交互配置使用目标安装的 `<install-root>/bin/ao-work auth`，并以 `<install-root>/bin/ao-work auth --show` 回读脱敏身份。安装脚本也可以接收相同授权参数，但只负责调用 Runtime。新工作空间从当前安装继承身份与凭证并生成 schema v5 绑定与本地入口。

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
./.agentic-ops/bin/ao-work capability list
```

授权属于 developer 安装，不属于单个工作空间。`ao-work auth` 在终端进入引导；token 不通过命令行参数传递。同一安装下的业务工作空间继承同一身份和凭证，不同研发员必须使用隔离安装。`workspace preflight` 仅用于诊断或修复工作空间，不是接管任务的前置步骤；接管时 Runtime 会重新校验工作空间、安装身份和 Jira 事实。

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

接管完成后，Runtime 不会先按默认仓库创建工作树。标准顺序是：`task repositories assess` 形成只读建议；研发工程师审查并修正完整关系表；`task repositories confirm --confirm` 固化唯一工作依据；实际需要改某仓库时调用 `task worktrees prepare --repository <owner/repo>`。任务完成 evidence 评论必须列出 `actual_change_repositories`；评论与 Jira 完成态回读后，使用 `task worktrees cleanup` 安全清理子工作树。源码池主工作树只作分析源，不由任务流程修改或清理。

## 6. AO问题反馈与快速改进

当发现的是 **AgenticOps 本身** 的问题（例如命令无法安全完成、需要过多人工干预或输出质量不足），统一使用“AO问题反馈”。它不同于业务项目的 Bug：不会在当前业务 Jira 项目建卡，而是在 AO 项目创建 `Agentic 缺陷`。

先校对并确保当前业务任务得到正确处置，然后在已初始化的业务项目工作空间中向 AI 发送：

```text
AO问题反馈：<现象、复现步骤或脱敏报错，以及期望行为>
```

AI 会整理中文摘要与描述，至少包含现象、复现步骤或证据、影响和期望行为。研发工程师核对并明确确认建卡内容后，AI 才会使用受控的 `ao-work jira create plan -> apply -> readback` 流程，在 Jira AO 项目创建 `Agentic 缺陷`；创建结果会回显真实 issue key。

不要直接调用 Jira REST API，也不要在描述中包含 token、密钥、客户数据或未经脱敏的日志。授权、字段或建卡结果不明确时，应停止在 Runtime 给出的处理动作上，不要重复 `apply`。

建卡后的改进仍需人工确认方案，并在独立 AgenticOps worktree 中进入 maintainer 工作面完成改进并创建 `develop` PR。业务工作空间不得直接修改 `~/.agentic-ops` 或调用 `ao-maint`；工作面切换必须通过独立目录和独立 AI 入口完成。

## 7. 更新与回滚

更新使用安装目录中的独立 developer Bootstrap；不要通过重复执行安装命令静默替代更新确认：

```sh
~/.agentic-ops/developer/bootstrap/update.sh
```

需要回滚时显式执行：

```sh
~/.agentic-ops/developer/bootstrap/rollback.sh
```

更新和回滚只改变 developer-only managed clone 与锁定 Python 环境，不修改各业务项目工作空间的 Jira 身份和任务状态。更新目标与当前 ref 必须先展示并由研发工程师确认；非交互模式不得静默接受。

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
