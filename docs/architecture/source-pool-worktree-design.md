# 中央克隆池 + Git Worktree 源码管理设计

## 1. 目标与范围

本文定义 AgenticOps developer 工作面的「中央克隆池 + Git Worktree」源码管理方案，并落实目标部署模型：

- 安装目录（`~/.agentic-ops`）代表一名 AI 研发员：承载研发员级配置（源码池根、Git 身份、Jira 账户与凭证）、运行能力与机器级源码池。
- 业务项目工作空间只存项目相关信息：profile/connection 选择、任务状态、审计；不再保存固定源码目录归属与研发员身份凭证。
- 源码池根为必配项，安装时指定；池成员按 `<owner>/<repo>` 组织，缺了才克隆。
- 业务任务执行时，从池用 `git worktree add` 挂出任务级子工作树集，路径格式：

```text
<source_root>/.worktree/<jira_id>/<问题版本>/<repo>
```

解决的核心问题：tapdata 项目约 17 个业务仓库（本机已克隆 12 个、合计约 12.3G，`t-layer3-test` 单仓 9.3G），慢网络下重复全量克隆不可接受；项目运行需要多仓库分支组合对应；任务分析需要跨多仓库搜索；工作空间与源码目录耦合过深，任务隔离不清晰。

本文改变「业务源码获取、布局、任务工作树、研发员身份归属」四条边界；不改变 Jira 绑定事实源、授权门禁、证据回写、`~/.agentic-ops` sparse managed clone 边界和 AgenticOps 源头仓库规则。

实现分两个阶段（对应两个 AO 任务）：

- 阶段一（本期）：中央克隆池（池根必配）+ 任务级子工作树集 + 多仓库分支对应 + from_branch 路径规范化。
- 阶段二（已由 D-052 收口）：Jira 账户/凭证/Git 执行身份上移安装目录，工作空间瘦身为纯项目绑定；旧工作空间失败关闭，不提供自动凭证迁移。

两个阶段的研发员级配置统一位于 `~/.agentic-ops/user/`。

## 2. 实施前基线与根因

- 实施前每个工作空间有独立 `source_root`，默认推导为 `<workspace父目录>/<workspace>-code/<repo短名>`。
- 实施前 `_ensure_source_checkout` 在工作空间级目录克隆或复用单仓库；现役池模式已由 `workspace init` 按 Profile 仓库清单准备中央池成员。
- 实施前 `_check_source_root_conflict` 禁止两个工作空间共享同一源码目录；现役工作空间共享池对象，通过任务子工作树隔离写入。
- `_validate_repository_remotes` 要求 raw/effective fetch/push URL 全部精确等于 `repositories.default` 且数量唯一；`_reject_git_url_rewrites` 拒绝 `url.*.insteadOf/pushInsteadOf` 改写。
- 实施前 Profile 只支持 `repositories.default`；现役 Profile 已支持 `repositories.list`、`worktree_domains` 和 `branches`。
- 实施前基线曾把身份与凭证保存在 schema v3 工作空间；该基线已由 D-052 取代。现役身份与凭证只保存在 developer 安装，工作空间固定 schema v5。
- 根因：克隆按工作空间独立发生，大仓库全量下载重复执行；源码目录按工作空间而非按任务组织；多仓库分支组合与任务目标仓库没有确定性映射，靠单仓库 default 硬编码；研发员身份与项目绑定耦合在同一个工作空间文件里。

## 3. 方案

### 3.1 总体形态与部署模型

