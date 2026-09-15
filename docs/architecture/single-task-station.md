# 单任务研发工位设计合同

本文定义单任务工位的实现合同，供运行代码、项目适配和验收实现共用；不是当前 CLI 使用说明、发布就绪声明或工作项执行计划。产品方向见[项目目标](../strategy/project-goals.md)，现役行为与分层见[v1 工程架构](agenticops-v1-architecture.md)。实现与验收必须分别提供证据；合同本身不声明真实 TapData 环境已运行。

## 1. 目标与范围

一个项目工作空间是一个研发工位，长期保存完整多仓工程，同时至多处理一个任务。并发通过多个独立工位实现。操作面为接管、归档、释放、清理；处理中不切换任务。当前任务完成且释放成功，或经研发明确清理终止并清理成功后，才允许下一任务接管。

首版支持本地工位。每工位使用独立 Git 仓库和固定 checkout，避免共享 Git 元数据中的任务分支占用、配置和回收耦合；现有 Source Pool 不删除、不迁移，可作为只读查找或下载优化来源，但新仓库不能用对象 alternates 等方式依赖缓存存活。缓存丢失不影响已准备工位。允许未完成任务为清理而正式归档，不增加边开发边保存多个归档快照的能力。容器、远程调度、环境池、会话租约、多任务 runtime 和持续优化平台不属于本合同。

公共 Workflow 维护身份、占用、确认、操作恢复和结果核验；Project 定义仓库、版本解析、构建启动及清理配方。Agent 原生工具执行构建、测试和应用运行；不直接照搬含发布、上传或全局配置修改的打包脚本。根入口仍为薄转发，不建立第二套 Runtime。四操作不授权合并、发布、Tag、强推或历史改写。

## 2. 身份与唯一事实

| 标识 | 归属与规则 |
|---|---|
| `station_id` | 复用工作空间已有稳定 `workspace_id` 作为工位身份，只改用户术语，不建立第二个 ID；创建新工位时生成新值，不能复制绑定文件克隆工位 |
| `issue_key` | Jira 工作项身份，用户可见 |
| `run_id` | 复用现役执行编号概念；新接管生成不可复用的随机编号，跨会话恢复不变，清理后重接同一 Jira 也生成新值；不生成多份活动 runtime |
| `operation_id` | 一次状态或资源操作的恢复身份，用户不必输入；重试沿用，独立新操作使用新值 |

对用户主要展示工位名称、任务号、任务结果与当前操作进度。写入请求携带从上下文读出的 `run_id` 和预期状态版本，执行时不得自动用新 run 补齐旧请求。`run_id` 只防止受检查操作和证据串用，不约束任意原生文件写入。

## 3. 路径与数据合同

```text
<workspace>/
├── .agenticops/
│   ├── init.json               # 安装与接线归属、状态 epoch
│   ├── workspace.json          # 工位 ID、Project、Product Root 等稳定绑定
│   ├── current-task.json       # 唯一活动状态；空闲时 current=null
│   ├── operation.json          # 唯一未结束操作或最近操作的结果
│   ├── authorization.json      # 当前执行的授权，始终绑定 run 和范围
│   └── evidence/               # 当前执行的质量、CI、同步及本地事实
├── config/                     # 研发维护的持久配置和秘密引用
├── source/<owner>/<repo>/      # 完整独立 Git 仓库，跨任务保留
├── runtime/                    # 唯一运行配置、Maven local、插件、日志及报告
└── archive/<issue-key>/<run-id>/
    ├── summary.md
    ├── record.json             # 内容清单与证据引用
    ├── evidence/               # 必要的脱敏证据
    └── receipts/               # 追加的清理或释放结果；不修改正式正文
```

`current-task.json` 固定包含 `schema_version`、单调 `revision`、`current`。`current=null` 表示无任务；非空对象包含 `issue_key/run_id/task_class/stage/outcome/engineering_baseline/task_repositories/terminal_proof/archive_ref`。`outcome` 为 `in_progress/completed/interrupted`；`archive_ref` 为空或引用本 run 的正式档案路径和摘要，不改变任务是否完成的事实。只有 `current` 的有无决定工位是否占用，不能再在 workspace 或索引里重复存 occupancy。没有未完成 operation 才可把无任务解释成可接管。

