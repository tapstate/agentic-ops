# 单任务工位设计合同

本文定义单任务工位的实现合同，供运行代码、项目适配和验收实现共用；不是当前 CLI 使用说明、发布就绪声明或工作项执行计划。产品方向见[项目目标](../strategy/project-goals.md)，现役行为与分层见[v1 工程架构](agenticops-v1-architecture.md)。实现与验收必须分别提供证据；合同本身不声明真实 TapData 环境已运行。

## 1. 目标与范围

一个项目工位长期保存完整多仓工程，同时至多处理一个任务。并发通过多个独立工位实现。操作面为接管、归档、释放、清理；处理中不切换任务。当前任务完成且释放成功，或经研发明确清理终止并清理成功后，才允许下一任务接管。

首版支持本地工位。每工位使用独立 Git 仓库和固定 checkout，避免共享 Git 元数据中的任务分支占用、配置和回收耦合；源码池由安装目录配置或工位初始化的显式 `--source-pool` 决定，默认 `~/.agentic-ops-repos`。缓存路径是 `repositories/<owner>/<repo>.git`，不按 origin 增加第二层隔离；同名缓存 origin 不一致时失败关闭，由研发者为新工位配置另一个源码池。接管准备源码时先下载缺失缓存或刷新已有缓存，再从缓存传输到独立工位仓库，不能用对象 alternates 等方式依赖缓存存活。缓存丢失不影响已准备工位。允许未完成任务为清理而正式归档，不增加边开发边保存多个归档快照的能力。容器、远程调度、环境池、会话租约、多任务 runtime 和持续优化平台不属于本合同。

公共 Workflow 维护身份、占用、确认、操作恢复和结果核验；Project 定义仓库、版本解析、构建启动及清理配方。Agent 原生工具执行构建、测试和应用运行；不直接照搬含发布、上传或全局配置修改的打包脚本。根入口仍为薄转发，不建立第二套 Runtime。四操作不授权合并、发布、Tag、强推或历史改写。

## 2. 身份与唯一事实

| 标识 | 归属与规则 |
|---|---|
| `station_id` | 复用工位已有稳定 `station_id` 作为工位身份，只改用户术语，不建立第二个 ID；创建新工位时生成新值，不能复制绑定文件克隆工位 |
| `issue_key` | Jira 工作项身份，用户可见 |
| `run_id` | 新接管生成 `<jira-key>-<timestamp-hex>`；`timestamp-hex` 是 8 位、小写、Unix 秒级 HEX。跨会话恢复不变，清理后重接同一 Jira 也生成新值；同一工位若发现既有同名档案则失败关闭，不生成多份活动 runtime |
| `operation_id` | 一次状态或资源操作的恢复身份，用户不必输入；重试沿用，独立新操作使用新值 |

对用户主要展示工位名称、任务号、任务结果与当前操作进度。写入请求携带从上下文读出的 `run_id` 和预期状态版本，执行时不得自动用新 run 补齐旧请求。`run_id` 只防止受检查操作和证据串用，不约束任意原生文件写入。

## 3. 路径与数据合同

```text
<station>/
├── .agenticops/
│   ├── init.json               # 安装与接线归属、状态 epoch
│   ├── station.json          # 工位 ID、Project、Product Root 等稳定绑定
│   ├── current-task.json       # 唯一活动状态；空闲时 current=null
│   ├── operation.json          # 唯一未结束操作或最近操作的结果
│   ├── authorization.json      # 当前执行的授权，始终绑定 run 和范围
│   └── evidence/               # 当前执行的质量、CI、同步及本地事实
├── config/                     # 研发维护的持久配置和秘密引用
├── source/<owner>/<repo>/      # 完整独立 Git 仓库，跨任务保留
├── runtime/                    # 唯一运行配置、任务 Maven local、插件、日志及报告
└── archive/<issue-key>/<run-id>/
    ├── summary.md
    ├── record.json             # 内容清单与证据引用
    ├── evidence/               # 必要的脱敏证据
    └── receipts/               # 追加的清理或释放结果；不修改正式正文
```

`current-task.json` 固定包含 `schema_version`、单调 `revision`、`current`。`current=null` 表示无任务；非空对象包含 `issue_key/run_id/task_class/stage/outcome/engineering_baseline/task_repositories/terminal_proof/archive_ref`。`outcome` 为 `in_progress/completed/interrupted`；`archive_ref` 为空或引用本 run 的正式档案路径和摘要，不改变任务是否完成的事实。只有 `current` 的有无决定工位是否占用，不能再在 station 或索引里重复存 occupancy。没有未完成 operation 才可把无任务解释成可接管。

