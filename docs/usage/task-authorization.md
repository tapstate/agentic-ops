# 任务接管与授权

本页说明单工位脚本操作。实际 Jira 事实须由已配置原生工具读取，本地 takeover 不替代 Jira 准入。完整四操作、退出与精确清单输入见[项目任务 Skill](../../projects/tapdata/skills/tapdata-task/SKILL.md)；质量输入见[质量检查与证据](quality-checkpoints.md)。

## 清理范围决策视图

`station-clean.py` 预检返回 `cleanup_scope`：`remove_or_reset` 列目录回收及每仓复位目标，`preserve_before_clear` 列源码成果处置及日志报告保全，`retain` 列配置、仓库及引用、已登记分支/PR、工位接线和正式档案，`unknown` 列尚未核验对象。每项说明原因及允许的决定；保留源码仓库不等于保留工作区修改。

该视图不生成第二份执行计划，不读取保留配置正文、不扫描整机或远端资源；远端状态未查询时明确标注未核验。预检阻塞时仍返回部分清单，但不允许确认执行。用户可选源码 archive/export/discard；丢弃仍需精确快照确认，删除保留分支或关闭 PR 仍是重置后的独立操作。核心持久目录销毁不是任务清理选项。

恢复展示原计划和已完成/待完成步骤，范围漂移按原 cleanup-amend 处理；运行根内部正常生成物不逐文件重做授权。完成时展示原范围、实际步骤回执及档案引用，空闲预检不执行清理。视图错误不代表已成功的生命周期操作失败，先回读原操作，不重复执行。

## 接管完整工程

在已初始化、current=null 且没有未完成操作的工位，先核验 Jira 类型、状态、负责人及产品版本。显式接管：

```sh
python3 <agenticops-root>/workflow/task.py takeover \
  --issue-key TAP-123 --task-class defect_fix --version <已确认主仓分支> \
  --profile full-application --operation-id <op-id> --expected-revision <revision> \
  --dir <station>
```

operation-id 使用 op- 前缀的稳定随机标识；重试保持同值和原请求。可选验证仓库在冻结前加入；所有必需仓库解析并核验后冻结完整工程。随后 repository context 读取 source 路径与基线，不使用远程页面替代本地事实。

## 登记修改仓与阶段

```sh
python3 <agenticops-root>/workflow/task.py repository add \
  --issue-key TAP-123 --expected-run-id <run> --expected-revision <revision> \
  --operation-id <op-id> --repo <owner/repo> \
  --base-branch <PR目标分支> --scope <范围> --verification <验证方式> --dir <station>
```

修改仓必须属于冻结工程；新工作分支由 `<git_name>/<run_id>` 自动生成，已有分支不能隐式复用。仅续办接管时保留既有分支并显式传入 `--work-branch`。准入、snapshot、issue-versions 和 quality 仍按项目规则补齐。advance 带当前 expected-run-id 与 expected-stage，拒绝后先回读，不补造事实或自动更新参数重放。

### 设计前修订已登记范围

当前任务处于 `task_intake` 或 `design_review`、源码干净且尚无交付或 PR/CI 观察时，可在用户确认后修订同仓同分支的范围和验证方式：

```sh
python3 <agenticops-root>/workflow/task.py repository amend \
  --issue-key <issue> --expected-run-id <run> --expected-revision <revision> \
  --operation-id <op-id> --repo <owner/repo> \
  --expected-binding-digest <原绑定摘要> --expected-head <完整Head> \
  --scope <新范围> --verification <新验证方式> \
  --decision-ref <真实确认来源> --dir <station>
```

原绑定取 `repository context` 的 `task_repositories[repo]`，摘要按 `workflow.engineering_baseline.digest` 计算。命令核验 origin、工作分支、Head、原绑定和 revision，不创建或切换分支，不重建基线，不覆盖观察、交付或失败历史。变更前撤销原授权；新范围使旧 Q1/Q2、发布确认和共同验证材料失效，即使代码 SHA 未变也须重新核对。之后按已确认的新方案重新记录质量检查点和授权，不将范围修订本身视作实施授权。

