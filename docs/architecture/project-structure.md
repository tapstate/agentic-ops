# AgenticOps 项目结构

> **状态：** 现役结构。旧 Go CLI、平台二进制、`install-resources/`、根 `scripts/` 和根 `tests/` 已删除；冻结历史文档只作为迁移证据，不是现役实现、验证或发布依据。

## 1. 结构原则

AgenticOps 先按工作面隔离，再在工作面内按资产类型划分。只有两个工作面：

- `maintainer`：维护 `tapstate/agentic-ops` 源头项目。
- `developer`：研发员在业务项目工作空间执行 Jira 任务。

两个工作面使用不同目录、AI 入口、命令、Python 包、Skill、Rule、授权、配置、状态和测试。不得用 `--mode`、环境变量或聊天指令把同一入口切换成另一工作面。

## 2. 四个位置

| 位置 | 工作面 | 可以保存 | 不得保存 |
| --- | --- | --- | --- |
| AgenticOps 源头仓库 / worktree | `maintainer` | 两个工作面的版本化源文件、设计、维护状态和发布能力 | 用户 token、真实业务任务状态 |
| `~/.agentic-ops` | `developer` | developer-only sparse managed clone、锁定 Python 环境、安装状态、当前研发员身份与凭证 | `maintainer/` 资产、业务任务状态、其它研发员身份 |
| 业务项目 AI 工作空间 | `developer` | 项目配置、安装身份引用、任务状态、证据和反馈 | AgenticOps 维护规则、身份凭证、其它项目任务状态、业务源码 |
| 业务源码目录（`<工作空间>-code/`，与工作空间同级） | `developer` | 业务项目源代码 | AgenticOps 受管状态、凭证、工作空间身份 |

## 3. 仓库结构

```text
agentic-ops/
  AGENTS.md                     # 根 AI 入口，固定进入 maintainer
  .agentic-ops-source           # 内容固定为 maintainer 的源头身份标记
  README.md
  .githooks/

  maintainer/
    AGENTS.md                   # 维护工作面 AI 入口
    bin/ao-maint                # 维护命令
    runtime/src/ao_maint/       # 维护 Python 包
    skills/                     # 维护 Skill，metadata.workplane: maintainer
    rules/                      # 源头维护规则
    standards/stories/          # 项目故事质量合同
    standards/experiments/      # AO 专用试验资产
    scripts/                    # 发布、Hotfix 和固定验证
    tests/
    pyproject.toml
    uv.lock

  developer/
    AGENTS.md                   # 业务工作面 AI 入口模板
    bootstrap/                  # 安装、更新、回滚和 ao-work 包装
    runtime/src/ao_work/        # 业务 Python 包
    skills/                     # 业务 Skill，metadata.workplane: developer
    rules/                      # AI 执行业务任务规则
    standards/                  # 公司、契约、连接、项目和运行手册
    tests/
    pyproject.toml
    uv.lock

  shared/
    README.md                   # 共享准入边界；默认无共享代码
    integration/
      README.md                 # developer 分发中的只读协议说明
      task-to-pr-*.schema.json  # 三个固定 task-to-pr JSON Schema

  docs/
```

## 4. AI 入口隔离

### 4.1 根仓库与维护 worktree

AI 在 AgenticOps 源头仓库或其 worktree 启动时：

1. 读取根 `AGENTS.md`。
2. 固定加载 `maintainer/AGENTS.md` 和当前维护任务需要的 maintainer 资产。
3. 通过 `./maintainer/bin/ao-maint` 调用维护能力。
4. 不加载 `developer/AGENTS.md`、业务项目凭证、业务任务状态或业务项目分支规则。

`.agentic-ops-source`、Git remote、仓库根和固定入口共同用于验证工作面，不由 AI 猜测；developer-only sparse 安装不检出该标记。

### 4.2 业务项目工作空间

`ao-work workspace init` 在业务项目工作空间生成 AI 入口。AI 在该目录启动时：

1. 读取该工作空间的 `AGENTS.md` 和 `.agentic-ops/agent.json`。
2. 从当前工作空间 `.agents/skills/` 发现初始化复制的 developer Skill；规则正文由 `AGENTS.md` 直接承载，标准资产由 `ao-work` 从受信安装根解析。
3. 通过 `ao-work` 调用业务任务能力。
4. 不加载根 `AGENTS.md`、`maintainer/`、项目目标、源头分支策略或发布脚本。

`.agents/skills/` 是 Codex 标准仓库级发现位置，保存受管普通文件副本，不使用指向安装根的 symlink，也不要求业务仓库存在 `developer/` 相对路径。`workspace preflight` 必须确认 Skill 集合、内容摘要和 developer 工作面归属与当前安装一致；缺失、漂移、额外 Skill 或 maintainer 污染都必须阻断并要求重新初始化。

### 4.3 工作空间与源码目录拓扑

业务项目 AI 工作空间是项目与任务容器（`.agentic-ops/`、`AGENTS.md` 管理块、`.agents/skills/`）；研发员身份与凭证属于 developer 安装。业务源码存放在中央源码池和任务级 worktree 中；`workspace init` 写入受管说明与项目映射，权威身份通过 schema v4 `install_identity_ref` 关联当前安装。

四个实体在目录树上互不嵌套：AgenticOps 源头仓库、developer 安装目录、业务项目工作空间、业务源码池/任务 worktree。身份与凭证严格限定在安装 `user/`，项目和任务状态限定在工作空间 `.agentic-ops/`；源码目录只放业务 Git 仓库，不混入 AgenticOps 受管状态。

### 4.4 停止条件

出现下列任一情况必须停止：