`engineering_baseline` 包含 `status=resolving|frozen`、Project/Profile 版本、解析输入、仓库条目与整体摘要；只有 frozen 可进入源码开发。`task_repositories` 是以仓库 ID 为键的变更与交付记录，引用唯一基线条目。质量、授权和 CI 状态独立留在 `.agenticops/`，都必须匹配 current 的 run/revision；不存在有效 current 时不接受活动证据写入。归档包含这些当前记录的必要脱敏副本，归档不是新的 Jira 或 Git 事实源。

`operation.json` 包含 `schema_version/operation_id/kind/run_id/request_digest/expected_revision/phase/status/steps/confirmation/archive_ref/cleanup_plan/cleanup_manifest`。`steps` 记录每个副作用的意图、精确对象、预期前后事实与回执；`status=running|failed|done`。failed 仍属未完成，不释放工位。归档摘要使用规范化 JSON（键排序、无多余空白、UTF-8）和 SHA-256；文件清单记录相对路径、字节长度和 SHA-256，排除自身哈希和后续 receipts，避免循环摘要。

config 不随任务删除。任务专用有效配置写入 runtime，正式档案只留脱敏配置、版本和秘密引用名称，不复制秘密。runtime 不以 run 分目录；暂存档案只用于原子发布，不属于第二个活动运行环境。Maven 的安装级与用户级 settings 继续提供镜像、认证、代理和 profile；工位只将本地仓库定向到 runtime，不复制 settings 或共享任务构件。

## 4. 两类仓库信息

### 4.1 完整工程基线

基线值的机器合同见 [engineering-baseline.schema.json](../../contracts/engineering-baseline.schema.json)。`workflow/engineering_baseline.py` 提供基线值构造、摘要校验、任务仓库引用和只读本地 Git 对象核验；`projects/tapdata/engineering-profiles.json` 定义完整应用的仓库集合，项目脚本 `engineering_baseline.py` 复用现役分支解析器。值构造中的 `status=frozen` 只表示输入清单已固化，调用方仍必须在接管操作中核验新鲜远端事实及全部本地 Git 对象后持久化；它不表示已创建仓库、已准备 runtime 或已绑定当前任务。基线模块本身不写状态；生命周期由 station 与 task.py 入口持锁编排。

每个 run 只有一份冻结工程基线。条目至少包含 `repository_id/origin/ref_kind/ref_name/commit_sha/resolution_source/rule_version/path`。Profile 决定完整运行所需仓库，Project 的现役仓库目录仍是 origin 唯一来源。解析结果中的 current、展示回退、未核验或 unresolved 不能作为可执行基线。所有条目解析成功并核验 Git 对象后，一次固化全清单摘要；不能把部分准备冒充完整环境。

首次接管从可信 origin 准备缺失仓库；已有仓库先核验路径、origin 和洁净度，再 fetch 明确引用并检出冻结 SHA，不改工位之外的主工作树。若冻结引用是 branch，则检出只由 AgenticOps 管理、名称含原引用与冻结 SHA 的本地基线分支；tag 和指定 commit 保持 detached。独立仓库保留任务分支和提交，释放时回到同一冻结呈现，下一次接管才同步新版本。缺失或冲突不覆盖原仓库；只重试该操作能够证明归属的创建或同步步骤。

准备期间可只读分析，生成物只写已登记 runtime；未冻结完整清单前不能开始代码修改或签发完整工程验证结果。修改实施线需要显式终止并清理原 run 后新接管，不在同一 run 偷换基线。恢复已有 run 保留其 SHA；新 run 如需复用已有分支，必须显式提供历史基线与预期 Head，核验后将该历史基线作为该仓唯一基线，并披露与其它仓当前解析结果的差异；兼容性由实际工程验证证明，不能自动继承旧验收。

### 4.2 任务变更与交付

每项包含 `repository_id/baseline_entry_digest/work_branch/target_branch/approved_scope/verification_method/observation/deliveries/disposition`。不重复保存第二个可变基线 SHA。`observation` 记录实时 HEAD 与源码指纹；指纹涵盖 tracked 差异和未跟踪源码，不把生成物混入授权源码范围。基线、HEAD 和 PR Head 的含义不同。

任务变更仓库可在源码修改前登记，最终可以无变更；工作分支只在声明范围内使用。配套仓的 branch 基线显示为受管本地基线分支，tag/commit 基线保持 detached；两者都可产生声明的构建产物，不得把分支或 detached 当文件系统只读保证。任何配套仓源码变化都必须先加入任务修改范围。

