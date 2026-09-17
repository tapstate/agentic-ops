# 任务接管与授权

本页说明单工位脚本操作。实际 Jira 事实须由已配置原生工具读取，本地 takeover 不替代 Jira 准入。完整四操作、退出与精确清单输入见[项目任务 Skill](../../projects/tapdata/skills/tapdata-task/SKILL.md)；质量输入见[质量检查与证据](quality-checkpoints.md)。

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

`python3 <agenticops-root>/workflow/station-clean.py --dir <station>` 只读展示当前任务与版本 4 清理计划；未完成任务返回放弃变更问题，不在脚本内部猜测确认或阻塞等待终端输入。预检阻塞仍返回任务与问题，但不提供可执行计划。新清理尚未开始时 `--abandon-changes no` 无副作用退出；已有未完成清理操作时返回恢复提示，不声称撤销此前动作。工位空闲且没有未完成操作时不删除材料。

中央 `policies/station-clean.json` 必须存在，项目 `projects/<project>/station-clean.json` 可省略。两份配置均为 `{"version":1,"preserve":[".idea/"],"clean":[{"pattern":"scratch/","action":"remove"}]}` 的结构，独立读取，按中央白、项目白、中央黑、项目黑顺序判定；全部未匹配时保留并报告，不配置 `other` 或 `unmatched_policy`。模式仅匹配工位根名称，支持 `*`、`?` 和目录后缀 `/`，不支持前导 `/`、深层路径、`**`、否定或方括号语法。源代码子树由 Git 阶段检查，不由名单递归匹配。

黑名单动作限定为 `source-reset`、`clear-children`、`lifecycle-clean` 和 `remove`；前三项分别只适用于 source、runtime、.agenticops。中央默认保留 config、archive、.idea，项目不能通过白名单跳过核心生命周期。初始化接线仍按原 manifest 核验。`remove` 只用于任务独占的普通根目录，生产前使用现有 `station_resources.py` 登记 `kind=directory`、根名称和 producer；Workflow 创建并登记身份。名单不能认领已有非空目录、初始化接线或任意文件，已有空目录采用仍须 `adopt_empty=true`。需要删除其它对象时停止并明确处置，不把规则匹配当成删除授权。

确认请求放在工位外，包含现有 summary、reason、decision_ref、confirmed_digest，另加 `cleanup_version:4`。未完成任务请求增加 `abandon_changes:true`，并使用下列命令明确传递用户的放弃决定；完成任务不需要放弃参数，但仍须现役 terminal proof 和 candidate_digest。归档保留成果，放弃不删除 Git 分支或提交。

```sh
python3 <agenticops-root>/workflow/station-clean.py --dir <station> --issue-key <issue> --expected-run-id <run> --expected-revision <revision> --operation-id <op-id> --input <工位外请求.json> --abandon-changes yes
```

源码构建目录仍存在时预检停止，Agent 使用项目已有 Maven/npm/pnpm 命令清理，再重新预检；产品不执行这些命令，也不提供目录删除兜底。源目录被外部工具移除时保留原登记，计划观测实际缺失；父目录身份改变或确认后才缺失按现有恢复规则停止。

中断后沿用原 operation-id、原 expected-revision 和原请求恢复；不得改用当前 revision 或另建操作。`station-source-reset.py` 是同一版本 4 操作的独立 Git 阶段入口，要求已发布且覆盖当前成果的档案，以及当前 issue/run/revision/operation-id，不得单独用于未归档源码。正常清理由入口调用同一实现，无需手工追加执行一次。

工位根目录的 `.idea/` 属于 IntelliJ IDEA 配置，由中央白名单统一保留，不遍历、归档或删除其内容。目录模式不匹配同名文件，符号链接按通用安全规则拒绝。白名单可以覆盖同层宽泛黑名单；不同清理动作同时命中同层对象才是冲突。新持久计划与目录类型对应 epoch 5，升级边界见[更新与回退](update-and-rollback.md)。