- `ao-maint` 在业务项目工作空间调用。
- `ao-work` 尝试执行维护子命令。
- 两个 Python 包互相导入。
- Skill 没有唯一 `metadata.workplane`，或声明多个工作面。
- developer 安装中出现 maintainer 运行资产。
- 配置、授权或状态文件从另一工作面隐式读取。

## 5. 工作面内职责

### 5.1 Runtime

- `ao_maint` 只实现故事影响分析、维护验证和其它源头维护原子能力。
- `ao_work` 只实现工作空间、授权、Jira、任务状态、证据和反馈等业务原子能力。
- 两个包可以各自使用 Python 标准库和已锁定依赖，但不得相互导入或共用可变状态。

### 5.2 Skill

Skill 负责选择流程、组织 Runtime 操作、解释结果，并在能力不足时触发 AI 判断或人工确认。每个 `SKILL.md` 必须在标准 frontmatter 的 `metadata` 中声明唯一 `workplane: maintainer` 或 `workplane: developer`。

### 5.3 Rule 与标准资产

- maintainer Rule 可以包含项目目标、设计红线、工作树、提交、发布和故事门禁。
- developer Rule 可以包含业务事实源、授权、证据、语言和停止条件。
- Tapdata 等项目差异只进入 developer 项目标准；AO 专用 Agentic 缺陷流程留在 maintainer 试验资产，不成为 Tapdata 默认规则。

### 5.4 Shell

- `developer/bootstrap/` 只负责 developer-only 安装、更新、回滚、环境准备和 `ao-work` 启动。
- `maintainer/scripts/` 只服务源头仓库的发布、Hotfix 和固定验证。
- Shell 不承载 Jira、任务状态、证据、策略或授权判断。

## 6. developer-only 安装

`~/.agentic-ops` 使用 Git sparse checkout，仅精确检出 developer 生产资产、跨面只读 JSON 协议及 Python 版本元数据：

```text
~/.agentic-ops/
  developer/AGENTS.md
  developer/bootstrap/
  developer/pyproject.toml
  developer/rules/
  developer/runtime/
  developer/skills/
  developer/standards/
  developer/uv.lock
  shared/integration/
  .python-version
  developer/.venv/
  bin/ao-work
  user/
  .local/
```

安装后的正常文件树不得出现 `maintainer/`。安装目录交付 developer 能力并保存当前研发员身份与 Jira 凭证；项目绑定保存在各自业务项目工作空间。
`developer/tests/`、`fixtures/`、fake producer、测试缓存和其它非生产顶层资产不得进入该安装树；Bootstrap 与 Runtime 都验证精确 sparse 集合及可见生产树，发现污染立即阻断。

该 sparse checkout 是面向人和 AI 的工作树、入口与默认可见性隔离，用于防止误入，不是 Git 内容权限边界。同一 managed clone 的 Git 对象仍可能包含源头提交树；developer Rule 禁止读取、恢复或扩大 sparse 范围中的 maintainer 路径。若未来要求内容级不可达或独立权限，必须以独立 developer 分发仓库或导出制品开专题实现，不能把当前方案描述为安全沙箱。

指定分支验证安装（`developer/bootstrap/install-verify-branch.sh`）在 `~/.agentic-ops` 之外的独立目录生成可运行的验证安装：默认从官方远端按指定分支克隆并写入 `.agentic-ops/verification-only` 标记；其 `ao-work` 的安装身份校验与生产一致，仅把「HEAD 是 `origin/main` 祖先」放宽为「HEAD 可达于任一 `origin/*` 远端引用」。本地 `--source-worktree` 模式只校验安装流程，origin 是本地路径，不可运行。验证安装不用于生产维护，`install.sh`、`update.sh`、`rollback.sh` 会拒绝它。详见《指定分支验证安装设计》。

## 7. `shared/` 准入

默认不建立共享代码层。资产只有同时满足以下条件才可进入 `shared/`：

- 不包含角色授权、工作流决策、外部副作用或工作面专属失败码。
- 两个工作面确有稳定、相同的只读语义。
- 移入共享区不会使 AI 获得另一工作面的入口或路径。
- 有测试证明两边只能按声明方式读取。

否则宁可保持少量重复，也不牺牲隔离的可见性和可审计性。

当前源仓 `shared/` 白名单固定为根 `README.md`、`integration/README.md` 和三个
`task-to-pr-*.schema.json`。根 `shared/README.md` 只用于源仓说明，不进入 developer
分发；`~/.agentic-ops` 只可见 `shared/integration/README.md` 与三个 JSON Schema。
这些文件必须是不可执行的普通文件；`shared/` 禁止符号链接、Python、Shell、Skill、
`AGENTS.md` 和任何未列入白名单的路径。Bootstrap 与 Python Runtime 都必须同时校验
提交树白名单和安装后的可见树，不能因为 sparse checkout 隐藏了非法路径而放行。

## 8. 验收

- 根 AI 入口只导向 maintainer，业务工作空间入口只导向 developer。
- `ao-maint` 与 `ao-work` 的解析器、子命令和 Python 包不交叉。
- 两个工作面分别维护授权、配置、状态和测试，没有隐式环境兜底跨面读取。
- `~/.agentic-ops` sparse checkout 不含 maintainer、tests、fixture 或 fake producer；业务工作空间只能发现 developer Skill。
- 目录和 Skill 归属可由静态合同测试验证。
- 固定资源验证确认旧 Go Runtime、`agentic-cli`、`install-resources/` 和根目录旧运行路径没有残留。
- 冻结历史文档明确标为迁移证据，不会被误认为现役操作。