中断后保持原 operation-id、revision、摘要、Head 和请求恢复；任务绑定已写入时只补回读和回执，不重复写任务。不同请求不得复用 operation-id。开发后期或有交付证据时拒绝，不能手改阶段或状态绕过。

这是 epoch 4 内的兼容扩展：任务绑定和质量事件格式保持不变，沿用 `scope_change` 操作种类；旧验证事件用事件时保存的 context 重放并计算绑定摘要，不迁移或改写旧日志。

### 已有分支/PR 的两条处理路径

同 run 恢复保留基线；目标分支推进不代表要重接。新 run 续办需要 takeover --continuation-input <json>，以 repository ID 为键给出 work_branch、baseline_sha、expected_head。历史基线和候选均为真实完整 commit SHA，分支与预期 Head 必须匹配；不是把最新目标当旧分支起点。登记此修改仓时另用 --expected-head 核验续办分支。

新分支重做必须先 archive/clean 结束旧处理，保留 refs 和档案，再接新 run。不能只换 operation 或修改基线绕过现有占用；旧 PR 不自动关闭，旧验证不借给新候选。

## 实施授权

在已冻结源码上形成方案，使用 Q1/Q2 将根因或功能目标、修改范围、风险、回滚、验收项/预期/执行方式和允许后续动作一次性明确确认。未经确认不签发实施授权。之后按 authorization.py grant 的现役参数绑定 issue/run、Agent 和方案版本；所有授权写入须携带 expected-run-id。

范围或方案改变使旧确认失效；必要时 revoke，重新确认和签发，不手改授权文件。改变完整工程版本则先结束当前处理再接管。归档后不再继续开发。

### 方案未变时显式续签

审查或 CI 等待导致授权到期，不必仅因此结束当前任务。先用 `authorization.py show` 查看原确认，并用 `show --digest` 取得原授权摘要；向用户展示当前 run、方案、仓库范围和新的有效期，取得明确决定及可回查来源后，使用此前固定的摘要执行：

```sh
python3 "$agenticops_root/workflow/authorization.py" renew \
  --issue-key "$task_key" --expected-run-id "$task_run" \
  --expected-authorization-digest "$confirmed_authorization_digest" \
  --confirmed-by "$decision_maker" --confirmation-ref "$confirmation_source" \
  --ttl-hours 8 --dir "$project_station"
```

`renew` 仅适用于当前未归档任务的 `design_review`、`implementation`、`pr_review` 和 `ci_validation`。它核对原授权的任务、run、方案摘要、完整仓库绑定及当前 Project endpoint，只延长有效期并追加确认记录，不修改阶段、基线、方案、Agent 或质量证据。原授权摘要已变化、授权已撤销、方案或绑定改变、缺少旧方案摘要时拒绝；这些情况不能通过自动换参数重试解决。方案改变仍须按原流程重新设计确认。成功后重复提交同一摘要也拒绝，不能利用重放延长授权。确认来源是可回查记录，不提供用户身份认证。

### 同一实施范围内重新确认验收方案

实现后更正用例或执行方式时，先按质量合同重新确认 Q2；已有有效用户指令可作为确认来源，不重复索要相同决定。若任务、run、Q1、实施计划事实和全部仓库绑定均未变，可使用 `reconfirm` 将原授权绑定到新确认的 Q2：

```sh
python3 "$agenticops_root/workflow/authorization.py" reconfirm \
  --issue-key "$task_key" --expected-run-id "$task_run" \
  --expected-authorization-digest "$confirmed_authorization_digest" \
  --expected-q2-digest "$confirmed_q2_digest" \
  --confirmed-by "$decision_maker" --confirmation-ref "$confirmation_source" \
  --dir "$project_station"
```

执行前核对 `show --digest` 的原授权摘要与 `quality.py status` 的已确认 Q2 摘要。命令只替换 Q2 绑定，追加 `reconfirmations` 历史；有效期、Agent、实施范围、基线、质量执行记录和阶段保持不变。旧授权与目标 Q2 任一变化、Q2 未确认、原授权到期或撤销、Q1 或实施绑定变化时拒绝。重复请求不会覆盖历史；结果不明时先回读再判断，不能自动刷新摘要重放。