新增修改仓库使用内部 `scope_change` 操作，不增加用户操作种类：先核对冻结条目和洁净度，持久化意图，登记范围，准备工作分支，回读实际 branch/HEAD 后提交绑定。新分支固定为 `<git_name>/<run_id>`；`git_name` 在工位初始化时从全局 Git `user.name` 校验后一次性写入 `station.json`，缺失或不合法时由研发显式补充，后续不跟随机器配置改变。创建前若本地或远端同名分支存在则失败关闭。续办保留接管时核验的既有分支，不重命名。准备分支不等于授权修改代码；现有实现授权如因范围变化失效，先撤销，再重新确认和签发，不能在失败中保留旧授权。原有仓库的授权修改不能因新增仓库被擦除。中断按同一意图恢复。

新工作分支名称不由项目规则或用户自由指定；普通新分支由工位身份和 run 唯一确定。首版每个修改仓库一个工作分支、一个明确 PR 目标。一个任务可跨多个仓库各交付一个 PR；需要同仓多版本回补时另行明确后续工作项，不在当前工位隐式切版本。替代 PR 要记录替代链并以最终有效 PR 的证据结案，不能自由文本豁免未交付成果。

## 5. 操作状态与接口语义

新接管使用 `facts.station_contract=3`。当前 station epoch 以机器契约为准；旧 epoch 的活动状态由原版本退出，不在本版跨代际续接。一次确认、目录回收、源码成果与外部处置以第 7、8 节为准。

编码前的新任务使用 `source-readiness` 刷新任务目标分支引用。准备先将旧证据标为 refreshing，逐仓保存 fetch 意图和结果，最终记录本次 observed 快照；失败重试安全地刷新同一 run 的证据，不推进阶段。检查全部工程身份、工作区与未登记 ignored 产物，以及任务分支、冻结基线祖先关系和目标分支。等同基线时可进入授权；目标正常向前推进时，用户可用 `--confirm-digest` 与 `--decision-ref` 明确选择保留冻结基线开发、在 PR 前按项目规则同步，或退出后重新接管。分叉、回退、缺失或不可信引用拒绝。grant 及进入 implementation 时重新回读本地与远端，授权绑定 source_readiness_digest；准备过程不修改冻结基线或工作分支内容。已开始编码后不要求工作区始终洁净，也不重复以本检查替代后续代码与 CI 证据。

公开写操作只有 `takeover/archive/release/clean`。只读上下文返回 current、operation、完整基线、任务变更、目录与项目执行入口；读取不修改状态。所有写请求有幂等操作 ID；重复请求返回相同操作结果或继续其未完成步骤，不重复接管或再次删除。

| 操作 | 前提与结果 | 阶段 |
|---|---|---|
| 接管 `takeover` | current=null 且无未完成操作；生成新 run 并先绑定工位，成功后可处理任务 | intent → bound → baseline_frozen → source_prepared → runtime_prepared → done |
| 归档 `archive` | 当前任务可为 in_progress/completed/interrupted；按事实标记“已完成”或“未完成”，不判完成、不解绑 | intent → stopped → frozen → archive_published → archive_bound → done |
| 释放 `release` | 研发明确释放、交付/验收核对通过；成功后解绑，任何中途失败保持占用 | intent → terminal_recorded → stopped → frozen → archive_published → cleaned → neutral → unbound → done |
| 清理 `clean` | in_progress 或 interrupted；研发明确终止并确认处置清单；先确保“未完成”档案有效，再记录终止并删除现场，不把 completed 改成 interrupted | intent → stopped → frozen → archive_published → terminal_recorded → cleaned → neutral → unbound → done |

接管前只读核验身份、来源和既有 source 洁净度；随后持久化 intent，再绑定 current，才开始资源副作用。接管失败 current 留在 in_progress，可恢复或明确 clean；不得生成新 run 冒充重试。archive 和 release 不能覆盖尚未完成的其它操作。

现役阶段检查仍维护 task.stage，目标完成检查从“先移除 worktree”改为先验证交付并持久化 outcome=completed，资源释放独立。研发的 release 可以在完成事实已有时执行，或在同一调用中核验并记录 completed；确认只覆盖精确任务/run/成果，不覆盖以后变动。completed 之后出现新的源码差异不回退完成事实，也不能直接清理；必须核验和明确处置额外成果。无代码任务通过 Project 定义的验收证据完成，不用空 PR 清单推导成功。

release 的研发确认绑定任务、run、最终候选摘要和释放范围。clean 确认绑定 run 与精确 `cleanup_plan_digest`；plan 包含目录身份、范围、源码 archive/export/discard 处置及开发基线。plan 不包含尚未生成的档案摘要。未提交修改的不可逆丢弃必须列出文件和内容指纹；现有精确授权仍有效时不重复询问。未知归属、未列项或确认后新增内容不删除且阻止解绑，不静默 stash。保留在 source 中的 dirty 文件不能被视为工位空闲。

