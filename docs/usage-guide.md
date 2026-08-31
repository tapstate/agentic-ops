# AgenticOps 使用指引

不熟悉本文术语时，先查看[术语表](glossary.md)。

## 1. 安装

本节用于业务使用者安装已发布的 AgenticOps，不用于维护产品源码。两种安装方式均从
受信的 `main` 分支安装到 `~/.agentic-ops`，且都需要 Git、Bash 和 Python 3.9+；安装
目录已存在时会拒绝覆盖，请改用 `agenticops update`。Git SSH 的配置、验证和撤销见
[Git SSH 授权指引](security/git-ssh-access.md)。

### 1.1 GitHub CLI 一键安装

此方式使用 `gh` 从私有仓库读取安装入口，适合已登录 GitHub CLI 的用户。除通用依赖外，
还需要 GitHub CLI（`gh`）登录到有本仓库读取权限的 GitHub 账号。

先确认 `gh` 登录状态：

```sh
gh auth status -h github.com
```

未登录时完成网页登录；不要把 token 写入命令行或仓库：

```sh
gh auth login --hostname github.com --git-protocol ssh --skip-ssh-key --scopes repo
```

`gh auth status` 成功时无需重复登录。随后使用当前账号从私有仓库读取受信 `main` 分支的
安装入口：

```sh
(
  set -euo pipefail
  bootstrap="$(gh api -H 'Accept: application/vnd.github.raw' \
    '/repos/tapstate/agentic-ops/contents/bootstrap/install.sh?ref=main')"
  printf '%s\n' "$bootstrap" | bash
)
```

默认 Source Pool 为 `~/.agentic-ops-repos`（即 `${product_root}-repos`）。如需复用已有
外部池，可把最后一行改为：

```sh
printf '%s\n' "$bootstrap" | bash -s -- --repository-pool <Source-Pool-目录>
```

默认 `manual` 模式不会自动下载业务仓库；明确需要自动供给时再增加
`--repository-provisioning auto-clone`。两种模式都会先校验池根目录不能位于 Product Root
内，且路径必须是可读写目录。

`gh api` 被拒绝时，核对当前账号的仓库访问权与 `repo` scope。

### 1.2 Git clone 安装

此方式不依赖 `gh`；适合已配置 Git SSH、且 GitHub 账号具有 `tapstate/agentic-ops`
读取权限的用户。先按 Git SSH 授权指引验证身份与仓库读取权限，再直接将受信分支稀疏
克隆为正式产品根目录：

```sh
(
  set -euo pipefail
  ao_install_root="$HOME/.agentic-ops"
  # 复用已有外部池时改为该池的绝对路径。
  ao_repository_pool="$HOME/.agentic-ops-repos"
  # 默认不自动下载业务仓库；仅在项目映射和下载范围已确认后改为 auto-clone。
  ao_repository_provisioning="manual"
  test ! -e "$ao_install_root" || {
    printf '安装目录已存在：%s；请使用 agenticops update 更新\n' "$ao_install_root" >&2
    exit 2
  }

  git clone --filter=blob:none --no-checkout --branch main --single-branch \
    git@github.com:tapstate/agentic-ops.git \
    "$ao_install_root"

  git -C "$ao_install_root" sparse-checkout init --cone
  git -C "$ao_install_root" sparse-checkout set \
    adapters bootstrap contracts gate policies projects workflow
  git -C "$ao_install_root" checkout main

  ao_current_ref="$(git -C "$ao_install_root" rev-parse HEAD)"
  python3 "$ao_install_root/bootstrap/product_state.py" \
    --product-root "$ao_install_root" write \
    --mode installed \
    --repository git@github.com:tapstate/agentic-ops.git \
    --branch main \
    --current-ref "$ao_current_ref"
  python3 "$ao_install_root/bootstrap/repository_pool.py" \
    --product-root "$ao_install_root" configure \
    --root "$ao_repository_pool" --provisioning "$ao_repository_provisioning"
)
```

这条路径不执行 `install.sh`：Git 直接写入正式安装目录，再显式写入“使用工作面”的
产品状态，后续 `update` 与 `rollback` 才能按安装分支正常工作。`git clone` 被拒绝时，
按 Git SSH 授权指引检查密钥、组织 SSO 与仓库授权。

两种方式安装出的目录结构和产品状态相同；下载、认证或克隆失败时不会继续安装。不要把
产品源码仓库当作业务安装目录。

## 2. 选择并配置 Source Pool

Source Pool 是业务仓库的统一主工作树根目录：每个仓库位于
`<Source-Pool>/<owner>/<repo>`，任务实际修改的 linked worktree 则位于各自工作空间的
`.agenticops/worktrees/`。它不是 Product Root 的子目录，也不会被 Agent 整体加入可写范围。

安装时未指定池会创建默认池；仅在需要复用已有、干净的业务仓库主工作树或需要为不同环境
隔离仓库缓存时，才显式指定该参数。GitHub CLI 安装在第 1.1 节的 `bash -s --` 后传入
`--repository-pool`；Git clone 安装在第 1.2 节修改 `ao_repository_pool`。池目录必须可读、
可写、可进入，且不能与 Product Root 或项目工作空间互相嵌套。