项目配置更正导致 Q1 摘要变化时，先核对接管事实并通过质量 CLI 重新确认 Q1，再显式提供 `--expected-q1-digest <已确认的当前Q1摘要>`。该参数只允许绑定已经有效确认的目标 Q1，并追加旧、新 Q1 摘要；未提供时仍要求 Q1 不变。实施计划事实与完整仓库绑定仍必须一致，不能用配置修复扩大范围。有效事实和用户决定可复用，执行证据不得改写。

该入口不扩大实施授权。新增可选历史字段兼容既有工位 epoch；不迁移旧任务，旧授权与仅续签的回归继续保留。确认来源只记录可回查决定，不提供身份认证。

## 相关文档

- [首次使用指引](../usage-guide.md)：安装与初始化项目工位。
- [权限与安全边界](../security/permissions.md)：凭证、服务器保护与 Workflow 检查点的边界。
- [v1 工程架构](../architecture/agenticops-v1-architecture.md)：单工位、多仓库和四操作模型。

## PR 前同步检出来源

功能与缺陷使用相同操作：回读当前任务登记的仓库、work_branch、base_branch 和冻结 base_sha；保留任务独立提交，不能以最新来源替换原始基线。Agent 原生查询已登记 origin 的对应来源分支最新完整 SHA，并保存查询来源与时间；本地 remote-tracking ref 或历史缓存不能冒称最新远端事实。

需要同步时，展示具体仓库、工作分支、来源分支及 SHA，确认本次 Merge 已被明确授权后才原生 fetch/merge。已取得覆盖这些对象的方案授权不重复询问；任务一般授权不能默认为包含 Merge。同一工作分支上的编辑、Merge 和最终验证串行执行。来源已包含时原生 Merge 可返回已是最新，仍记录本次核对的来源 SHA。

合并完成后调用只读检查：

```sh
python3 <agenticops-root>/workflow/source_sync.py \
  --repo <source-repository> --work-branch <registered-work-branch> \
  --base-revision <frozen-base-sha> --source-revision <just-queried-source-sha>
```

工具核对工作分支、干净状态及祖先关系；它不查询远端、不授予 Merge 权限、不更新冻结基线，也不证明行为正确。将输出与原生远端查询和 Merge 记录一并保存为当前 run 证据。来源不明、历史改写、文本冲突或结果不明时保留当前工作树，先解决或由研发决定从最新基线重建，不能自动丢弃工作或重置任务。PR 创建/更新前再检查来源是否前进；已前进则重复同步及受影响验证，不能仅复用旧包含关系。

### 同步后的分析与重验

同步前保存任务 Head；同步后给上述命令增加 `--before-merge-revision <同步前任务完整SHA>`。输出保留三组范围和差异指纹：原基线到同步前任务、原基线到新来源、新来源到最终任务。任务原提交未保留时拒绝，不能把重建或丢弃后的代码当作普通 Merge。

Agent 原生读取这三组差异，核对原任务目的是否仍满足、来源更新是否改变调用关系/配置/预期，并按最终代码重新确定模块及上层 Jar 消费范围。不能只检查冲突文件或两组路径交集：不同文件的 API、配置和依赖同样可能产生行为交互。工具总是要求行为分析，不自动判断语义等价。

补充必要用例后，执行最终范围的本地验证并回读对应 PR CI；关联当前源码、用例、依赖 Jar 和报告。若 Merge 或后续处理改变版本，旧执行按原版本保留，不能改写为当前结果；来源已包含且代码、用例及依赖全部未变时可引用仍有效的原执行并说明核对依据。失败按同一问题的累计修复记录续办；研发引导处理后重做受影响环节，不默认清空任务或从头开始。

## 重置工位

### 配置化清理入口