现场漂移通过同一 operation 的追加式计划修订恢复，重新确认精确清单并绑定原摘要与修订编号；保留原请求、旧计划、草稿与已完成回执。正式档案不得覆盖。独立 archive 仅在正式档案尚未发布时允许重新确认草稿清单，此确认不授权删除。释放恢复不能把新增未验收提交纳入旧完成证明；重新生成相同内容的产物也不能沿用旧删除回执。

“归档 + 清理”允许两个顺序调用，也可由一次明确请求按 clean 的阶段编排。先停止任务写入者，生成或核验本 run 标记“未完成”的正式档案；再记录本地 interrupted 和研发终止决定，按已确认清单清理。此前独立 archive 不要求先把任务改为 interrupted，后续 clean 复用该档案，不因 outcome 从 in_progress 变为 interrupted 而重写正文。归档失败或身份、摘要不匹配时不删除现场；清理失败仍保持占用。清理不关闭 PR、不修改 Jira、不撤销远端提交。部分 prepare 失败时，未冻结基线也可 clean：使用已登记创建步骤和准备前事实生成清单，档案如实记录已知材料及缺口，不要求不存在的完整基线或验收结果。完整独立仓库和本地 refs 保留，未知创建残留需人工处置。

## 6. 完成事实与交付核验

完成入口在同一 run 中核验全部工程仓库，包括配套仓；源码 dirty/untracked、未经登记的本地变更或未处置提交阻止正常完成。生成物按项目路径及生产记录分类；未知文件不能默认归生成物。基线到最终候选无源码差异才可记录 no_change，候选必须洁净；仅“没提交”不算无变更。

每个有效交付条目保存 `repository/pr/target_branch/candidate_head/pr_head/merged_at/merge_commit/readback_ref`。正常路径要求最终本地候选 Head 与 PR 合并时 Head 相同，GitHub 已合并且目标等于登记目标，记录实际合并结果；项目使用的 merge/squash/rebase 均按服务端合并事实核验，不能要求原 Head 必为目标分支祖先。必要时核验 merge_commit 在当前目标历史中；目标历史已被改写或无法解释时停止，不猜测合并策略。

旧 PR、被替代 PR、目标错误或本地候选比已合并 PR 多出提交都不能完成。合并后需同步 PR Head 的，必须保持本地洁净且符合已有同步规则，并重新核验候选验证证据，不伪称新 Head 已测。同仓多有效目标不属于首版自动完成路径。

除了 PR，继续核验 Project 现有质量、CI、关联测试及适用的发布验收；任务类型未配置的验收不能凭空要求，已配置要求也不能被“PR 已合并”替代。completed 是本地核验结果，Jira 同步结果另存，不能假称已改 Jira。外部写入未知先回读原操作，清理不再次发送。

## 7. 任务目录与重置边界

“重置工位”是 clean/release 共用的退出行为，不新增第五个生命周期入口，也不替代 station purge。config、完整独立 source、正式 archive 及初始化接线保留；runtime 是任务独占可丢弃区域，清空内容但保留根目录。持久配置、源码唯一副本和用户手工材料不能放入 runtime。完成任务仍通过 release 核验原有交付事实；未完成任务通过 clean 归档为 incomplete，再记录 interrupted，不修改 Jira 完成状态。

runtime 采用 `clear_children_keep_root`，其直接和深层生成内容均在授权域内，不逐文件登记。源码内无法迁出的 target/node_modules 等目录由 Project engineering profile 的 generated_directories 声明，实际生产前按精确路径登记为 `source-generated/delete_root`。配方模式只允许选择候选路径，不授予已有非空目录的删除权。目录身份包含 station/run、路径、生产者、配方 ID/版本、父目录及根目录的 device/inode。Workflow 先写创建意图，再 mkdir 并写创建回执，最后才能启动生产者；已存在目录只能在显式采用且确认为空时登记。创建后回执前中断不能猜测非空目录归属；仅按原创建意图核验生产者、配方、父身份和空目录后继续。初始 current CAS 同时保存已核验空 runtime 的采用身份，允许在后续目录登记前中断时取消接管。

受管根及祖先禁止链接、越界、挂载点；源码生成根不得包含 tracked 文件。递归删除基于目录 FD，不跟随内部符号链接，只删除链接本身；跨设备、嵌套 .git、FIFO/socket/device 等特殊对象停止处理。生产者在预检前已移除的生成根，以 observed_missing_before_intent 显式列入确认并记录观测缺失；确认后、清理意图前才消失的根须重新确认，不能冒称本次已删除。目录回执后再次出现内容时停止，必须明确补充处置，不能沿用旧回执重删。这不是本地安全沙箱，不承诺阻止任意原生进程写入。

