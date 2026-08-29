# AgenticOps 维护指引

## 1. 从零开始

准备 Git、Python 3.9+ 和 uv：

```sh
git clone git@github.com:tapstate/agentic-ops.git
cd agentic-ops
./agenticops setup
```

无需给 `git clone` 增加 `--branch develop --single-branch`。`setup` 会安全切换并
跟踪 `develop`、仅 fast-forward 同步、安装本仓库维护依赖并接入受信 Git Hook。
工作区有修改且需要切换分支时会停止，不会覆盖修改。

## 2. 维护与运行是一套代码

源码根本身就是 Product Root；修改 `develop` 后，Gate、Policy、Workflow、Project
和 Adapter 立即从同一份源码运行，不需要复制到另一套安装目录。只有工作空间中的
生成接线可能需要刷新：

```sh
./agenticops doctor --workspace <项目工作空间>
./agenticops repair --workspace <项目工作空间>
```

源码根产生的所有非 Git 状态统一进入：

```text
.local/
├── product.json              # source、仓库、develop 和当前提交
├── venv/internal/            # 本仓库维护依赖
├── cache/                    # 缓存
├── story-gate/               # 故事审批、证据和运行记录
└── release/                  # 发布运行记录
```

`.local/` 不提交，也不是规则事实源。

## 3. 变更归属

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

## 4. 验证

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

## 5. 发布与 Hotfix

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