`engineering_baseline` 包含 `status=resolving|frozen`、Project/Profile 版本、解析输入、仓库条目与整体摘要；只有 frozen 可进入源码开发。`task_repositories` 是以仓库 ID 为键的变更与交付记录，引用唯一基线条目。质量、授权和 CI 状态独立留在 `.agenticops/`，都必须匹配 current 的 run/revision；不存在有效 current 时不接受活动证据写入。归档包含这些当前记录的必要脱敏副本，归档不是新的 Jira 或 Git 事实源。

`operation.json` 包含 `schema_version/operation_id/kind/run_id/request_digest/expected_revision/phase/status/steps/confirmation/archive_ref/cleanup_plan/cleanup_manifest`。`steps` 记录每个副作用的意图、精确对象、预期前后事实与回执；`status=running|failed|done`。failed 仍属未完成，不释放工位。归档摘要使用规范化 JSON（键排序、无多余空白、UTF-8）和 SHA-256；文件清单记录相对路径、字节长度和 SHA-256，排除自身哈希和后续 receipts，避免循环摘要。

config 不随任务删除。任务专用有效配置写入 runtime，正式档案只留脱敏配置、版本和秘密引用名称，不复制秘密。runtime 不以 run 分目录；暂存档案只用于原子发布，不属于第二个活动运行环境。

## 4. 两类仓库信息

### 4.1 完整工程基线

基线值的机器合同见 [engineering-baseline.schema.json](../../contracts/engineering-baseline.schema.json)。`workflow/engineering_baseline.py` 提供基线值构造、摘要校验、任务仓库引用和只读本地 Git 对象核验；`projects/tapdata/engineering-profiles.json` 定义完整应用的仓库集合，项目脚本 `engineering_baseline.py` 复用现役分支解析器。值构造中的 `status=frozen` 只表示输入清单已固化，调用方仍必须在接管操作中核验新鲜远端事实及全部本地 Git 对象后持久化；它不表示已创建仓库、已准备 runtime 或已绑定当前任务。基线模块本身不写状态；生命周期由 station 与 task.py 入口持锁编排。

每个 run 只有一份冻结工程基线。条目至少包含 `repository_id/origin/ref_kind/ref_name/commit_sha/resolution_source/rule_version/path`。Profile 决定完整运行所需仓库，Project 的现役仓库目录仍是 origin 唯一来源。解析结果中的 current、展示回退、未核验或 unresolved 不能作为可执行基线。所有条目解析成功并核验 Git 对象后，一次固化全清单摘要；不能把部分准备冒充完整环境。

首次接管从可信 origin 准备缺失仓库；已有仓库先核验路径、origin 和洁净度，再 fetch 明确引用并检出冻结 SHA，不改工作空间之外的主工作树。独立仓库保留任务分支和提交，释放时使用 detached HEAD 脱离旧分支，下一次接管才同步新版本。缺失或冲突不覆盖原仓库；只重试该操作能够证明归属的创建或同步步骤。

准备期间可只读分析，生成物只写已登记 runtime；未冻结完整清单前不能开始代码修改或签发完整工程验证结果。修改实施线需要显式终止并清理原 run 后新接管，不在同一 run 偷换基线。恢复已有 run 保留其 SHA；新 run 如需复用已有分支，必须显式提供历史基线与预期 Head，核验后将该历史基线作为该仓唯一基线，并披露与其它仓当前解析结果的差异；兼容性由实际工程验证证明，不能自动继承旧验收。

### 4.2 任务变更与交付

每项包含 `repository_id/baseline_entry_digest/work_branch/target_branch/approved_scope/verification_method/observation/deliveries/disposition`。不重复保存第二个可变基线 SHA。`observation` 记录实时 HEAD 与源码指纹；指纹涵盖 tracked 差异和未跟踪源码，不把生成物混入授权源码范围。基线、HEAD 和 PR Head 的含义不同。

任务变更仓库可在源码修改前登记，最终可以无变更；工作分支只在声明范围内使用。配套仓默认 detached 在冻结 SHA，可产生声明的构建产物，不得把 detached 当文件系统只读保证。任何配套仓源码变化都必须先加入任务修改范围。

新增修改仓库使用内部 `scope_change` 操作，不增加用户操作种类：先核对冻结条目和洁净度，持久化意图，登记范围，准备工作分支，回读实际 branch/HEAD 后提交绑定。准备分支不等于授权修改代码；现有实现授权如因范围变化失效，先撤销，再重新确认和签发，不能在失败中保留旧授权。原有仓库的授权修改不能因新增仓库被擦除。中断按同一意图恢复。