Agent 使用原生能力停止已登记应用、构建、测试和其它写入者，Workflow 回读身份；不能凭 PID 号码猜测进程，不扫描整机。容器、数据库命名空间等运行资源必须完成精确回收，或由项目给出保留不会污染后续任务的隔离证据。未知外部写入仅检查当前 run 已登记 operation、活动证据和同步记录，必须先回读原操作；不能改成 retain 绕过，不宣称能检测未登记的原生调用。

## 8. 一次确认、成果归档与恢复

预检展示 task/run、目录根和身份、源码成果处置、每仓开发引用及完整提交 SHA、保留项、归档策略与当前阻塞。确认绑定这些确定范围；独占目录内部生成内容和停止时产生的日志不改变授权范围。源码内容、根身份或处置范围变化需要展示差异并补充确认。正式档案摘要是执行证据，不加入范围摘要，不因归档完成再次询问同一授权。

退出顺序为：展示范围并确认 → 停止写入者并核验 → 保存并核验正式档案 → 回收目录及明确处置源码 → 检出开发基线 → 撤销授权并清除活动材料 → CAS 解绑 → done。原生工具负责停止动作；检查点发现写入者未停时保留现场。独立 archive 仍支持只归档不释放，后来 clean/release 复用覆盖当前成果的档案与有效确认。

### 8.1 源码成果与日志

每份待移除成果只允许三种确定动作：archive、export、discard。默认 archive 保存 HEAD→index 和 index→worktree 两份 binary/full-index patch、必要 untracked 文件实际字节及路径/大小/hash/mode，记录 origin、Head、分支和保留引用。在临时仓库按顺序重建并比对文件、index entries 和 mode；不能只有 diff 文件名或状态摘要。代码材料只用于审计和人工取回，不重新激活旧 run。

export 必须写到本次清理范围以外的受控位置，实际内容、源码快照及回读均核验；discard 必须额外确认精确仓库、路径和内容指纹。无任何有效动作时不得恢复或删除源码。敏感正文不进入档案，不通过改写 patch 假称保存完整；超过大小上限或不支持的冲突索引、submodule、源码链接等对象停止并明确处置。旧档案必须逐成果摘要覆盖，不因整体档案有效就推定保存了新增成果。归档保存脱敏总结、结构化证据和 Project 指定 runtime/logs、runtime/reports 文本；停止期间新增日志先追加不可变回执。归档保存上述脱敏材料，不保存原始敏感日志或依赖缓存。

正式档案在 archive/<issue> 的操作专属临时目录准备，核验后原子改名到 archive/<issue>/<run>，发布后正文不可覆盖。目录回收、解绑和后续分支处置只追加 receipts。发布成功但回执或 archive_ref 未写时，恢复先核验已有档案并补绑定，不重算已部分回收的现场。

### 8.2 开发基线与分支

接管时按 Project 每仓 dev_branch 解析已下载开发引用，保存完整 SHA、引用来源、观察时间及配方版本，独立于任务冻结基线。重置只使用已确认且本地存在的 SHA：branch 基线回到同一受管本地基线分支，tag/commit 回到 detached，不移动命名开发分支或任务分支，不执行 rebase、reset、stash 或网络 fetch。任务 Head 通过计划中明确展示的 refs/agenticops/archive/<run>/<repository-digest> 保留；此引用不自动删除。下次接管重新刷新、核验目标开发基线。

没有有效归位 SHA 的已检出仓库停止；未完成 takeover 中有可信创建事实但尚未 checkout 的 .git-only 仓库可保留并标为待准备，沿同一 handoff_clean 恢复。未选中的已登记持久仓库保留，核验身份、无写入者及接管前后状态；source 中未知对象阻止空闲，不当作生成物删除。

本地/远端任务分支和 PR 默认保留，普通重置不调用 GitHub。删除分支或关闭 PR 是重置后的独立选择：先展示精确对象、SHA、状态和保护信息，经用户确认后以原生工具执行。workflow/station_disposition.py 只向原 archive 的 receipts 按 disposition_id 追加 intent 和 readback，不占 current、不重启旧 operation、不执行外部调用。unknown 只回读原调用，不重发，也不撤销已完成的本地重置。

### 8.3 操作日志、空闲判定与兼容

operation.json 保留操作身份、阶段和材料引用；不可变大清单与归档草稿按内容摘要保存在 .agenticops/operation-data。生成物清理回执按目录保存在 archive receipts，不按文件保存 intent/receipt，不随生成文件数重写完整状态。各阶段先持久意图再执行副作用，实际结果回读后原子写入回执，文件及父目录 fsync；跨文件不声称事务。