```text
~/.agentic-ops/                    ← 安装目录 = 一名 AI 研发员
  user/config.yaml                 ← 研发员级配置：source_pool_root（必配，阶段一）；身份/凭证（阶段二）
  ...                              ← developer-only sparse managed clone（不变）

<source_root>/                     ← 池根（安装时必配，如 ~/github）
  <owner>/<repo>/                  ← 池成员：完整克隆（普通克隆，保留主 checkout）
    .git/                          ← 唯一对象库（全项目、全任务共享）
    <默认分支 checkout>            ← 用户可手动使用的主工作树
  .locks/<owner>__<repo>.lock      ← 任务接管时的池成员级并发锁（复用 TaskLock）
  .worktree/<JIRA-KEY>/<问题版本>/<repo>/ ← 任务级子工作树（git worktree）
  README.md                        ← 池容器说明（受管标记）

<workspace>/                       ← 业务项目工作空间（只存项目信息）
  .agentic-ops/                    ← 任务状态、审计；身份/凭证在阶段二后不再入内
```

- 池是普通克隆而非 bare：保留主 checkout，用户可在 `~/github/<owner>/<repo>` 手动操作（现状习惯不变）；AI 任务工作树用 `git worktree add` 挂出。
- 每个池成员是独立 git 仓库；并发锁只作用于同一仓库内部。
- 池根与池成员路径必须通过 `validate_business_source_root` 同类校验：不得是 `~/.agentic-ops`、不得是 AgenticOps 源头仓库或其子目录（`workplane_mismatch`）。
- 工作空间索引（`_index_path`）与 `agent.json` 的 `source_root` 字段语义调整为「任务工作树根目录」（即池根），保证既有索引结构兼容。

### 3.2 池根配置（必配）

- `source_pool_root` 为必配研发员级配置，写入 `~/.agentic-ops/user/config.yaml`（安装/首次配置时由安装流程或 `ao-work` 配置命令写入；实现时确认该路径不在 sparse 受管覆盖范围内）。支持 `--source-pool-root <dir>` 显式覆盖（仅本次）。
- 未配置 `source_pool_root` → `workspace init` 直接阻断（失败码 `source_pool_root_invalid`），提示先配置池根。不做「未配置回退现状」的兼容路径（项目未真实推广，不需要兼容）。
- 池根必须存在且可写；不存在时由 init 创建并写入容器 README（复用 `_write_source_container_readme` 思路，归属改为「AI 研发员源码池」）。
- `--source-root` 显式指向已有独立完整克隆时仍可复用（`reused`），作为一次性/特殊场景功能保留，不是默认路径。

### 3.3 工作空间初始化流程（池模式）

`workspace init` 的源码步骤：

1. 解析池根（3.2）；准备池成员全集：对 `repositories.list` 逐仓库执行——池成员不存在 → 必须执行 `git clone --progress`（复用 `_run_git_streaming`，AO-11 流式、逐仓库进度），不得因预检权限诊断静默跳过；已存在 → 认领（adopt）：校验 remotes 精确匹配（复用 `_validate_repository_remotes`）、拒绝 URL 改写（复用 `_reject_git_url_rewrites`）、拒绝指向 AgenticOps 源头仓库。若已存在 Git 仓库的 remote 身份不符合目标仓库，且工作区没有未提交修改，则清除该 checkout 后重新 clone；存在未提交修改时失败关闭，要求人工先保存修改。
2. 认领时若池成员是浅克隆（现有 `~/github` 克隆是 depth 1）→ 自动流式 `git fetch --unshallow`；不允许以浅克隆作为池成员。
3. 全集准备支持中断续传：`Ctrl+C` 中断时已完成的池成员保留（不删除、不污染），下次 init/任务接管自动补齐缺失成员；不写任何初始化完成标记。
4. 不创建工作空间级源码目录或凭证；写 profile overlay、AGENTS.md、Skill 和 schema v5 agent.json（`source_root` 写池根）。
5. 任务工作树集在任务接管时创建（3.4），由任务上下文（Jira key、目标仓库、问题版本）与分支推导接口（3.9）推导。

### 3.4 任务级子工作树集（核心）

任务接管（公开入口 `ao-work takeover`，内部来源准备 `task start`）时：