新工作分支名称由项目规则或用户提供；同名已存在时不覆盖或自动复用。首版每个修改仓库一个工作分支、一个明确 PR 目标。一个任务可跨多个仓库各交付一个 PR；需要同仓多版本回补时另行明确后续工作项，不在当前工位隐式切版本。替代 PR 要记录替代链并以最终有效 PR 的证据结案，不能自由文本豁免未交付成果。

## 5. 操作状态与接口语义

公开写操作只有 `takeover/archive/release/clean`。只读上下文返回 current、operation、完整基线、任务变更、目录与项目执行入口；读取不修改状态。所有写请求有幂等操作 ID；重复请求返回相同操作结果或继续其未完成步骤，不重复接管或再次删除。

| 操作 | 前提与结果 | 阶段 |
|---|---|---|
| 接管 `takeover` | current=null 且无未完成操作；生成新 run 并先绑定工位，成功后可处理任务 | intent → bound → baseline_frozen → source_prepared → runtime_prepared → done |
| 归档 `archive` | 当前任务可为 in_progress/completed/interrupted；按事实标记“已完成”或“未完成”，不判完成、不解绑 | intent → stopped → frozen → archive_published → archive_bound → done |
| 释放 `release` | 研发明确释放、交付/验收核对通过；成功后解绑，任何中途失败保持占用 | intent → terminal_recorded → stopped → frozen → archive_published → cleaned → neutral → unbound → done |
| 清理 `clean` | in_progress 或 interrupted；研发明确终止并确认处置清单；先确保“未完成”档案有效，再记录终止并删除现场，不把 completed 改成 interrupted | intent → stopped → frozen → archive_published → terminal_recorded → cleaned → neutral → unbound → done |

接管前只读核验身份、来源和既有 source 洁净度；随后持久化 intent，再绑定 current，才开始资源副作用。接管失败 current 留在 in_progress，可恢复或明确 clean；不得生成新 run 冒充重试。archive 和 release 不能覆盖尚未完成的其它操作。

现役阶段检查仍维护 task.stage，目标完成检查从“先移除 worktree”改为先验证交付并持久化 outcome=completed，资源释放独立。研发的 release 可以在完成事实已有时执行，或在同一调用中核验并记录 completed；确认只覆盖精确任务/run/成果，不覆盖以后变动。completed 之后出现新的源码差异不回退完成事实，也不能直接清理；必须核验和明确处置额外成果。无代码任务通过 Project 定义的验收证据完成，不用空 PR 清单推导成功。

release 的研发确认绑定任务、run、最终候选摘要和释放范围。clean 确认绑定 run 与精确 `cleanup_plan_digest`；plan 逐项包含对象身份/指纹、范围，以及 delete、retain_ref、retain_config 或明确展示的 export_then_delete。plan 不包含尚未生成的档案摘要。未提交修改的不可逆丢弃必须列出文件和内容指纹；现有精确授权仍有效时不重复询问。未知归属、未列项或确认后新增内容不删除且阻止解绑，不静默 stash。保留在 source 中的 dirty 文件不能被视为工位空闲。

现场漂移通过同一 operation 的追加式计划修订恢复，重新确认精确清单并绑定原摘要与修订编号；保留原请求、旧计划、草稿与已完成回执。正式档案不得覆盖。独立 archive 仅在正式档案尚未发布时允许重新确认草稿清单，此确认不授权删除。释放恢复不能把新增未验收提交纳入旧完成证明；重新生成相同内容的产物也不能沿用旧删除回执。

“归档 + 清理”允许两个顺序调用，也可由一次明确请求按 clean 的阶段编排。先停止任务写入者，生成或核验本 run 标记“未完成”的正式档案；再记录本地 interrupted 和研发终止决定，按已确认清单清理。此前独立 archive 不要求先把任务改为 interrupted，后续 clean 复用该档案，不因 outcome 从 in_progress 变为 interrupted 而重写正文。归档失败或身份、摘要不匹配时不删除现场；清理失败仍保持占用。清理不关闭 PR、不修改 Jira、不撤销远端提交。部分 prepare 失败时，未冻结基线也可 clean：使用已登记创建步骤和准备前事实生成清单，档案如实记录已知材料及缺口，不要求不存在的完整基线或验收结果。完整独立仓库和本地 refs 保留，未知创建残留需人工处置。

## 6. 完成事实与交付核验