删除中断后恢复同一目录意图；delete_root 缺失只有存在可信创建和删除意图、父身份匹配时才可补回执。clear_children_keep_root 必须证明原根身份未变且为空。范围变化通过同一 operation 追加计划修订和确认；正式档案不可覆盖，新增源码必须明确 export/discard 或另行安全保存，不能假称被旧档案覆盖。

空闲必须满足：runtime 为空、受管源码生成目录不存在、本次工程源码洁净且在确认开发 SHA、配置档案及保留引用完整、已登记写入者停止、运行资源无污染、没有活动授权/证据和未完成 operation。未知材料不删除且阻止解绑。先持久化结果与授权撤销事实，再清除活动副本、写 unbind intent、CAS current=null、补 done；任何中断沿原操作恢复，不碰新任务。

目录归属、源码材料、操作 sidecar 和恢复语义使用机器契约声明的当前 epoch。任何旧 epoch 工位必须在原版本退出并受控 purge 后显式重建；本版不在线迁移或续接旧代际 operation。purge 校验并移除归属明确的 operation-data，保留 source/config/archive，未知或损坏状态停止。

### 配置化清理计划版本 5

`station-clean.py` 在原 clean/release 操作内使用清理计划 schema 5，分别绑定中央和项目名单摘要及根对象分类结果，不合并配置。未知对象、规则与核心生命周期冲突、未登记清理目录均在归档前预检拒绝；命中保留目录不遍历子树。源码构建目录由原生项目工具清理，计划绑定项目配方、命令输入、源码/引用及原始对象范围；报告先保全至 runtime/reports 并校验摘要。一次范围确认后操作等待原生清理回执，聚合检查退出码、残留和漂移，再正式归档。Workflow 不执行构建命令，不递归删除 source-generated 产物。原生删除后的缺失由同一计划回执解释，不能要求用户重复确认相同范围。旧版本 4 计划只按原合同恢复；新增原生等待阶段与回执是不兼容状态变更，工位 epoch 升至 14。

执行顺序为正式归档核验、同一操作的源码成果恢复与 Git detached 归位、已登记目录回收、活动状态清理及解绑。源码步骤复用既有成果证明和 neutral 回执，额外的 station-source-reset 完成回执阻止恢复时重复还原旧 HEAD；后续仍重新核验源码洁净、归位 SHA、保留引用和产物缺失。普通工位根目录通过既有 directory 登记创建为 station-generated，删除使用原有目录身份与 FD 回收机制；不从配置取得未知目录的删除权。

状态代际变化代表新增目录、计划字段或执行顺序已经不兼容旧版读写。升级与回退在切换前只比较 epoch：变化时要求 Product Root 工位登记为空；原版本完成退出与 purge 后，再显式初始化。升级器不读取旧任务或 operation；目标版本不在线迁移、续接或 repair 采用旧代际状态。

## 9. TapData 配方合同

完整应用 Profile 引用 `projects/tapdata/repositories.json` 仓库 ID，不另存 origin；首版集合为 tapdata、tapdata-common-lib、tapdata-connectors、tapdata-connectors-enterprise、tapdata-enterprise、tapdata-license、tapdata-web、tapdata-application、hazelcast，均位于 `source/tapdata/<repo>`。t-layer3-test 保留项目登记，是明确启用的验证依赖，启用后必须在基线冻结前加入；docs/docs-en 已解除 TapData 项目登记，不参与项目仓库准备和分支对齐。解除登记不删除既有本地仓库、Git refs 或任务材料。其它类型任务的精简 Profile 需单独明确，不能偷偷把完整应用 Profile 降级。

分支解析复用 `version-branch-alignments.json` 与现役解析器规则，输出明确 ref/SHA；现有展示回退和 keep-current 结果若无法提供确定执行依据，必须配置明确 ref 后再冻结。产品版本与模块分支不机械同名，目标分支另行登记。已有基线外仓库需要加入时，应先确认 Profile 范围，在冻结前补齐；冻结后发现遗漏而必须扩展完整工程时结束本次处理并明确清理重接，不能修改同 run 清单冒充原环境。

完整配方的目标合同包含 `id/revision/repositories/version_resolver/toolchain_requirements/actions/resources/health_checks`。当前 engineering-profiles.json 仅实现 id/revision/repositories/optional_repositories；其余配方合同尚未实现或未经真实环境验证，不能以删除合同的方式宣称完整应用已交付。actions 应是按目标源码核验的 argv、cwd、环境引用和产物声明，覆盖 prepare/build/start/stop/verify，只引用固定 source/config/runtime 路径。工具版本、Maven profile、Node 包管理器必须从目标分支和已确认环境取得，不猜值；实际启动与健康证据是独立验收层。