1. 确定任务「问题版本」（`main`、`develop`、`release-v*` 或客户分支；兼容读取旧「修复分支」）。
2. 按 Jira 描述「目标仓库」首行和版本化 Profile 判定领域；目标仓库缺失时使用 `repositories.default`，列表外或未映射仓库失败关闭。现役 Runtime 尚不根据其它 Jira 文本或源码池线索推断候选领域，也不识别跨线索领域冲突。逐个领域仓库：
   - 按分支推导接口解析该仓库对应分支（3.9.2）。
   - 要求池成员已由 `workspace init` 准备；任务接管只刷新、验证和挂载，不在此阶段补 clone。
   - 先刷新并解析该领域全部仓库的远端提交；已有工作树也必须匹配同一批提交，任一仓库失败时不创建任何工作树。目标路径 `worktree_path = <pool_root>/.worktree/<jira_id>/<问题版本>/<repo>`（规范化见 3.5）。
   - 拿池成员锁 → `git worktree add --detach <worktree_path> <对应 ref>`；同一任务同分支同仓库已存在 worktree（resume/恢复）→ 校验路径与 ref 后复用，不重复创建。
   - 写入 per-worktree 身份（3.7）。
3. Runtime 为领域内仓库创建工作树，但当前来源上下文只把目标仓库工作树作为 `source_root`，并只输出目标仓库的 `problem_version`、`target_branch` 和路径。跨仓库分析及逐仓对齐证据输出属于后续能力。
4. 任务目标仓库（`target_repo`）解析：Jira 描述「目标仓库」section → 校验在 `repositories.list` 内；缺失回退 `default`。分析发现需要修改分析集内其它仓库时，由人工确认后固化扩展（跨仓库修改与 PR 集合跟踪见 3.9.4，本期为单主仓库 PR 流）。
5. 恢复时检测到旧布局 `<pool_root>/<jira_id>/<问题版本>/<repo>` → 失败关闭，不迁移、不删除、不创建新副本；由研发工程师先确认旧工作树中的修改并人工迁移或清理。

任务审计完成后，按清理策略 `git worktree remove` 任务根下各工作树（dirty 阻断，见 3.8）。

### 3.5 路径规范化与安全（已确认：`/` 替换为 `-`）

- `<jira_id>`：来自 Jira issue key，仅 `[0-9A-Za-z-]` 安全字符（复用 Jira key 校验）。
- `<问题版本>`：允许 git 合法分支名；含 `/` 时替换为 `-`（`feature/x` → `feature-x`），再通过 git 分支名校验 + 路径穿越防护（禁止 `.`/`..` 段、`@{}`、前导 `-`、空段、绝对路径）。替换保证目录层级固定为 5 层：`<source_root>/.worktree/<jira>/<问题版本>/<repo>`。
- `<repo>`：使用 `owner/repository` 的短名并校验安全字符。现役 Runtime 未实现跨 owner 的短名冲突专用校验；生产 TapData Profile 的领域成员使用同一 owner，暂不触发该冲突。
- 路径总长度限制（如 ≤ 240 字符），超限阻断并提示。
- worktree 路径互斥：不同任务/不同 from_branch 天然不同路径；同一任务同 from_branch 下不同仓库按 `<repo>` 区分；同一任务同仓库重复接管复用同一路径。

### 3.6 并发与更新

- 任务接管使用池成员级锁 `<pool_root>/.locks/<owner>__<repo>.lock`，复用 `TaskLock`，当前超时为 10 秒。`workspace init` 的池成员准备使用其初始化锁路径；任务接管在锁内执行 fetch、远端基线解析和 worktree add，避免同池成员并发冲突。
- fetch 统一在池成员执行（`git -C <pool_member> fetch origin`，一次惠及所有 worktree）；worktree 内不允许直接 fetch，Runtime 转池成员执行。主 checkout 用户手动 fetch 不强制拿锁（git 自身 ref 锁兜底，文档明示并发窗口）。
- gc：不强制 `gc.auto=0`，接受 git 自身互斥 + 池成员锁双重保护。

### 3.7 身份隔离（关键）

