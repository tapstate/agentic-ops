# AgenticOps 维护指引

不熟悉本文术语时，先查看[术语表](glossary.md)。

## 旧版空工位的一次性恢复

正常升级遵循[更新与回退](usage/update-and-rollback.md)。当指定旧版清理器无法处理自身缓存且绑定产品根已切换时，可由维护人员使用 `internal/workspace_recovery.py`；它不安装到使用工作面，不解析活动任务，也不修改旧 epoch 或复用历史授权。

此工具仅支持产品提交 `25d0b2c66298b5c00eb961d2929116846e113681` 生成的 epoch 2、任务索引为空的 TapData 工位。其它版本、有任务、未知状态、改动接线或不可信目录均停止，不扩大为通用兼容工具。先停止该工位 Agent 和应用写入，再生成清单：

```sh
python3 internal/workspace_recovery.py plan --workspace <绝对工位路径> --product-root <绝对产品根> --backup <工位外同文件系统的新备份目录>
python3 internal/workspace_recovery.py apply --backup <备份目录> --confirmed-digest <核验并确认的摘要> --writers-stopped
```

plan 固化工位身份、旧状态和接线指纹。apply 取得工位、旧状态及缓存锁，逐项回读后原子移出受管接线，最后整体移出已核验的旧状态目录。文件保存在备份中，不递归删除原材料；源码、配置、归档、其它用户文件及数据库不在处置范围内。秘密可能存在于旧配置，备份仅当前用户可访问，不提交或上传。符号链接按原目标字符串保存，备份不作为可直接运行的旧工位。

中断后使用原摘要重试，通过原位置或备份位置的唯一匹配恢复；不覆盖已有备份或新工位。结果 exported 只表示导出成功，不表示新工位已可用。确认旧绑定已移出后，单独使用新版本原生 init/doctor 重建；若保留材料非空，按正常规则确认并使用 --reuse-materials。恢复旧现场需单独核验和授权，不能把备份直接覆盖到新工位。

## 1. 从零开始

准备 Git、Python 3.9+ 和 uv：

```sh
git clone --branch develop --single-branch git@github.com:tapstate/agentic-ops.git
cd agentic-ops
./agenticops setup
```

克隆前先完成 [Git SSH 授权指引](security/git-ssh-access.md)，并确认账号有本仓库访问权。示例显式以 `develop` 作为维护基线；`setup` 会仅 fast-forward 同步该分支、安装本仓库维护依赖并接入受信 Git Hook。工作区有修改时会停止，不会覆盖修改。业务源码由独立工位的 source 管理，不写入产品根。

工位接管时按完整 Profile 下载缺失独立仓库；无权限或已有目录不洁净时停止对应准备。产品不再管理共享池或跨工位源码租约。

`setup` 用于首次初始化维护工作面。之后在 `develop` 更新当前源码目录：

```sh
./agenticops update
```

`update` 只执行 fast-forward，不自动切换分支、处理分叉、覆盖修改或推送本地提交。本地领先远端时会继续同步维护依赖和 Hook，并明确报告领先提交数。

## 2. 初始化项目工作空间

维护源码目录与项目工作空间必须分开。以下示例在当前 `develop` 源码目录为 TapData 初始化工作空间，并立即检查接线：

```sh
workspace="$HOME/agenticops-tapdata"
./agenticops init --workspace "$workspace" --project tapdata
./agenticops doctor --workspace "$workspace"
```

`workspace` 不得是源码目录或其子目录。省略 `--agent` 会接入当前源码目录提供的全部 Agent；只接入部分 Agent 时重复传入 `--agent <Agent ID>`。

## 3. 维护与运行是一套代码

源码目录直接运行产品；修改 `develop` 后，Gate、Policy、Workflow、Project 和 Adapter 立即从同一份源码运行，不需要复制到另一套安装目录。只有工作空间中的生成接线可能需要刷新：

```sh
./agenticops doctor --workspace <项目工作空间>
./agenticops repair --workspace <项目工作空间>
```