默认使用 `manual` 供给模式：先由用户按 `<owner>/<repo>` 布局下载并校验业务仓库，任务
准备只会使用已接入的仓库。`auto-clone` 会在任务准备时自动 clone 项目仓库；只有项目仓库
映射、Git SSH 权限和自动下载范围均已确认时才使用：

在 GitHub CLI 安装方式中附加 `--repository-provisioning auto-clone`；Git clone 安装方式中把
`ao_repository_provisioning` 改为 `auto-clone`。除这两处外，不要在工作空间或任务执行时临时
改变供给模式。

工作空间首次初始化时默认继承 Product Root 的池；如该工作空间必须使用独立池，在首次初始化
显式传入 `--repository-pool`。实际路径与来源会固化在 `.agenticops/workspace.json`；之后改变
Product Root 默认池不会静默重绑已有工作空间，也不得手改该文件。迁移前必须先清理任务
worktree，再重新初始化或使用后续受控迁移能力。

## 3. 初始化项目工作空间

先查看产品根目录（Product Root）当前提供的 Agent：

```sh
~/.agentic-ops/agenticops agents
```

接入全部 Agent：

```sh
~/.agentic-ops/agenticops init --workspace <项目工作空间> --project tapdata
```

只接入部分 Agent 时重复传入：

```sh
~/.agentic-ops/agenticops init --workspace <项目工作空间> --project tapdata --agent <Agent-ID-1> --agent <Agent-ID-2>
```

工作空间默认继承 Product Root 的 Source Pool。需要独立池时只在首次初始化显式覆盖：

```sh
~/.agentic-ops/agenticops init --workspace <项目工作空间> --project tapdata \
  --repository-pool <独立-Source-Pool-目录>
```

没有 `both` 特殊值，也不限制 Agent 数量。一个工作空间绑定一个产品项目，可接管该
项目下任意多个任务；一个任务可修改多个仓库。

初始化后，工作空间会生成可再生的薄入口 `.agenticops/agenticops`。它每次读取同目录
的 `workspace.json` 以定位并校验中央 Product Root，再在当前工作空间执行中央入口；
不需要 `.env`，也不保存第二份路径配置：

```sh
cd <项目工作空间>
./.agenticops/agenticops doctor
./.agenticops/agenticops start --agent <Agent ID>
```

若薄入口缺失、不可执行或 `doctor` 提示接线漂移，使用已绑定的中央入口执行
`repair --workspace <项目工作空间>`；不得手改 `.agenticops/workspace.json` 或用 `.env`
覆盖绑定关系。

产品根目录会在成功初始化、修复或启动后，维护一个仅供本机提示的已知工作空间索引。更新到
新提交时只提示待刷新项，不会扫描或自动修改业务目录。可通过以下命令维护该索引和工作空间：

```sh
~/.agentic-ops/agenticops workspace list
~/.agentic-ops/agenticops workspace repair --all
~/.agentic-ops/agenticops workspace prune --all
~/.agentic-ops/agenticops workspace detach --workspace <项目工作空间>
~/.agentic-ops/agenticops workspace purge --workspace <项目工作空间>
```

`--all` 必须显式指定；批量操作会先列出目标并要求确认。`prune` 仅注销缺失、已改绑或
无效的本机提示登记。`clean --generated-only` 只收敛可再生接线；`detach` 保留
`.agenticops/tasks/`，因此存在已准备 worktree 时会要求先清理，避免解绑后留下孤儿；
`purge` 会联动清理洁净 worktree 后删除任务状态，且不支持批量执行。非交互环境默认拒绝
这些确认操作；仅在已由调用方展示并确认目标列表的自动化场景中使用 `--yes`。

## 4. 工作空间数据

```text
.agenticops/
├── init.json                 # 初始化版本与生成产物哈希
├── workspace.json            # 产品根目录、workspace ID、项目、Agent、Source Pool 绑定
└── tasks/
    ├── index.json            # 任务注册与激活状态
    └── <JIRA-KEY>/           # 该任务的状态、授权、事件和 CI
```

`init.json` 和 Agent 配置可重新生成；`workspace.json` 是工作空间配置；`tasks/` 是
业务运行数据。Policy、Project Skill 和 Runtime 不复制到工作空间。

## 5. 检查、更新与回退

```sh
~/.agentic-ops/agenticops doctor --workspace <项目工作空间>
~/.agentic-ops/agenticops repair --workspace <项目工作空间>
~/.agentic-ops/agenticops update
~/.agentic-ops/agenticops rollback
```

`doctor` 只读检查；`repair` 安全重建接线并迁移旧工作空间状态，不改任务语义。
以上命令运行在安装产品根目录，即使用工作面。`update` 只 fast-forward 到安装时
记录的分支；`rollback` 回到最近一次更新前的提交。使用工作面有本地修改、HEAD
偏离安装记录或远端历史异常时会停止，不会覆盖现场。

## 6. 启动 Agent、查看并接管任务

普通只读接管/梳理从薄工作空间启动：