- AI 任务工作树身份由 per-worktree config 承载：`git config extensions.worktreeConfig true`，随后在 worktree 内 `git config --worktree user.name/email` 写入 `execution_identity` 的 Git 姓名与邮箱；池成员主 checkout 的用户身份不受影响。
- 双保险：Runtime 执行 git 写操作时注入 `GIT_AUTHOR_NAME/GIT_AUTHOR_EMAIL/GIT_COMMITTER_NAME/GIT_COMMITTER_EMAIL`（复用 `execution_identity` 四字段）；提交后校验 author/committer 等于当前研发员身份，不一致即阻断。
- 池成员 config 中的 `user.*` 视为用户个人配置（主 checkout 手动使用），AI 侧一律以 worktree config + 环境变量为准。
- 现役：`execution_identity` 只从 developer 安装读取（D-052），工作空间不保存身份或凭证。

### 3.8 生命周期

- 任务工作树：现役 Runtime 支持创建、精确复用和“本次新建失败后的尽力回滚”，尚未提供任务完成后的公开清理命令。回滚会调用 `git worktree remove --force`，但当前没有验证 remove 返回码或输出独立失败码；强回滚和生命周期清理由后续工作项补齐。
- 池成员：不随任务/工作空间删除而删除（共享资源）；仅当无任何任务工作树与工作空间引用且用户明确要求时清理。
- 工作空间删除：不触碰池成员；若残留任务工作树，先逐个按 3.8 清理。

### 3.9 多仓库（本期核心，tapdata 即多仓库）

#### 3.9.1 Profile 仓库集与挂载策略（渐进可调）

```yaml
repositories:
  default: tapdata/tapdata
  list:
    - tapdata/tapdata
    - tapdata/tapdata-enterprise
    - tapdata/tapdata-web
    - tapdata/tapdata-connectors
    - tapdata/tapdata-connectors-enterprise
    - tapdata/tapdata-license
    - tapdata/tapdata-common-lib
    - tapdata/tapdata-application
    - tapdata/feishu_robot
    - tapdata/tapdata-cloud
    - tapdata/t-layer3-test
    - tapdata/docs
    - tapdata/docs-en
    - tapdata/mcp-tap-server
    - tapdata/solutions
    - tapdata/fhir-solution
    # fork（Hazelcast/mongo）不默认纳入，任务明确要求时按需挂载
  worktree_domains:          # 任务工作树领域；目标仓库必须恰好属于一个领域
    - id: product
      baseline_repository: tapdata/tapdata
      repositories: [tapdata/tapdata, tapdata/tapdata-enterprise, tapdata/tapdata-web]
    - id: assistant
      baseline_repository: tapdata/feishu_robot
      repositories: [tapdata/feishu_robot]
    - id: automation-test
      baseline_repository: tapdata/t-layer3-test
      repositories: [tapdata/t-layer3-test]
    - id: connector
      baseline_repository: tapdata/tapdata-connectors
      repositories: [tapdata/tapdata-connectors, tapdata/tapdata-connectors-enterprise]
```

- `default`：任务目标仓库缺省值（兼容既有校验与索引）。
- `list`：profile 允许的全部业务仓库（17 个，含文档类 `docs`/`docs-en`；fork 如 `Hazelcast`/`mongo` 不默认纳入，任务明确要求时按需挂载）；池成员全集与 target 校验范围。
- `worktree_domains`：按目标仓库确定任务领域和挂载仓库集。每个仓库至多属于一个领域，领域基线仓必须属于该领域；TapData Profile 不允许以全量 `list` 回退。目标仓库未映射时以 `task_domain_unresolved` 阻断，并提示补齐仓库或领域，避免无关仓库被挂载。
- `analysis_mount`：仅为尚未声明领域的旧 Profile 保留的兼容策略；TapData 不使用该回退。
- 最小可用 Profile 可只声明 `default` 与 `list`，但生产 TapData Profile 必须声明 `worktree_domains`；`branches` 可随分支事实渐进补充。

#### 3.9.2 分支对应关系（领域基线 + 项目对齐工具）