`python3 <agenticops-root>/workflow/station-clean.py --dir <station>` 只读展示当前任务与版本 5 清理计划；未完成任务返回放弃变更问题，不在脚本内部猜测确认或阻塞等待终端输入。预检阻塞仍返回任务与问题，但不提供可执行计划。新清理尚未开始时 `--abandon-changes no` 无副作用退出；已有未完成清理操作时返回恢复提示，不声称撤销此前动作。工位空闲且没有未完成操作时不删除材料。

中央 `policies/station-clean.json` 必须存在，项目 `projects/<project>/station-clean.json` 可省略。两份配置均为 `{"version":1,"preserve":[".idea/"],"clean":[{"pattern":"scratch/","action":"remove"}]}` 的结构，独立读取，按中央白、项目白、中央黑、项目黑顺序判定；全部未匹配时保留并报告，不配置 `other` 或 `unmatched_policy`。模式仅匹配工位根名称，支持 `*`、`?` 和目录后缀 `/`，不支持前导 `/`、深层路径、`**`、否定或方括号语法。源代码子树由 Git 阶段检查，不由名单递归匹配。

黑名单动作限定为 `source-reset`、`clear-children`、`lifecycle-clean` 和 `remove`；前三项分别只适用于 source、runtime、.agenticops。中央默认保留 config、archive、.idea，项目不能通过白名单跳过核心生命周期。初始化接线仍按原 manifest 核验。`remove` 只用于任务独占的普通根目录，生产前使用现有 `station_resources.py` 登记 `kind=directory`、根名称和 producer；Workflow 创建并登记身份。名单不能认领已有非空目录、初始化接线或任意文件，已有空目录采用仍须 `adopt_empty=true`。需要删除其它对象时停止并明确处置，不把规则匹配当成删除授权。

确认请求放在工位外，包含现有 summary、reason、decision_ref、confirmed_digest，另加 `cleanup_version:5`。未完成任务请求增加 `abandon_changes:true`，并使用下列命令明确传递用户的放弃决定；完成任务不需要放弃参数，但仍须现役 terminal proof 和 candidate_digest。归档保留成果，放弃不删除 Git 分支或提交。

```sh
python3 <agenticops-root>/workflow/station-clean.py --dir <station> --issue-key <issue> --expected-run-id <run> --expected-revision <revision> --operation-id <op-id> --input <工位外请求.json> --abandon-changes yes
```

预检一次列出原生配方、未登记产物、报告保全缺口和其它阻塞。执行清理前先停止写入者，运行 `python3 <agenticops-root>/workflow/native_cleanup.py preserve --dir <station> --issue-key <issue> --expected-run-id <run>`；它只将配方匹配的关键报告复制至 runtime/reports，并核对摘要，不删除原物。修复全部阻塞后重新预检，再由用户确认完整范围一次。

提交确认请求后，操作在 `awaiting_native_clean` 返回。Agent 按计划各仓 cwd/argv 使用原生工具执行：Java 为绑定当前 runtime/maven-local 的 `mvn -Dmaven.repo.local=<已核验路径> clean`，Web 只接受当前 package.json 明确声明的 pnpm/npm clean 脚本；其它仓使用 `projects/tapdata/repo-cleanup.json` 指定的固定项目脚本，不复用数据库清理脚本。Workflow 不运行这些命令，也不提供通用目录删除兜底。

将全部仓库结果一次写入 JSON（键为仓库 ID，值含整数 exit_code 与可回读 source_ref），使用 `native_cleanup.py receipt`，参数同 preserve 并增加 `--operation-id <原op-id> --input <回执.json>`。成功回执必须同时满足源码/引用未变、登记产物消失、保全报告一致。失败与残留聚合输出；处理后沿用原操作和确认，不能伪造成功退出码。新增对象、脚本、源码或范围变化需完整补充确认。回执不代表产品监督了原生命令，只是带来源的执行事实；原生工具仍遵守宿主权限。

回执通过后再次调用原 `station-clean.py` 请求，正式归档保全报告及源码成果，再复位源码、回收 runtime 并解绑。已执行原生清理导致登记根消失，不要求重复确认；父身份漂移仍停止。版本 4 请求仅沿旧合同恢复，不采用版本 5 的执行语义。