完成入口在同一 run 中核验全部工程仓库，包括配套仓；源码 dirty/untracked、未经登记的本地变更或未处置提交阻止正常完成。生成物按项目路径及生产记录分类；未知文件不能默认归生成物。基线到最终候选无源码差异才可记录 no_change，候选必须洁净；仅“没提交”不算无变更。

每个有效交付条目保存 `repository/pr/target_branch/candidate_head/pr_head/merged_at/merge_commit/readback_ref`。正常路径要求最终本地候选 Head 与 PR 合并时 Head 相同，GitHub 已合并且目标等于登记目标，记录实际合并结果；项目使用的 merge/squash/rebase 均按服务端合并事实核验，不能要求原 Head 必为目标分支祖先。必要时核验 merge_commit 在当前目标历史中；目标历史已被改写或无法解释时停止，不猜测合并策略。

旧 PR、被替代 PR、目标错误或本地候选比已合并 PR 多出提交都不能完成。合并后需同步 PR Head 的，必须保持本地洁净且符合已有同步规则，并重新核验候选验证证据，不伪称新 Head 已测。同仓多有效目标不属于首版自动完成路径。

除了 PR，继续核验 Project 现有质量、CI、关联测试及适用的发布验收；任务类型未配置的验收不能凭空要求，已配置要求也不能被“PR 已合并”替代。completed 是本地核验结果，Jira 同步结果另存，不能假称已改 Jira。外部写入未知先回读原操作，清理不再次发送。

## 7. 资源隔离与稳定现场

Project 资源清单定义管理路径、生产动作、停止/核验入口、保留或删除策略。首版 runtime 内固定分出 effective-config、maven-local、plugins、logs、reports 和应用工作目录，仍是一份活动 runtime。Maven 命令显式使用该工位 runtime 的 local repository；不能把本任务 Jar 安装进共享可写缓存再假称隔离。源码内 target、node_modules 或打包输出仅按实际配方逐项声明，不能通用 `git clean -fdx`。

端口在启动时实际绑定并回读，不把“探测时空闲”当预留成功。进程记录 PID、启动时间、可执行程序和工作目录；停止前重新核对身份，避免 PID 重用误杀。只核对本工位登记资源，不扫描整台机器未知进程作为释放条件。停止失败或原会话仍有写入者时保持占用，不宣称工位锁能阻止原生写入。

测试数据使用项目已授权的工位独立实例或可验证命名空间，记录精确对象及创建回执；不能删除共享数据库或按任务号猜测归属。没有安全清理接口时给出精确人工接力，回读完成后才能解绑。秘密使用持久引用及有效配置注入，不复制到档案。接管复用有效环境确认，不在输入未变化时要求逐项重复确认。

正式归档前停止任务应用、测试、构建及其它登记写入者，再核对源代码、配置和产物指纹。人工执行进程必须登记或明确交接；不能证明现场稳定时不发布档案。单独 archive 成功后以 current.archive_ref 锁定本次处理材料，不要求 outcome 已为终态，但不再允许源码开发、阶段推进或新增实施授权；只允许核验、归档重试及相应的释放/清理。这是为结束本次处理而归档，不是暂停后续写的快照。未完成任务必须清理成功后才能接新任务；以后再次接管同一 Jira 使用新 run。

## 8. 归档、清理与中断恢复

archive 不要求任务先完成或终止。`record.task_result` 为 completed 或 incomplete，分别展示“已完成”“未完成”；仅 outcome=completed 且有相应完成事实才记 completed，其余按 incomplete 归档。未完成档案记录处理到哪里、已有成果、未解决事项、已有验证和归档原因，不因缺少合并 PR 或验收报告而拒绝归档；未知材料明确标记缺失。归档不自行设置 completed/interrupted，不解绑，也不改变 Jira。后续清理的本地终止和资源回收结果写入 receipts，不把 incomplete 改写为 completed。两类档案均记录 station/run/issue；事实、推测和未知分开，不补造耗时和次数，不保存完整会话、秘密或原始敏感日志。

正式档案先在 archive 下的受控临时目录生成，清单包含脱敏文件哈希，核验后在同一文件系统原子改名到 `<issue>/<run>`。目标已存在时只能验证相同身份和内容后复用，不能覆盖。record 内包括冻结现场摘要与 `resource_inventory`（资源事实及建议处置），该 inventory 不授予删除权限。敏感资源只记录脱敏对象 ID、建议处置和安全摘要，真实参数留在受限 operation 中。临时目录通过操作 ID 证明归属，不能按通配符清理其它档案。