分支对应关系做成「从主仓库分支推导其它仓库分支」的接口，而不是完整矩阵表：

```yaml
branches:
  derive_from: default       # 主仓库（default 或显式 owner/repo），以它的分支为推导基准
  default_rule: same_name    # 默认规则：其它仓库使用与主仓库同名的分支
  baseline_branches: {}      # 目标仓库的显式任务基线；声明后缺失即阻断
  dev_branches: {}           # 主仓开发基线场景的仓库分支映射
  overrides: []              # 例外规则表，渐进补充；命中才生效
    # - from_branch: release-2.0
    #   repo: tapdata/tapdata-web
    #   branch: release/2.0
    # - from_branch: release-2.0
    #   repo: tapdata/tapdata-connectors
    #   branch: v2.0.x
```

推导逻辑（确定性，标准资产，不靠 AI 猜测）：

1. 任务「问题版本」确定（任务描述「问题版本」，兼容读取旧「修复分支」；缺失时使用所属领域 `baseline_repository` 的 `baseline_branches`，未声明映射才使用 `branches.default_branch`）。
2. 先以目标仓库选择唯一 `worktree_domain`，其 `baseline_repository` 是本次分支推导的主仓库；未命中领域即阻断，不做全量挂载。
3. 以基线仓库 `tapdata/tapdata` 识别 TapData 产品域，不依赖可变的领域 id。先刷新领域内全部池成员，再运行 `tap_align_branches.py plan --no-fetch --remote-only --repositories ... --json`；只接受与当前领域仓库集合完全一致的计划，并以其中每行的 `target` 作为实际分支。remote-only 模式不得回退本地同名分支或本地 PluginKit 内容。若使用 `<tapdata>,<enterprise>,<web>` 三段式对齐规格，工作树路径与有效问题版本使用第一段，完整规格只传给计划脚本。
4. 其它领域对每个仓库 R 使用通用静态规则：
   - R 是领域基线仓库 → 工作树分支 = `<问题版本>`。
   - 命中 `overrides`（from_branch + repo 都匹配）→ 工作树分支 = 规则声明的 `branch`。
   - `<问题版本>` 等于领域基线仓声明的开发分支且 R 命中 `dev_branches` → 工作树分支 = 显式声明的开发分支。
   - 否则默认规则 `same_name` → 工作树分支 = `<问题版本>` 同名分支。
   - 刷新 `origin` 后必须精确解析上述推导的远端引用；分支不存在则阻断（`branch_derivation_failed`），输出仓库与推导分支，提示维护者补映射或确认分支。

- 接口语义：产品域以项目对齐工具处理 PluginKit、license 与 release 的非同名关系；其它领域默认同名并行开发，例外用 `overrides` 逐条补充。
- 显式配置非法（derive_from 不在 list、override 引用未知仓库）由 Profile 加载校验阻断，并归入现役配置错误；当前没有独立 `branch_derivation_invalid` 失败码。
- 未来可扩展其它 `default_rule`（如 `latest_origin`、按 tag 推导），本期实现 `same_name` + `overrides`。

#### 3.9.3 任务目标仓库

- `task_start` 在建立来源上下文前直接解析 Jira 描述「目标仓库」section；首行值必须在 `repositories.list` 内（`target_repository_unknown`），缺失回退 `repositories.default`。
- Profile 中 `target_repo` 仍声明为 `workspace_repo_mapping`；池模式来源快照使用 Runtime 已解析的目标工作树仓库覆盖该值。独立 checkout 必须与工作空间绑定仓库一致，否则返回 `task_source_repository_mismatch`。

#### 3.9.4 跨仓库修改与 PR 集合（后续增强，本期不实现）

- 任务分析集覆盖全部相关仓库，但本期任务流程仍以单主仓库（target_repo）为修改目标，PR 流保持单仓库语义。
- 跨仓库修改（同一任务在多个仓库出分支/PR）、PR 集合跟踪、任务状态与 Jira 回写扩展列为独立后续增强；本期任务工作树集与池机制已为它预留结构（任务根下天然多仓库并存）。