中断后沿用原 operation-id、原 expected-revision 和原请求恢复；不得改用当前 revision 或另建操作。`station-source-reset.py` 是同一版本 4/5 操作的独立 Git 阶段入口，要求已发布且覆盖当前成果的档案，以及当前 issue/run/revision/operation-id，不得单独用于未归档源码。正常清理由入口调用同一实现，无需手工追加执行一次。

工位根目录的 `.idea/` 属于 IntelliJ IDEA 配置，由中央白名单统一保留，不遍历、归档或删除其内容。目录模式不匹配同名文件，符号链接按通用安全规则拒绝。白名单可以覆盖同层宽泛黑名单；不同清理动作同时命中同层对象才是冲突。工位代际及目标产品支持范围以[机器兼容清单](../../contracts/station-state-compatibility.json)为准，清理计划版本不代表工位 epoch；不兼容工位须由匹配的原产品版本处理，升级顺序见[更新与回退](update-and-rollback.md)。

未初始化子模块只在索引、当前提交与归位提交的 gitlink 路径和对象完全一致，且路径不存在或为空目录时保留。清理前和归位时均核验，不递归检出子模块；含内容、符号链接、gitlink 变更或冲突时仍停止。此边界不改变工位状态格式或 epoch，也不授权删除子模块内容。

### 原清理操作的恢复

