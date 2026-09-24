# AgenticOps 维护指引

不熟悉本文术语时，先查看[术语表](glossary.md)。

## 旧版空工位的一次性恢复

正常升级遵循[更新与回退](usage/update-and-rollback.md)。当指定旧版清理器无法处理自身缓存且绑定产品根已切换时，可由维护人员使用 `internal/station_recovery.py`；它不安装到安装目录，不解析活动任务，也不修改旧 epoch 或复用历史授权。

此工具仅支持产品提交 `25d0b2c66298b5c00eb961d2929116846e113681` 生成的 epoch 2、任务索引为空的 TapData 工位。其它版本、有任务、未知状态、改动接线或不可信目录均停止，不扩大为通用兼容工具。先停止该工位 Agent 和应用写入，再生成清单：

```sh
python3 internal/station_recovery.py plan --station <绝对工位路径> --product-root <绝对产品根> --backup <工位外同文件系统的新备份目录>
python3 internal/station_recovery.py apply --backup <备份目录> --confirmed-digest <核验并确认的摘要> --writers-stopped
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

工位接管时按完整源码 Profile 准备独立仓库；无权限或已有目录不洁净时停止对应准备。产品管理 bare 下载缓存以加速准备，但不共享工位 Git 元数据或管理跨工位源码租约；缓存不是任务事实源，详见[工位源码与材料](usage/station-materials.md)。

`setup` 用于首次初始化产品源码目录。之后在 `develop` 更新当前源码目录：

```sh
./agenticops update
```

`update` 只执行 fast-forward，不自动切换分支、处理分叉、覆盖修改或推送本地提交。本地领先远端时会继续同步维护依赖和源码 Git Hook，并明确报告领先提交数。

## 2. 初始化测试工位

维护源码目录、候选安装目录与项目工位必须分开。公开入口只允许已安装 Product Root 绑定业务工位，不允许可变源码目录直接绑定。以下示例从当前源码仓库的已提交 `develop` 创建本地候选安装，再初始化新的 TapData 测试工位；不推送，不触碰已有安装或工位：

```sh
source_root="$(pwd -P)"
test_root="$(mktemp -d "${TMPDIR:-/tmp}/agenticops-test.XXXXXX")"
candidate_root="$test_root/product"
station="$test_root/station"
./agenticops install --install-home "$candidate_root" --repository "$source_root" --branch develop
"$candidate_root/agenticops" station init --station "$station" --project tapdata
"$candidate_root/agenticops" station doctor --station "$station"
```

Git 克隆只包含该分支已提交内容，不包含当前未提交修改；先核对源码候选提交与安装 HEAD。未提交候选的安装范围必须另外精确记录，不能把上面的克隆结果称为已包含工作树。正式固定验收由[验证](#5-验证)的既有入口准备隔离候选，不要为运行测试擅自提交或推送。

`station` 不得是产品源码、安装目录或其子目录；测试父目录也应在二者之外。省略 `--agent` 会接入候选安装提供的全部 Agent；只接入部分 Agent 时重复传入 `--agent <Agent ID>`。示例只初始化和诊断，不接管 Jira；实际接管测试按已有测试绑定及授权执行，结束后保留现场供审查，退出和 purge 另按正常边界处理。

## 3. 维护与运行是一套代码

源码目录可以直接运行同一套 Gate、Policy、Workflow、Project、Adapter 模块和维护测试，不维护第二套 Runtime。业务工位则运行其绑定的候选安装快照，不会随着源码工作树编辑而热更新。需要复测新候选时准备新的安装快照和测试工位；已有安装的升级遵循[更新与回退](usage/update-and-rollback.md)，不能复制源码覆盖活动安装。

工位中的同版本可再生接线可能需要刷新，命令必须从其绑定安装运行：

```sh
<绑定安装目录>/agenticops station doctor --station <项目工位>
<绑定安装目录>/agenticops station repair --station <项目工位>
```

已启动的 Agent 可能仍持有启动时加载的指引，绑定安装或接线更新后应重启 Agent。通过 `agenticops station start` 启动时会尝试刷新接线；发现旧托管 Hook 时保留现场并要求同 epoch 的显式迁移，跨 epoch 不能用 refresh/repair 续接。源码仓库 Git Hook 独立保留，不是使用者的工具拦截链。更新、回退和首次初始化由 `.local/lifecycle.lock/` 串行执行；发布、Hotfix 或固定验收运行期间不要更新源码。

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

先从实际受阻或易出错的场景检查现有要求：是成果标准缺失、判定错误，还是辅助工具失败。优先修正现有判定、去除重复步骤或复用原生能力；只有现有机制不能解决问题时，才新增规则、配置或状态，并在现有方案中说明收益、适用边界和维护代价，不另建登记或审批流程。例如，源码清理应依据 Git 跟踪事实和已确认的处置范围，不因 Java 包目录名为 `target` 就增加 Maven 输出目录登记。

评审时用代表性场景比较改动前后的操作负担和结果：正常路径是否减少人工判断或重复操作，脚本中断后能否在原权限内接力，结果不合格时能否明确缺项并补齐。相关行为纳入受影响用例；文档更新和脚本成功本身不证明流程改善，未做真实场景验证的结论应明确限制。权限、保全和外部未知结果的边界不能因简化流程而取消。验证入口与执行时机沿用下节，不新增一套检查流程。

- 标准协议：`contracts/`
- 公司通用门禁：`policies/`
- 平台无关判定：`gate/`
- 确定性状态：`workflow/`
- 项目差异：`projects/<project>/`
- Agent/工具协议差异：`adapters/`
- 安装与接线：`bootstrap/`
- 产品维护协作指引：`skills/`（不含 `shared/`）；它们只供维护 Agent 使用，不安装或接线到业务工位。共享需求设计入口为 `skills/shared/ao-requirement/`，维护根和项目工位共用方法，按目标项目读取约束。Skill 的分类、事实源、发现接线和迁移要求见 [Skill 维护规范](skill-maintenance.md)。

新增 Agent 只增加 `adapters/agents/<id>/` 的 Manifest v3、必要的指引模板和测试（不提供通用工具 Hook）；不要修改公共入口建立平台枚举。新增产品项目只增加 `projects/<project>/`。每个 Jira Project Profile 必须配置 `jira.takeover_watermark`：逻辑键固定为 `agenticops_version`，配置实际 `customfield_<ID>`、字符串字段名、启用的 Jira 事务类型 ID 和 `overwrite` 写入方式；`workflow/project_rules.py` 会拒绝缺失或无效配置，不能绕过接管门禁。工作项、进度和验收写入 Jira，不在仓库新增执行计划。

`python3 workflow/project_rules.py` 的 `render`、`branch` 和 `workflow` 子命令必须通过 `--project <project>` 显式指定项目。无工位的 Python 调用同样提供 `project`，不再默认 TapData；工位调用使用 `station` 的现有绑定。缺少项目时停止读取或生成，旧脚本应补齐参数，不能用默认业务项目代替缺失输入。

准入清单生成使用 `admission.json` 中各 `task_classes.<class>.doc` 的路径，新任务类型无需修改通用渲染器。路径必须为 `projects/<project>/admission/<英文小写连字符名称>.md`，不允许越界、符号链接或多个任务类型覆盖同一文件。生成器先检查全部目标并渲染正文，再开始写入；`render --check` 只检测漂移，不创建目录。既有 TapData 文档路径和正文保持不变。

## 5. 验证

运行代码及 Skill 变更整理为明确候选后，使用一次正式验收同时完成四项检查和提交门禁证据：

```sh
internal/acceptance.sh full --change-source staged
```

此入口薄转发至 `story-gate verify`，固定执行 Runtime、Resources、Install、Release；故事注册表已在 Runtime 内执行，不重复计为第五项。已提交范围使用 `full --change-source range --base <base> --head <head>`，且当前实际 HEAD 必须等于指定 head。staged 前后要求工作树与索引一致，无非忽略的未跟踪输入；不会自动暂存、stash 或清理用户文件。

不带 `--change-source` 的 `full`、`quick` 或组合检查只用于诊断，不产生门禁证据。`quick` 包含完整 Runtime，并不承诺快速。开发中按需运行受影响用例，候选确定后再正式验收，不要先执行诊断 full 再重复正式四项。

```sh
internal/acceptance.sh runtime install
internal/acceptance.sh --list
```

维护审查可使用 `python3 skills/ao-review-change/scripts/review-context.py --change-source staged` 读取精确候选摘要。`acceptance_evidence` 在 Story Gate 已核验匹配证据时返回 `run_id` 和四项检查结果，便于记录 Jira 验收引用；没有有效证据时为 null。它不读取原始日志，不签发批准，也不替代代码审查。

诊断日志写入 `.local/acceptance/<run-id>/`；正式日志写入 `.local/story-gate/runs/<impact-id>/<run-id>/`，最新自包含摘要位于 `.local/story-gate/evidence/`。同一精确候选从暂存到 commit/range、推送可以消费匹配证据；基线、完整树、变更范围或行为相关环境变化则重验。显式再次调用 verify 始终重新执行，不做跨候选缓存。重验开始即使旧通过和审批失效，失败、取消、超时保留本次非通过记录。机器崩溃残留锁需人工确认没有在途进程后处理，不自动抢锁。

检查上限分别为 Runtime 600 秒、Resources 120 秒、Install 600 秒、Release 300 秒；超时回收检查进程组，不用提高等待上限冒充性能优化。生命周期测试保留完整九仓端到端和双仓隔离，其余使用独立最小临时工程，输出逐用例耗时；正式记录同时给出每组耗时。性能调优使用同机同配置三次中位数，不把时间阈值作为普通 CI 硬门禁。

证据 v5 的环境字段只包含实际工具版本、两个维护依赖文件的摘要和测试行为开关，不记录绝对路径或敏感环境变量。正式四项拒绝启用可选 Maven 集成开关；有需要时单独运行诊断并附上真实集成证据。OPA 缺失或可选 Maven 用例未启用必须明确披露，不声称真实集成已验收。Git 读取和检查子进程清除外部 `GIT_*` 上下文覆盖，候选不能使用 assume-unchanged 或 skip-worktree 隐藏工作树变化。原始日志目录和文件分别限制为 0700 和 0600。发布校验器核验历史开发记录后，仍在自身隔离环境重新完整验收。v3/v4 记录仅保留查阅，不满足新版合同；首次升级走受保护 main 的独立人工 PR，不要求旧 release submit-review 接受新格式，不双写旧证据。不要使用 `--no-verify`。

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

产品维护据此采用根 `AGENTS.md` 的协作约定：沿用有效授权、先完成可审查准备、只在真实决策处暂停；技能编写检查见 [Skill 维护规范](skill-maintenance.md)。初始化技能区分明确更新与意图不清的重建，接管测试技能区分必填绑定与可选回复，避免把重复确认当作安全措施。

异步工具、执行中补充要求和推理强度调整以当前 Agent 实际暴露的接口为准；不能因为模型支持就假定 CLI、桌面端或 Adapter 已支持。没有异步能力时按顺序执行并报告阻塞，不新增 Runtime 模拟平台调度，也不自行修改用户模型配置。等待期间可做独立只读分析；恢复后先核验原操作及当前目标，再消费结果。子代理只用于已要求的独立工作，接管测试仍使用一个执行子代理，不扩大并发写入范围。

指引调整的人工场景核对包括：明确更新既有工位时直接 repair；缺工作目录时只问该目录；已有绑定不符时停止覆盖；用户中途收窄目标时停止受影响步骤；外部写结果未知时先回读；测试通过且候选未变时不重复运行。静态核对与固定验收只能证明指令和资源合同，不能宣称 Astra 行为、真实 Jira 接管或多平台端到端已验证。