### 3.10 身份/凭证上移安装目录（阶段二，已确认要做）

目标：`~/.agentic-ops/user/` 承载研发员身份与凭证，工作空间只存项目绑定与任务状态。

- `~/.agentic-ops/user/identity.yaml`（或 config.yaml 内嵌）：`agent_id`（研发员标识，安装级唯一，不再每工作空间生成）、Git 执行身份（`execution_identity` 四字段）、Jira 账户（email）+ `~/.agentic-ops/user/.env`（token，权限 0600）。
- 工作空间 `agent.json` 现役 schema v5：不保存 `jira_account_id`、`execution_identity` 或凭证，保留 `project_profile`、`jira_project`、`connection_id`、`jira_site`、`source_root`（池根）、`repository`，并保存安装目录身份引用与本地入口绑定；schema v4 及更早版本在凭证读取和联网前失败关闭。
- init 流程：从安装目录读身份与凭证（不再交互收集 email/token/执行身份）；Jira 账户校验绑定安装目录身份 + 项目，`jira_workspace_mismatch` / drift 语义改为「工作空间与安装目录身份或项目不一致」。
- 凭证安全：`~/.agentic-ops/user/` 加入 sparse managed clone 排除清单（不随更新覆盖）、权限 0600、读写路径校验（复用 `validate_managed_path` 思路）；凭证不入工作空间、不入池。
- 多工作空间共享同一身份（同一研发员多项目），`agent_id` 冲突检查改为安装级唯一校验。
- 旧工作空间：schema v3 与 `.agentic-ops/.env` 在读取凭证和联网前失败关闭；人工先用 `ao-work auth` 重新配置安装授权，再明确重新初始化。Runtime 不自动复制或删除旧凭证（D-052）。
- D-046 修订：「业务项目工作空间保存该研发员唯一的 Jira 账户」改为「安装目录保存研发员唯一的 Jira 账户与凭证，业务项目工作空间只绑定项目」；同步修订 decision-log 与项目目标文档相关表述。

## 4. 安全边界（不弱化项）

- 任务工作树路径互斥：不同工作空间/任务不得使用同一 worktree 路径；AI 不得使用池成员主 checkout 路径作为任务工作树。
- 池成员 remote 精确匹配、URL 改写拒绝、`source_checkout_must_not_be_agenticops_source_or_descendant` 全部保留并在池与 worktree 两侧执行。
- 身份隔离按 D-052 执行：同一 developer 安装下工作空间继承同一身份与凭证，禁止跨安装或从工作空间/任务状态拼接身份凭证。
- 阶段二后凭证仅存 `~/.agentic-ops/user/`（0600、sparse 排除），工作空间与池均不得出现凭证；迁移失败不落半成品状态。
- `workspace init` 的 clone/unshallow 失败按初始化事务处理；任务接管的 worktree add 失败会尽力移除本次已创建工作树。现役任务回滚未验证 remove 结果，不能描述为不可失败的强回滚。
- 池根与池成员路径禁止为 `~/.agentic-ops`、AgenticOps 源头仓库或其子目录。

## 5. 现役失败码与后续缺口

现役 Runtime 已实现：

- `source_pool_root_invalid`：池根缺失（必配未配置）、非法（是 `~/.agentic-ops`、是源头仓库、不可写、配置格式错误）。
- `source_pool_unshallow_failed`：自动 unshallow 失败（已回滚）。
- `branch_derivation_failed`：分支推导结果在刷新后的仓库远端不存在（输出精确仓库与推导分支）。
- `worktree_add_failed`：任务工作树创建失败（已回滚）。
- `worktree_legacy_layout_detected`：检测到该 Jira key 的旧布局任务根；保留原目录并失败关闭，由研发工程师人工确认迁移或清理。
- `worktree_path_invalid`：任务工作树路径非法（jira_id/from_branch/repo 或长度超限、路径穿越）。
- `task_lock_timeout`：包括任务接管池成员锁在内的 `TaskLock` 等待超时。
- `target_repository_unknown`：任务目标仓库不在 profile `repositories.list` 内。
- 安装授权：`install_identity_missing`（安装目录缺少身份/凭证）、`install_identity_drift`（工作空间引用与安装目录身份不一致）、`workspace_jira_identity_upgrade_required`（旧 schema 失败关闭）。