现役资源登记通过 station_resources.py 接收 directory/process/external/source-disposition 数组并绑定 run；file 只用于已有工具的证据登记，不替代目录归属。生产前创建生成目录，运行资源与可选分支/PR 按第 7、8 节分别处置，归档后不恢复开发或改写正式档案。

FE/TM 工作目录在 runtime 中分开，连接同一已确认 Mongo 环境，端口和 backend_url 必须互相匹配。启动健康检查分别记录 TM 可用、FE 连接成功、适用时 Web 可访问；任务验证另证明本次 connector/Jar 的生产与实际加载文件内容一致。未具备数据库或凭据只报告环境缺口，不伪造启动成功。实现配方时需用真实目标分支验证各入口，本文不声明当前模板已可运行。

## 10. 现役改造映射与升级

| 现役位置 | 目标变化 |
|---|---|
| task_store/task.py 与任务注册表 | 单 current 占用、四操作及 run/op 恢复，删除多活动任务歧义；完成不以前置删除 worktree 为条件 |
| repository_worktree 与仓库目录 | 独立固定仓库、完整基线与任务变更引用；旧 task/run 路径只在原版本清理 |
| authorization、quality、CI、evidence、external_sync | 活动路径单份，保留 run/revision 校验，归档后无活动写入；规则仍来自各 Project/Policy |
| bootstrap、station schema、init、doctor 与兼容性检查 | 创建/识别 config/source/runtime/archive；检查单 current 与未完成操作；诊断不删除未知文件 |
| TapData Project 与任务 Skill | 完整工程 Profile、分支解析消费、原生运行资源配方和四操作引导；不复制公共状态逻辑 |
| docs、故事合同与测试 | 现役与目标区别在实现发布时收敛，按本合同验收并更新使用说明 |

当前状态代际和最低升级协议以机器契约为准。生成与清理机制先在同版本形成完整闭环，不依赖升级器：生成工位→任务接管/归档/释放或清理→station purge→重生成。purge 只移除归属明确的接线与受管状态，保留 source/config/archive；非空 runtime、未知 .agenticops 内容或未完成操作阻止解绑。保留目录可在明确 --reuse-materials 后复用，但不能自动导入配置、历史授权或验收。

跨版本是第二阶段编排：先让原版本完成自身清理，再切换产品并调用新版本生成；新版本不解析旧任务或提供旧清理 Runtime。升级器只比较当前与目标 epoch：相同可直接切换，变化时要求可读取的空工位登记；任一登记工位、登记缺失或无法核验均阻止版本切换。repair 不跨代际采用。回退使用相同干净边界。操作说明见[更新与回退](../usage/update-and-rollback.md)。

## 11. 可复用验收合同

以下是实现必须具备的行为测试，不代表本设计已执行这些场景。运行代码仍通过 Runtime、Resources、Install、Release 四项固定验收。

| 场景 | 必须观察到的结果 |
|---|---|
| 首次接管完整工程 | 全 Profile ref/SHA 可核验，修改仓使用工作分支；真实应用加载本任务产物并验证目标行为 |
| 双仓库信息 | 任务绑定只引用冻结基线；当前 Head/PR 更新不改变它；配套仓违规修改会被发现 |
| 独占与身份 | current 或未完成操作存在时拒绝新任务；同 run 恢复，新接管新 run；旧回执拒绝 |
| 两个工位 | 固定源码、Git 元数据、Maven 写入、端口和测试数据互不污染 |
| 范围变更中断 | 分支创建前后中断均可按同意图恢复，旧授权不因写入失败重新有效 |
| 正常交付 | 本地候选与有效 PR Head/目标/合并事实及项目验收一致；支持目标项目允许的合并策略 |
| 完成后释放失败 | completed 不回退，占用不解除；恢复后证据可追溯 |
| 中途清理 | 精确授权后 interrupted，删除范围匹配；默认保留 Git refs 和远端事实；未知修改阻止解绑 |
| 未完成任务归档后清理 | in_progress 可归档为 incomplete，无需先完成或终止；归档后仍占用且停止开发；clean 复用原档案，清理成功才解绑，正文不被改成 completed |
| 部分接管转清理 | 未冻结基线可退出，已创建资源可核验，旧 prepare 不能在清理后继续写入 |
| 归档/清理每阶段崩溃 | 停止前不归档，发布回执丢失可读回，删除一半后不重算档案，解绑回执丢失可恢复 |
| 独立归档后释放 | 原 inventory 不授权删除；release manifest 精确绑定确认与档案；新增对象不静默删除或改写档案 |
| 归档前确认清理 | 确认只绑定 cleanup plan，档案发布后补封套不使确认失效；新增对象必须重新确认对应 plan |
| 路径与资源边界 | symlink/越界/新增内容/PID 重用/未知数据不被误删；不清理共享缓存或其它工位 |
| 顺序复用 | A 完成释放或清理终止后才能接 B；B 无旧任务 Jar/插件/数据/授权，A 档案仍完整 |
| 升级与回退 | 带旧任务拒绝跨 epoch；completed/inactive 经工位级 purge 后重建路径可用；外部导出可核验；冲突目录不覆盖；新旧档案与独立源码不误删 |