```sh
~/.agentic-ops/agenticops start --agent <Agent ID> --workspace <项目工作空间>
```

`start` 会先刷新接线，并把 `--` 后参数原样交给 Agent。在对话中先请求只读任务清单：

```text
列出当前工作空间已登记或可恢复的任务；再只读查询 Jira 中我可以接管的任务。不要执行写操作。
```

本地注册表只保存已接管任务；Jira 仍是待接管任务的事实源。若 Agent 没有可用 Jira 原生
连接，应明确报告，而不是编造任务清单。

```sh
python3 ~/.agentic-ops/workflow/task.py list --dir <项目工作空间>
python3 ~/.agentic-ops/workflow/task.py status --issue-key TAP-123 --dir <项目工作空间>
```

接管时向 Agent 发送：

```text
接管 TAP-123。先读取 Jira 事实、项目准入规则和相关代码；列全缺失项、目标仓库、工作分支、验证方式和风险。未经我的方案确认，不要进入实现或执行外部写操作。
```

Agent 提交方案后，按实际信息补全并发送：

```text
确认 TAP-123 的方案。授权仓库：<owner/repo>；工作分支：<branch>；基线：<branch>；变更范围：<范围>；验证：<命令或方法>。仅在此范围内实现、测试、提交、推送和创建/更新 PR；范围、风险或验证变化时停止并重新确认。
```

Agent 应回显任务阶段、实际变更仓库、验证结果、提交、PR 和 CI；证据回写 Jira 前必须展示
内容供确认。

任务类型为 `defect_fix`、`feature_change`、`technical_task`。多个任务可同时 `active`；
存在歧义时必须显式绑定 issue key。Agent 必须按项目准入要求登记每个仓库的工作分支、
基线、范围和验证方式；研发工程师确认方案并签发任务授权后，才能进入实现。

进入设计评审后，先登记仓库并准备任务 worktree。Project Package 的
`projects/<project>/repositories.json` 是仓库目录；池内仓库必须位于
`<pool>/<owner>/<repo>`，用户自行下载的仓库也必须通过 origin、Git 根目录、
基线分支和洁净度校验：

```sh
python3 ~/.agentic-ops/workflow/task.py repository add \
  --issue-key TAP-123 --repo tapdata/tapdata \
  --work-branch feature/TAP-123 --scope '<变更范围>' \
  --verification '<验证命令>' --dir <项目工作空间>

python3 ~/.agentic-ops/workflow/task.py repository prepare \
  --issue-key TAP-123 --dir <项目工作空间>
```

`prepare` 会要求主工作树在基线分支且洁净，执行 `fetch --prune` 和 fast-forward，
再在 `<workspace>/.agenticops/worktrees/<issue-key>/<run-id>/<owner>/<repo>` 创建 linked
worktree，并把
`base_sha` 与仓库目录摘要写入任务状态。缺少池配置、仓库未接入、origin 不符、fetch
失败、主工作树脏或分叉都会停止；不会移动、覆盖或删除用户仓库。

worktree 准备完成并重新签发包含 `base_sha` 的授权后，用任务模式启动 Agent：

```sh
~/.agentic-ops/agenticops start --agent codex \
  --workspace <项目工作空间> --issue-key TAP-123
```

启动入口仍来自薄工作空间，但任务模式以 TAP-123 当前 run 目录为 cwd；校验该 run 的每个
worktree 后作为 `--add-dir` 传给 Agent。没有动态目录能力、worktree 已清理、路径/分支漂移
或仓库目录变化时失败关闭。

任务完成时阶段推进会清理洁净 worktree；也可显式执行：

```sh
python3 ~/.agentic-ops/workflow/task.py repository cleanup \
  --issue-key TAP-123 --dir <项目工作空间>
```

默认保留本地任务分支，便于恢复和审计。只有明确希望回收且分支已安全合并时增加
`--delete-branches`；底层只执行 `git branch -d`，不会强删。任务 reset 生成新 `run_id`；
如果保留了同名分支，默认准备会阻断，需改用新分支，或在确认旧分支正是要继续的现场后
显式传入 `--reuse-existing-branch`。`workspace purge` 会先执行同样的洁净度检查和
worktree 清理，脏 worktree 会阻断整个 purge。

任务已被接管时，再次执行 `task.py init` 不会自动激活、覆盖或生成新 run，而是停止并展示
当前阶段与 `run_id`：继续时显式执行 `task.py activate`；重做时先执行 `repository cleanup`，
再显式执行 `reset --expected-run-id <当前-run-id>`。同一任务的主 Agent、subagent 和
恢复会话共享任务状态中的同一个 `run_id`，不得按会话自行生成；过期或并发 reset 会停止，
不会覆盖后来创建的 run。

Jira、Git、GitHub PR/CI 仍是事实源。合并、发布、Tag、保护分支写入、强推和历史改写
不被普通任务授权覆盖；事实、权限或外部写入结果不明确时必须停止，不能手改
`.agenticops/` 或换工具绕过门禁。

平台接线细节见 [Claude 验证](testing/e2e-claude.md)和
[Codex 验证](testing/e2e-codex.md)；权限边界见[安全说明](security/permissions.md)。