以下设计码尚未由现役任务工作树 Runtime 实现，必须作为能力缺口处理，不能据本文构造命令或声称已受保护：

- `worktree_path_conflict`：路径被文件、符号链接、主 checkout 或其它任务占用时的统一中文阻断。
- `worktree_remove_failed`：回滚或生命周期清理失败并完成结果验证。
- `repository_short_name_collision`：跨 owner 的仓库短名冲突。
- `branch_derivation_invalid`：将 Profile 分支配置错误细分为独立失败码。

## 6. 组件变更

### 阶段一

- `developer/runtime/src/ao_work/workspace_init/service.py`：池根必配解析、池成员全集认领/克隆（含浅克隆 unshallow、中断续传）、init 不创建源码目录（`source_root` 语义改为池根）、池成员锁、容器 README。
- `developer/runtime/src/ao_work/task_start.py`（及任务接管/恢复路径）：任务工作树集创建/复用、per-worktree 身份写入、问题版本规范化、产品域 remote-only 分支对齐、其它领域基线规则解析、`target_repo` 多仓库解析与校验，并把有效 `problem_version` 与实际 `target_branch` 分开写入来源上下文。
- `developer/runtime/src/ao_work/workspace.py`：`validate_business_source_root` 增加池根/主 checkout 约束；工作树路径规范化与校验函数。
- `developer/runtime/src/ao_work/workspace_init/cli.py`：`--source-pool-root` 参数；交互确认摘要展示池根、池成员全集与任务工作树路径规则。
- `developer/runtime/src/ao_work/config/`：研发员级配置读取（`~/.agentic-ops/user/config.yaml` 的 `source_pool_root`）；ProjectProfile 增加 `repositories.list/worktree_domains`、`branches`（derive_from/default_rule/overrides）。
- `developer/standards/projects/tapdata/profile.yaml`：`repositories` 扩展 list（17 个业务仓库，含文档类，fork 按需）与 `worktree_domains`（产品、小助手、自动化测试、连接器）；`branches` 推导配置（同名默认，overrides 渐进补充）；`problem_version` 保留 Jira Description 输入声明，`target_branch` 由任务工作树对齐结果提供。
- `developer/standards/contracts/operations/workspace-init.yaml`：`source_pool_root` 必配输入、`source_root` 语义说明、postcondition 增加池成员/身份隔离断言、failure 增加新失败码。
- `developer/bootstrap/install.sh` / 配置命令：安装/首次配置时引导写入 `source_pool_root`（必配）。

### 阶段二

- `developer/runtime/src/ao_work/installation.py`：`~/.agentic-ops/user/` 加入 sparse 排除清单、身份/凭证读写与权限校验。
- `developer/runtime/src/ao_work/workspace_init/service.py` / `config/`：身份与凭证改从安装目录读取；现役 agent.json 已演进到 schema v5，并绑定工作空间本地入口。
- 不新增自动迁移命令：旧工作空间失败关闭，人工重新授权并确认初始化；旧凭证清理由研发工程师决策。
- `docs/decision-log.md`：修订 D-046；`docs/strategy/project-goals.md` 相关表述同步。

## 7. 测试与验证