实施完成条件是上述行为及固定验收有真实证据；设计评审通过只表示合同足以指导实现，不代表 TapData 环境、代码或发布已完成。

## 下载缓存的准备与兼容

源码池为独立 bare 仓库，仅缓存远端分支和标签，不保存任务、授权或验证事实。每个仓库使用独占锁串行刷新及传输；首次下载和工位克隆均先在本次临时目录完成校验再发布，失败可按原操作重试。工位使用本地 Git 传输并禁用本地硬链接优化，origin 恢复为可信远端，后续推送仍指向真实仓库。源码池可以跟随远端更新标签，工位已有标签冲突仍停止，不能强制覆盖。

初始化仍只生成空工位接线；接管确定 Project Profile 后才按所选仓库准备源码。缓存不可用时停止当前源码准备，不绕过先入池的顺序；已经完成 fetch 的恢复直接核验工位已有引用，不刷新缓存或重新解释历史基线。工位 purge 不清理产品根缓存，也不清理旧版源码池。

本次只改变对象下载路径，不改变 `.agenticops/` 字段、clone/fetch 意图与回执含义，下载路径变化本身不改变 epoch；任务重置合同使用 epoch 5。同 epoch 已完成步骤和未完成 fetch 可直接恢复；工位不存在但 clone 已回执仍拒绝重建。缓存不是工位状态的迁移来源。

## 同周期方案修订

`replan` 是当前任务内的受控修订，不是第五种任务生命周期或新 run。只允许未归档、进行中的 design_review/implementation/pr_review/ci_validation。准备输出绑定当前 revision、完整任务摘要、各源码指纹、远端工作分支、项目目录和新仓的精确引用；确认后原子登记操作，先撤销授权，再准备新增仓，最后写入 design_review。所有原仓、PR 观察和交付历史保留，不能从登记中删除有变更的仓库。

工程基线仅追加 Project Profile 许可的可选仓。`extension_history` 保存前版摘要、新条目及确认引用，revision 单调增加；核验时重建摘要链，已有条目不允许修改。新增工作分支统一使用本 run，原有续办分支名称不改变。新仓必须洁净，原仓允许保持已确认的 dirty 指纹；仓库就绪与授权检查复用同一指纹，发生漂移须重新核对。构建产物、运行资源和原生工具权限仍按原规则处理。

操作中断沿原 operation_id、输入和确认引用恢复。current 写入后只完成回执，不倒退已生效方案。写入前可以 abort，保留原方案和所有已下载源码，撤销状态不复活；部分仓库的来源与指纹留在现役 current，供清理预检核对，不能当成已准备成功。仍未完成检出的源码可能需要在下一次使用前按实际现场完成准备，不能猜删或声称已可用。

质量日志不改写。受影响检查项通过本次修订摘要失效，未受影响项保留原结果和决定；任务级汇总按当前方案重新核对。新方案确认可复用本次真实人工决定的来源，不因内部摘要改变再次询问相同事实。旧报告仍对应原代码和用例，不能标记为新版本通过。该语义属于本开发版本 epoch 13，旧 epoch 工位须先原版结束和 purge，不在线迁移。

## 源码指纹与兼容边界

质量证据、扩仓及中断恢复共用工作目录指纹。干净仓库返回精确 HEAD；dirty 仓库使用版本域、tracked diff 的 SHA-256，以及按文件名字节排序的未跟踪记录生成组合摘要。每条记录明确编码文件名字节长度、原始文件名字节、普通文件或符号链接类型、普通文件的可执行标记，以及内容或链接目标的 SHA-256，防止名称与内容边界不同却得到相同输入。普通文件内容按块读取，符号链接只读取目标字符串，不跟随目标；本地指纹不是防篡改证明。

本次指纹语义变更将工位 epoch 从 14 提升至 15，兼容清单只支持当前 epoch。旧 dirty 恢复记录不能由新算法解释；升级与回退必须先在原版本结束任务并归档释放或清理，再显式 purge 注销受管状态，随后切换版本并重建工位，具体入口见[更新与回退](../usage/update-and-rollback.md)。不重新哈希、迁移或复用旧任务的质量确认、恢复记录与授权。