未初始化子模块只在索引、当前提交与归位提交的 gitlink 路径和对象完全一致，且路径不存在或为空目录时保留。清理前和归位时均核验，不递归检出子模块；含内容、符号链接、gitlink 变更或冲突时仍停止。此边界不改变工位状态格式或 epoch，也不授权删除子模块内容。

### 原清理操作的恢复

已有 schema 3 操作保留原请求和执行顺序：保留 config、完整 source 和 archive，清空 runtime，按原 Project 配方回收已登记源码生成目录。不得将正在执行的旧计划改成 schema 4；新操作使用上面的配置化入口。边界、源码 archive/export/discard 与中断恢复以[工位合同](../architecture/single-task-station.md#8-一次确认成果归档与恢复)为准。

构建前将日志、插件、下载和可迁出的生成物定位到 runtime；不能迁出的生成目录先用 station_resources.py 创建：输入数组项为 `{"kind":"directory","path":"source/tapdata/tapdata/target","producer":"maven"}`，已有空目录的显式采用另加 `"adopt_empty":true`。目录非空时不能补登记猜归属；源码生成路径必须符合工程 Profile 的 generated_directories。

```sh
python3 <agenticops-root>/workflow/task.py cleanup-preflight --issue-key <issue> --expected-run-id <run> --dir <station>
python3 <agenticops-root>/workflow/task.py clean --issue-key <issue> --expected-run-id <run> --expected-revision <revision> --operation-id <op-id> --input <工位外请求.json> --dir <station>
```

请求包含 summary、reason、真实 decision_ref 和 cleanup_plan.digest 对应的 confirmed_digest。默认源码归档，显式丢弃还需 discard_digest；完成任务改用 release 并提供 candidate_digest。Agent 先按原生能力停止写入者再执行，Workflow 检查并按同一操作归档及重置，无需归档后二次确认。归位结果为已确认开发 SHA 的 detached HEAD，分支未移动；下次接管联网刷新。

需要保留敏感或超过普通归档上限的源码时，先在工位外创建当前用户私有的导出目录（0700），再运行 `python3 <agenticops-root>/workflow/station_export.py --dir <station> --issue-key <issue> --expected-run-id <run> --path source/<owner>/<repo>/<file> --output <绝对导出文件路径>`。工具独占写出 0600 文件、重建核验并回读登记 export 决定；不覆盖已有不同内容，输出仅含路径和摘要。单成果原始内容上限 512 MiB，编码后上限 768 MiB。失败退出操作中的导出另带原 expected-operation-id，随后按新范围补充确认；正式归档后的导出意图和回执进入 archive/receipts。

分支和 PR 默认保留。用户选择删除/关闭时，本地重置完成后使用原生工具，另通过 `workflow/station_disposition.py --dir <station> --issue-key <issue> --run-id <原run> --input <处置.json>` 记录 intent/unknown/readback。该工具只记追加证据，不发送外部请求。意图需稳定 disposition_id、精确 object_id、resource_type、action、before 身份及保护回读、decision_ref，并以不含 confirmed_digest 的意图对象摘要确认；回执绑定 intent_digest 和 object_id，包含实际 readback_ref。disposition_id 是本次外部操作的稳定关联键，原生 API 支持幂等键时使用同值；API 不支持时不能把它当作服务端去重保证。重试已有意图只用于回读，不能据此重发。工位已有新任务时拒绝旧任务处置，避免删除被复用的对象。

关键日志与报告集中到 runtime/logs、runtime/reports（由 Project 的 archive_runtime 指定），归档保留脱敏后的 UTF-8 正文；停止期间新增内容以不可变附件追加后再删除。单文件超过 16 MiB、总量超过 64 MiB 或非文本材料须先安全导出并留下摘要，不能静默丢弃。

重置失败保持占用；恢复原操作和原请求。范围变化使用 cleanup-amend 绑定原计划摘要及修订号，源码新增成果须明确处理。epoch 4 及更早工位先由原版本归档、退出、purge，本版不在线接续旧代际操作。