- 当前固定验收覆盖：
  - 池根缺失 → `source_pool_root_invalid` 阻断（无回退兼容路径）。
  - 池模式 init：池内全集合 clone + 容器 README；中断续传（已完成成员保留，缺失补齐）；不创建工作空间源码目录。
  - 认领已有池成员：remotes 精确匹配通过、URL 改写拒绝、浅克隆自动 unshallow、指向源头仓库拒绝。
  - 任务工作树集：路径推导 `<pool_root>/.worktree/<jira>/<问题版本>/<repo>`、`feature/x` → `feature-x` 规范化、按领域挂载、未归类仓库阻断、非法分支/穿越路径阻断、同任务复用和 per-worktree 身份生效。
  - 分支推导：目标仓库显式基线、同名默认、overrides 与开发分支映射命中、远端分支缺失失败关闭（`branch_derivation_failed`）、显式配置非法由 Profile 加载校验阻断。
  - 多仓库：`target_repo` 描述解析、列表外仓库阻断和缺省回退 default。
  - 回滚：克隆失败删除新建池目录、unshallow 失败不落半成品、worktree add 失败时验证正常 remove 路径。
  - 并发：同池成员并发操作由池锁串行化（超时失败码）。
  - 阶段二：身份迁移（备份/回滚）、迁移后旧字段失效阻断、sparse 排除清单覆盖 user/、安装级 agent_id 唯一。
- `developer/tests/bootstrap/`：池根缺失/非法位置（`~/.agentic-ops`、源头仓库子目录）被阻断；安装流程可写入 `source_pool_root`。
- 固定完整验证保持不变（`test-python-runtime.sh`、`test-resources.sh`、`test_install_boundary.sh`、`test-release-workflow.sh`）。

尚缺固定验收：源码池线索判域与冲突候选、逐仓对齐证据输出、文件/符号链接型路径冲突、remove 失败后的强回滚，以及池成员枚举明确忽略 `.worktree/`。

## 8. 文档与故事

- `docs/runtime/python-runtime.md`、`docs/architecture/project-structure.md` 补充中央克隆池、任务工作树集布局与部署模型。
- `docs/development-engineers/getting-started.md`、`agent-init.md`、`de-002-workspace-init.md` 补充池必配、任务工作树、分支推导接口与多仓库说明；阶段二后补充身份上移说明。
- `docs/profiles/workflow-profile.md`：`repositories` 扩展 list/worktree_domains、`branches` 推导接口的配置规则同步。
- `docs/decision-log.md`：登记 D-048（中央克隆池 + 任务级工作树 + 多仓库分支推导）；阶段二修订 D-046。
- 新增/更新 developer 用户故事并注册故事，覆盖固定验收：池必配、池复用、身份隔离、任务工作树互斥与路径、分支推导映射、多仓库 target 解析、回滚、中断续传、阶段二身份迁移。

## 9. 决策点结论（评审已确认）

1. 池根配置：必配，写入 `~/.agentic-ops/user/config.yaml`；未配置直接阻断，不做兼容回退。✅（修订：取消兼容开关）
2. 池成员形态：普通克隆 + 保留主 checkout。✅
3. 身份隔离：per-worktree config + 运行时 env 双保险。✅
4. 浅克隆认领：自动流式 unshallow。✅
5. 任务工作树挂载：`--detach` 于分支对应 ref，任务内再建业务分支。✅
6. 身份/凭证上移安装目录：确定要做，实现按阶段二排期。✅
7. from_branch 含 `/`：替换为 `-`（`feature/x` → `feature-x`）。✅
8. 多仓库：profile `repositories.list/worktree_domains`（领域配置）+ 领域基线分支推导接口（同名默认 + overrides 渐进补充）+ 任务「目标仓库」与「问题版本」section 解析；目标仓库无法归类时阻断，跨仓库 PR 集合为后续增强。✅
9. 任务工作树路径：`<source_root>/.worktree/<jira_id>/<问题版本>/<repo>`；任务根 = `.worktree/<jira_id>/<问题版本>/`，任务根下按仓库挂工作树。✅
10. init 池成员准备：全集（list）逐仓库流式 clone，支持中断续传；任务接管仅挂载目标领域仓库。✅
11. 分支对应关系与领域配置均为版本化 Profile 资产；TapData 领域缺失时失败关闭，旧 Profile 仅保留兼容回退。✅（新增）
