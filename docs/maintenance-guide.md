# AgenticOps 维护指引

不熟悉本文术语时，先查看[术语表](glossary.md)。

## 1. 从零开始

准备 Git、Python 3.9+ 和 uv：

```sh
git clone --branch develop --single-branch git@github.com:tapstate/agentic-ops.git
cd agentic-ops
./agenticops setup \
  --repository-pool "$HOME/agenticops-repos" \
  --repository-provisioning manual
```

克隆前先完成 [Git SSH 授权指引](security/git-ssh-access.md)，并确认账号有本仓库访问权。
示例显式以 `develop` 作为维护基线；`setup` 会仅 fast-forward 同步该分支、安装本仓库维护
依赖并接入受信 Git Hook。工作区有修改时会停止，不会覆盖修改。`--repository-pool` 配置
开发产品根目录的默认业务仓库主工作树位置；未传时默认是 `${product_root}-repos`。该目录
不能与 Product Root 或项目工作空间互相嵌套，且必须可读、可写、可进入。

推荐初始值是 `manual`：业务仓库须由用户按 `<pool>/<owner>/<repo>` 下载并保持主工作树
洁净，任务工作流只从这里创建 linked worktree。只有项目仓库映射、Git SSH 权限和自动下载
范围均已确认后，才改用 `--repository-provisioning auto-clone`。配置保存于
`.local/repository-pool.json`；它是本机运行配置，不提交。已有项目工作空间会固化当时的池
绑定，修改开发面默认值不会静默迁移它们。

`setup` 用于首次初始化维护工作面。之后在 `develop` 更新当前源码目录：

```sh
./agenticops update
```

`update` 只执行 fast-forward，不自动切换分支、处理分叉、覆盖修改或推送本地提交。
本地领先远端时会继续同步维护依赖和 Hook，并明确报告领先提交数。

## 2. 初始化项目工作空间

维护源码目录与项目工作空间必须分开。以下示例在当前 `develop` 源码目录为 TapData 初始化
工作空间，并立即检查接线：

```sh
workspace="$HOME/agenticops-tapdata"
./agenticops init --workspace "$workspace" --project tapdata
./agenticops doctor --workspace "$workspace"
```

`workspace` 不得是源码目录或其子目录。省略 `--agent` 会接入当前源码目录
提供的全部 Agent；只接入部分 Agent 时重复传入 `--agent <Agent ID>`。

## 3. 维护与运行是一套代码

源码目录直接运行产品；修改 `develop` 后，Gate、Policy、Workflow、Project
和 Adapter 立即从同一份源码运行，不需要复制到另一套安装目录。只有工作空间中的
生成接线可能需要刷新：

```sh
./agenticops doctor --workspace <项目工作空间>
./agenticops repair --workspace <项目工作空间>
```

已启动的 Agent 可能仍持有启动时加载的指引，源码更新后应重启 Agent。通过
`agenticops start` 启动时会自动刷新接线。更新、回退和首次初始化由
`.local/lifecycle.lock/` 串行执行；发布、Hotfix 或固定验收运行期间不要更新源码。

源码目录产生的所有非 Git 状态统一进入：

```text
.local/
├── product.json              # source、仓库、develop 和最近生命周期同步提交
├── repository-pool.json      # 默认 Source Pool 根目录和仓库供给模式
├── repository-worktrees.json # 跨工作空间 worktree 租约
├── lifecycle.lock/           # 生命周期操作期间的临时互斥锁
├── venv/internal/            # 本仓库维护依赖
├── cache/                    # 缓存
├── story-gate/               # 故事审批、证据和运行记录
└── release/                  # 发布运行记录
```

`.local/` 不提交，也不是规则事实源。

## 4. 变更归属

- 标准协议：`contracts/`
- 公司通用门禁：`policies/`
- 平台无关判定：`gate/`
- 确定性状态：`workflow/`
- 项目差异：`projects/<project>/`
- Agent/工具协议差异：`adapters/`
- 安装与接线：`bootstrap/`

新增 Agent 只增加 `adapters/agents/<id>/` 的 Manifest、薄 Hook、模板和测试；不要
修改公共入口建立平台枚举。新增产品项目只增加 `projects/<project>/`。工作项、进度
和验收写入 Jira，不在仓库新增执行计划。

## 5. 验证

运行代码变更必须执行：

```sh
internal/acceptance.sh quick
internal/acceptance.sh full
```

`quick` 检查 Runtime 和资源边界；`full` 执行四项固定验收。也可以按需组合：

```sh
internal/acceptance.sh runtime install
internal/acceptance.sh --list
```

日志和汇总写入 `.local/acceptance/<run-id>/`。OPA 未安装导致 Rego 一致性检查跳过时
必须在交付中说明。不要使用 `--no-verify`。

## 6. 发布与 Hotfix

正常发布：

```sh
internal/release/release.sh prepare --version vX.Y
internal/release/release.sh publish --version vX.Y --confirm-release
```

`publish`、合并和 Tag 需要针对实际候选范围的明确授权。Hotfix 只能使用：

```sh
internal/release/hotfix.sh <JIRA-KEY>
```

它原子更新 `main` 与 `develop`；冲突、分叉或回读不明时停止。源码版本由
`python3 internal/version.py` 输出为 `<分支>-<标签>-<提交数>-<提交编号>`。

详细边界见[项目目标](strategy/project-goals.md)和
[v1 架构](architecture/agenticops-v1-architecture.md)。