用户授权范围与执行完整性关联分开：`cleanup_plan` 的规范化内容为 run、对象身份/指纹、处置和范围；`cleanup_plan_digest` 固定这些内容，`confirmation_digest` 证明研发已确认该 plan。正式档案发布后才形成唯一可执行的 `operation.cleanup_manifest`，它引用原 plan 并组合 `cleanup_plan_digest/confirmation_digest/archive_digest`。archive digest 只关联证据，不参与授权范围摘要；补齐它既不修改 plan，也不扩权或要求重复确认。尚无正式档案或有效 plan 确认时不得执行删除。

独立 archive 后，已完成任务走 release，未完成任务走 clean；从原 inventory 与当前相关资源回读生成 plan，取得其尚缺的精确确认，再使用既有 archive digest 形成 manifest，不因归档后记录终止而重新生成档案。既有确认范围和内容仍匹配时直接复用。确认后新增对象不自动扩权；同一 operation 在删除新增项前追加新的 plan 及确认版本，保留旧版本和已执行回执。新增对象作为本操作附加 inventory 留在 receipt 中，不改正式档案；无法证明归属则保持占用。

归档发布后，operation 保存 archive_path/digest，并在同一工位锁内 CAS 更新 current.archive_ref；独立 archive 只有完成 archive_bound 才标记 done。恢复仅校验档案自身及其绑定，不重算已部分清理的现场。若发布已成功但回执或 archive_ref 未写，未完成操作继续阻止开发和接新任务，按预先登记目标和文件清单回读，补回执及绑定。正式正文不可变；实际清理或释放结果在 receipts 中按 operation ID 原子追加，记录 archive digest、实际 cleanup manifest digest 和逐项结果；重复写只能接受相同内容。

所有状态与步骤使用同一工位锁串行；锁文件在 `.agenticops/` 内固定，单次释放不删除锁目录。每个外部副作用前写意图，后写实际回读。步骤原子写临时文件、flush/fsync 后 replace，并持久化父目录；跨文件不宣称事务。release/clean 内的归档仅为 phase，不创建嵌套 operation。

清理按确认清单逐项执行：目标是 symlink、路径越界、身份/内容发生未允许变化、包含未列文件时停止；已删除条目视为幂等成功。源码未提交修改仅在 clean 的精确授权下恢复到该条目已记录的提交内容，不能误用默认 develop；工位 neutral 只切洁净独立 checkout 到已记录的冻结或最终候选提交，保留全部分支 refs。中立状态不要求追远端最新版本。

完成清理后先将证据、授权撤销事实和最终回执持久化；再把当前运行辅助状态清理至空闲允许清单，写 unbind intent，最后 CAS 将 current=null 并增加 revision。operation 未标记 done 时即使 current=null 仍阻止新接管，恢复依据同一 operation/run 的终态回执补 done，不能重新清理无关现场。新 run 建立前必须没有旧活动授权或质量状态；档案完整后才移除其活动副本。

未完成 takeover 可在研发确认后转入 clean，交接始终在同一 operation 文件内可判定：先向旧 operation 原子写入完整 `handoff`（目标 clean operation ID、请求、cleanup plan 及其确认和摘要，此时不要求未来 archive digest），再原子替换为 clean operation，内含 `supersedes`、原操作摘要、已完成步骤与资源记录。新 clean 发布档案后再补齐可执行 manifest。读到旧 takeover 的 handoff 时只继续交接，不再 prepare；读到 clean 时只恢复 clean。旧记录归入 evidence 可随后完成，不作为交接正确性的前置条件。未完成 release/clean 只能恢复同一操作，不允许用另一 kind 覆盖。迟到回执绑定旧 run/op 只能被拒绝或单独追加原档案备注，不能修改新任务或正式档案正文。

## 9. TapData 配方合同

完整应用 Profile 引用 `projects/tapdata/repositories.json` 仓库 ID，不另存 origin；首版集合为 tapdata、tapdata-common-lib、tapdata-connectors、tapdata-connectors-enterprise、tapdata-enterprise、tapdata-license、tapdata-web、tapdata-application、hazelcast，均位于 `source/tapdata/<repo>`。t-layer3-test 保留项目登记，是明确启用的验证依赖，启用后必须在基线冻结前加入；docs/docs-en 已解除 TapData 项目登记，不参与项目仓库准备和分支对齐。解除登记不删除既有本地仓库、Git refs 或任务材料。其它类型任务的精简 Profile 需单独明确，不能偷偷把完整应用 Profile 降级。