已启动的 Agent 可能仍持有启动时加载的指引，源码更新后应重启 Agent。通过 `agenticops start` 启动时会自动刷新接线。更新、回退和首次初始化由 `.local/lifecycle.lock/` 串行执行；发布、Hotfix 或固定验收运行期间不要更新源码。

源码目录产生的所有非 Git 状态统一进入：

```text
.local/
├── product.json              # source、仓库、develop 和最近生命周期同步提交
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
- 维护面协作指引：`skills/`；它们只供维护 Agent 使用，不安装或接线到业务工作空间。Skill 的分类、事实源、发现接线和迁移要求见 [Skill 维护规范](skill-maintenance.md)。

新增 Agent 只增加 `adapters/agents/<id>/` 的 Manifest、薄 Hook、模板和测试；不要修改公共入口建立平台枚举。新增产品项目只增加 `projects/<project>/`。每个 Jira Project Profile 必须配置 `jira.takeover_watermark`：逻辑键固定为 `agenticops_version`，配置实际 `customfield_<ID>`、字符串字段名、启用的 Jira 事务类型 ID 和 `overwrite` 写入方式；`workflow/project_rules.py` 会拒绝缺失或无效配置，不能绕过接管门禁。工作项、进度和验收写入 Jira，不在仓库新增执行计划。

## 5. 验证

运行代码及 Skill 变更按固定合同执行完整验收一次：

```sh
internal/acceptance.sh full
```

`full` 执行四项固定验收。开发中可先用 `quick` 检查 Runtime 和资源边界，或按需组合检查；它们不能替代要求的完整验收。不要求先跑 `quick` 再机械重复 `full`。完整验收通过后，仅在候选变化、检查失败或存在未解决风险时追加验证：

```sh
internal/acceptance.sh runtime install
internal/acceptance.sh --list
```

日志和汇总写入 `.local/acceptance/<run-id>/`。OPA 未安装导致 Rego 一致性检查跳过时必须在交付中说明。不要使用 `--no-verify`。

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

它原子更新 `main` 与 `develop`；冲突、分叉或回读不明时停止。源码版本由 `python3 internal/version.py` 输出为 `<分支>-<标签>-<提交数>-<提交编号>`。

详细边界见[项目目标](strategy/project-goals.md)和 [v1 架构](architecture/agenticops-v1-architecture.md)。

## 7. 维护 Agent 协作与模型适配

截至 2026-09-13，OpenAI 的 [GPT-6 Astra 指引](https://developers.openai.com/api/docs/guides/latest-model)说明：模型更敏感于 Skill 和仓库指令，可能多问澄清问题、扩大测试；也支持执行中补充要求和异步工具能力。这些是模型与平台能力说明，不是 AgenticOps 的权限保证。

维护面据此采用根 `AGENTS.md` 的协作约定：沿用有效授权、先完成可审查准备、只在真实决策处暂停；技能编写检查见 [Skill 维护规范](skill-maintenance.md)。初始化技能区分明确更新与意图不清的重建，接管测试技能区分必填绑定与可选回复，避免把重复确认当作安全措施。

异步工具、执行中补充要求和推理强度调整以当前 Agent 实际暴露的接口为准；不能因为模型支持就假定 CLI、桌面端或 Adapter 已支持。没有异步能力时按顺序执行并报告阻塞，不新增 Runtime 模拟平台调度，也不自行修改用户模型配置。等待期间可做独立只读分析；恢复后先核验原操作及当前目标，再消费结果。子代理只用于已要求的独立工作，接管测试仍使用一个执行子代理，不扩大并发写入范围。

指引调整的人工场景核对包括：明确更新既有工作空间时直接 repair；缺工作目录时只问该目录；已有绑定不符时停止覆盖；用户中途收窄目标时停止受影响步骤；外部写结果未知时先回读；测试通过且候选未变时不重复运行。静态核对与固定验收只能证明指令和资源合同，不能宣称 Astra 行为、真实 Jira 接管或多平台端到端已验证。