本节仅适用于当前运行时支持的工位代际，支持范围以[机器兼容清单](../../contracts/station-state-compatibility.json)为准。已有 schema 3 操作保留原请求和执行顺序：保留 config、完整 source 和正式 Product Root `.archive/`，清空 runtime，按原 Project 配方回收已登记源码生成目录。不得将正在执行的旧计划改成 schema 4；新操作使用上面的配置化入口。边界、源码 archive/export/discard 与中断恢复以[工位合同](../architecture/single-task-station.md#8-一次确认成果归档与恢复)为准。

构建前将日志、插件、下载和可迁出的生成物定位到 runtime；不能迁出的生成目录先用 station_resources.py 创建：输入数组项为 `{"kind":"directory","path":"source/tapdata/tapdata/target","producer":"maven"}`，已有空目录的显式采用另加 `"adopt_empty":true`。目录非空时不能补登记猜归属；源码生成路径必须符合工程 Profile 的 generated_directories。

```sh
python3 <agenticops-root>/workflow/task.py cleanup-preflight --issue-key <issue> --expected-run-id <run> --dir <station>
python3 <agenticops-root>/workflow/task.py clean --issue-key <issue> --expected-run-id <run> --expected-revision <revision> --operation-id <op-id> --input <工位外请求.json> --dir <station>
```

请求包含 summary、reason、真实 decision_ref 和 cleanup_plan.digest 对应的 confirmed_digest。默认源码归档，显式丢弃还需 discard_digest；完成任务改用 release 并提供 candidate_digest。Agent 先按原生能力停止写入者再执行，Workflow 检查并按同一操作归档及重置，无需归档后二次确认。归位结果保持冻结引用语义：branch 回到受管本地基线分支，tag/commit 回到 detached；命名开发分支和任务分支不移动，下次接管才联网刷新。

需要保留敏感或超过普通归档上限的源码时，先在工位外创建当前用户私有的导出目录（0700），再运行 `python3 <agenticops-root>/workflow/station_export.py --dir <station> --issue-key <issue> --expected-run-id <run> --path source/<owner>/<repo>/<file> --output <绝对导出文件路径>`。工具独占写出 0600 文件、重建核验并回读登记 export 决定；不覆盖已有不同内容，输出仅含路径和摘要。单成果原始内容上限 512 MiB，编码后上限 768 MiB。失败退出操作中的导出另带原 expected-operation-id，随后按新范围补充确认；正式归档后的导出意图和回执进入 Product Root `.archive/<run-id>/receipts`。

分支和 PR 默认保留。用户选择删除/关闭时，本地重置完成后使用原生工具，另通过 `workflow/station_disposition.py --dir <station> --issue-key <issue> --run-id <原run> --input <处置.json>` 记录 intent/unknown/readback。该工具只记追加证据，不发送外部请求。意图需稳定 disposition_id、精确 object_id、resource_type、action、before 身份及保护回读、decision_ref，并以不含 confirmed_digest 的意图对象摘要确认；回执绑定 intent_digest 和 object_id，包含实际 readback_ref。disposition_id 是本次外部操作的稳定关联键，原生 API 支持幂等键时使用同值；API 不支持时不能把它当作服务端去重保证。重试已有意图只用于回读，不能据此重发。工位已有新任务时拒绝旧任务处置，避免删除被复用的对象。

关键日志与报告集中到 runtime/logs、runtime/reports（由 Project 的 archive_runtime 指定），归档保留脱敏后的 UTF-8 正文；停止期间新增内容以不可变附件追加后再删除。单文件超过 16 MiB、总量超过 64 MiB 或非文本材料须先安全导出并留下摘要，不能静默丢弃。

重置失败保持占用；恢复原操作和原请求。范围变化使用 cleanup-amend 绑定原计划摘要及修订号，源码新增成果须明确处理。不兼容工位先使用匹配的原产品版本结束任务并归档释放或清理，再显式执行 station purge；解绑成功后才切换产品并重新初始化，详见[更新与回退](update-and-rollback.md)。目标版本不解析或迁移旧任务，repair 不跨代际采用。

## 完成预检与等待合并

`task.py next` 在 `ci_validation` 阶段同时核对源码洁净、登记工作分支、最终候选的合并 PR 事实、质量验收、CI 和有效方案授权。多个仓库缺失合并事实时一次列出 `awaiting_merge`，`advance_ready` 为 false；PR Ready 只表示具备审查条件，不表示已经合并。合并仍需独立明确授权。

预检不写任务、处置或完成证据。`advance` 和未完成任务的 `release` 使用相同完成判定，并在工位锁内重读当前任务及源码后核验；此前的成功预检不能作为放行令牌。完成写入时才记录最终处置，已有完成凭证的恢复与释放继续核验冻结候选，不重复要求已撤销的实施授权。

## 同周期方案返工

发现 Draft PR 的方向或验收方案需要调整时，先保留当前源码和 PR。准备单个 JSON 输入，包含 `reason`、`facts`、`repositories`、`additions`、`impact`：facts 只更新项目声明的方案字段；repositories 按已登记仓库列出 scope 数组和 verification；additions 另需 ref_name 与 commit_sha，只能选择 Profile 的可选仓。impact 包含受影响 repositories、质量 items 引用和 rationale，必须覆盖受影响仓库的已登记检查项。

```sh
python3 <agenticops-root>/workflow/task.py replan prepare --dir <station> --issue-key <key> --expected-run-id <run> --input <request.json>
python3 <agenticops-root>/workflow/task.py replan apply --dir <station> --issue-key <key> --expected-run-id <run> --expected-revision <prepare-revision> --operation-id <stable-operation-id> --input <saved-prepare-output.json> --decision-ref <confirmed-decision-reference>
python3 <agenticops-root>/workflow/task.py replan abort --dir <station> --issue-key <key> --expected-run-id <run> --operation-id <same-operation-id> --decision-ref <abort-decision-reference>
```

prepare 只读；Agent 用原生文件工具保存完整输出，展示变更范围、验收影响及新仓来源后取得研发决定。apply 使用完整准备输出，不只传 digest；输入、revision、源码或远端漂移时拒绝。网络失败保留原 operation 恢复，不换 run、不清理重接管。abort 只适用于 current 尚未写入的新方案，写入后应恢复 apply 完成回执。

完成后按原入口 source-readiness、quality status/apply、authorization grant 和 advance 恢复实施。本次研发决定已覆盖的新方案及验收事实使用同一真实确认引用，不重复提问；变化的质量项必须按新方案处理。未变项不重跑，旧执行记录不换版本标签。新仓分支与当前 run 一致，历史分支名中的旧后缀不用于判断当前运行归属。