分支解析复用 `version-branch-alignments.json` 与现役解析器规则，输出明确 ref/SHA；现有展示回退和 keep-current 结果若无法提供确定执行依据，必须配置明确 ref 后再冻结。产品版本与模块分支不机械同名，目标分支另行登记。已有基线外仓库需要加入时，应先确认 Profile 范围，在冻结前补齐；冻结后发现遗漏而必须扩展完整工程时结束本次处理并明确清理重接，不能修改同 run 清单冒充原环境。

完整配方的目标合同包含 `id/revision/repositories/version_resolver/toolchain_requirements/actions/resources/health_checks`。当前 engineering-profiles.json 仅实现 id/revision/repositories/optional_repositories；其余配方合同尚未实现或未经真实环境验证，不能以删除合同的方式宣称完整应用已交付。actions 应是按目标源码核验的 argv、cwd、环境引用和产物声明，覆盖 prepare/build/start/stop/verify，只引用固定 source/config/runtime 路径。工具版本、Maven profile、Node 包管理器必须从目标分支和已确认环境取得，不猜值；实际启动与健康证据是独立验收层。

现役资源登记通过 station_resources.py 接收 file/process/external 数组并绑定 run。外部资源 quiesced 且回读可信可先归档，最终释放或清理须得到 cleaned；归档后只允许既有 external 身份不变的清理状态/readback_ref 更新，不允许新增资源或改写正式档案。

FE/TM 工作目录在 runtime 中分开，连接同一已确认 Mongo 环境，端口和 backend_url 必须互相匹配。启动健康检查分别记录 TM 可用、FE 连接成功、适用时 Web 可访问；任务验证另证明本次 connector/Jar 的生产与实际加载文件内容一致。未具备数据库或凭据只报告环境缺口，不伪造启动成功。实现配方时需用真实目标分支验证各入口，本文不声明当前模板已可运行。

## 10. 现役改造映射与升级

| 现役位置 | 目标变化 |
|---|---|
| task_store/task.py 与任务注册表 | 单 current 占用、四操作及 run/op 恢复，删除多活动任务歧义；完成不以前置删除 worktree 为条件 |
| repository_worktree 与仓库目录 | 独立固定仓库、完整基线与任务变更引用；旧 task/run 路径只在原版本清理 |
| authorization、quality、CI、evidence、external_sync | 活动路径单份，保留 run/revision 校验，归档后无活动写入；规则仍来自各 Project/Policy |
| bootstrap、workspace schema、init、doctor 与兼容性检查 | 创建/识别 config/source/runtime/archive；检查单 current 与未完成操作；诊断不删除未知文件 |
| TapData Project 与任务 Skill | 完整工程 Profile、分支解析消费、原生运行资源配方和四操作引导；不复制公共状态逻辑 |
| docs、故事合同与测试 | 现役与目标区别在实现发布时收敛，按本合同验收并更新使用说明 |

当前状态代际为 epoch 3，最低升级协议为 2。生成与清理机制先在同版本形成完整闭环，不依赖升级器：生成工位→任务接管/归档/释放或清理→workspace purge→重生成。purge 只移除归属明确的接线与受管状态，保留 source/config/archive；非空 runtime、未知 .agenticops 内容或未完成操作阻止解绑。保留目录可在明确 --reuse-materials 后复用，但不能自动导入配置、历史授权或验收。

跨版本是第二阶段编排：先让原版本完成自身清理，再切换产品并调用新版本生成；新版本不解析旧任务或提供旧清理 Runtime。本次不另发过渡版本，旧安装先在原版本保存材料并受控解绑，再重新安装，不承诺旧升级器直接 update。新协议只保障此后的切换。任一已登记工作空间不兼容或无法核验均阻止版本切换；repair 不跨代际采用。回退使用相同干净边界。操作说明见[更新与回退](../usage/update-and-rollback.md)。

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
| 升级与回退 | 带旧任务拒绝跨 epoch；completed/inactive 经工作空间级 purge 后重建路径可用；外部导出可核验；冲突目录不覆盖；新旧档案与独立源码不误删 |

实施完成条件是上述行为及固定验收有真实证据；设计评审通过只表示合同足以指导实现，不代表 TapData 环境、代码或发布已完成。
