# 任务接管与授权

本文现役命令继续按当前实现使用。[任务生命周期目标合同](../architecture/single-task-station.md#任务生命周期目标合同)定义完整目标；持久归档与[执行接管](../architecture/single-task-station.md#执行接管当前源码)已进入当前源码，两个清理操作仍待实现，不能当作旧安装已有能力。源码保留仍按工位合同区分当前实现；授权期限见[实施授权](task-authorization.md#实施授权)，旧安装不会因文档变化而更新。数据职责统一见[数据生命周期开发规范](../development-data-lifecycle.md)。

生命周期边界以[工位合同](../architecture/single-task-station.md)为准。清理脚本自动消费工位及项目清单，归档只记录结果，不要求完整会话或源码导出。

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

新分支重做必须先 release/clean 结束旧处理，保留源码和 refs，再接新 run。不能只换 operation 或修改基线绕过现有占用；旧 PR 不自动关闭，旧验证不借给新候选。

## 从 Jira 任务接续

本节说明业务任务接续；AgenticOps 源码维护任务使用[维护接续指引](../maintenance-guide.md#从-jira-接续维护任务)，不进入业务工位接管流程。

用户要求继续某个 Jira 业务任务时，使用共享 [ao-task-resume](../../skills/shared/ao-task-resume/SKILL.md)。入口只需要 Jira key；旧 run、会话或档案用于查缺，不作为必填恢复参数。先核验工位绑定及 current/op：兼容且原 run 可用时沿原操作恢复；旧现场不兼容时先由原版本退出，不用目标版本读取旧状态；空闲工位则依据外部成果建立新 run。用户已选择升级后继续的，不重复询问是否放弃业务任务。

### 持续记录与升级前核对

在方案确认、代码交付、测试总结和暂停节点，复用已有 Jira 字段、评论及 PR 描述更新下列事实，不新增固定表单或每次工具调用后的回写：

| 材料 | 记录与核验 |
|---|---|
| 目标、设计、决定、验收 | Jira 现有正文/字段；项目有 Issue Analysis 等字段时优先使用，评论记录变化、决定来源和未决项，不覆盖原需求 |
| 仓库与代码 | Jira 引用 GitHub 分支/PR，记录仓库、目标分支、真实历史基线、完整 Head 和分开交付的提交；原生回读实际对象 |
| 验证 | 保留原执行 SHA、方法、结果、用例计数、报告及未覆盖范围；GitHub CI 和 Jira 总结互相引用，本地报告明确位置和保全方式 |
| 环境与剩余工作 | 已选架构、配置/秘密引用、构建启动依据、实际资源、缺项、已完成和待做事项；不记录秘密明文或将端口存活当验收 |

阶段评论可派生本地分支、历史基线和版本，但不证明已经推送。升级前核对本地未推送提交、未提交修改、未跟踪文件及报告，按有效权限提交/推送或保全并记录位置。GitHub 缺权限、Jira 同步失败或未知结果时明确尚未持久化的成果；先核对未知写入，再决定是否具备安全退出路径，不能声称仅凭 Jira key 可以恢复尚未保存的字节。临时导出旧环境只用于首次补齐，目标版本不保留旧 schema 解析器。

### 读取、补全与重新接管

读取 Jira 当前正文、相关评论、关联测试和链接，沿来源读取 GitHub 分支、PR、提交及 CI；评论多页时读取到覆盖当前设计和最后进展，后续有效决定取代旧决定但不删除历史。核验负责人、任务准入、仓库身份、目标、历史基线、当前 Head、设计范围及未决外部结果。不要把 API 摘要中的评论数量当评论正文，也不要把最新目标分支与任务 Head 的 merge-base 自动当历史基线。

材料缺失时先利用源码、Git 历史、PR 差异、CI 或可访问旧会话核实；不能确定的只问关键缺项，区分事实、推测和用户新决定，继续无依赖准备。缺旧会话不阻止记录充分的任务；缺报告仍是未验证，缺历史基线不猜 SHA。修复后的信息回写同一 Jira，外部写入复用原生意图/回读原则，不建设专用同步队列。

将核验成果转成当前 CLI 输入：`takeover --continuation-input` 使用仓库 ID 对应的 `work_branch/baseline_sha/expected_head`，随后 `repository add --expected-head` 核验并使用保留分支。重新记录本版任务事实、方案与质量材料，复用仍适用的用户设计决定并绑定新授权；不复制旧质量日志、不跳阶段。需要的环境依赖按项目 Runbook 补齐并执行，验收只使用实际执行版本。新 run 的 Jira 总结引用原事实来源，说明本次复用成果、补齐内容及剩余工作。

## 实施授权

在已冻结源码上形成方案，使用 Q1/Q2 将根因或功能目标、修改范围、风险、回滚、验收项/预期/执行方式和允许后续动作一次性明确确认。未经确认不签发实施授权。已有用户决定明确覆盖当前方案及后续动作时，复用同一真实确认来源；调整后展示完整有效方案和变化，只补问未决内容或超出原授权的范围，不因整理材料再次索取相同决定。之后按 authorization.py grant 的现役参数绑定 issue/run、Agent 和方案版本；所有授权写入须携带 expected-run-id。

范围或方案改变使旧确认失效；必要时 revoke，重新确认和签发，不手改授权文件。改变完整工程版本则先结束当前处理再接管。归档不冻结开发；release/clean 后须建立新 run 才能继续。

默认 `grant` 不设置固定到期时间；用户明确限定期限时才传 `--ttl-hours <小时>`。撤销、退出、run/范围/方案绑定变化仍按原规则处理。新默认值不删除已有授权的显式期限；无固定期限不等于允许无限扩大动作，也不控制 Agent Token 预算。执行接管的 8 小时闲置判断是独立目标，尚未由此实现。

### 方案未变时显式续签

对旧授权或用户明确限定期限的授权，审查或 CI 等待导致到期时，不必仅因此结束当前任务。先用 `authorization.py show` 查看原确认，并用 `show --digest` 取得原授权摘要；向用户展示当前 run、方案、仓库范围和新的有效期，取得明确决定及可回查来源后，使用此前固定的摘要执行：

```sh
python3 "$agenticops_root/workflow/authorization.py" renew \
  --issue-key "$task_key" --expected-run-id "$task_run" \
  --expected-authorization-digest "$confirmed_authorization_digest" \
  --confirmed-by "$decision_maker" --confirmation-ref "$confirmation_source" \
  --ttl-hours 8 --dir "$project_station"
```

无固定期限的授权无需 `renew`；普通继续与 `reconfirm` 保持无固定期限。若需要重新限定期限，明确撤销并按当前方案重新签发，不把有限续签附加到无期限记录上。

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

原授权同时到期时，已有明确决定覆盖当前验收方案及续签的，可复用该真实确认来源，在上述 `reconfirm` 请求增加 `--renew-ttl-hours <小时数>`。该显式参数只允许忽略原有效期已过，仍核验原授权摘要、当前已确认的 Q2、run、实施计划、仓库及 Project endpoint；Q1 默认仍必须一致。新有效期必须为有限的未来时间且晚于原有效期。重新绑定与续期在同一工位锁内一次原子写入，在同一 `reconfirmations` 条目保留旧、新有效期；任何检查失败不写入。未提供参数仍拒绝过期授权，普通 `renew` 仍拒绝 Q2 变化。不得通过先改状态或自动重放完成恢复。

项目配置更正导致 Q1 摘要变化时，先核对接管事实并通过质量 CLI 重新确认 Q1，再显式提供 `--expected-q1-digest <已确认的当前Q1摘要>`。该参数只允许绑定已经有效确认的目标 Q1，并追加旧、新 Q1 摘要；未提供时仍要求 Q1 不变。实施计划事实与完整仓库绑定仍必须一致，不能用配置修复扩大范围。有效事实和用户决定可复用，执行证据不得改写。

该入口不扩大实施授权。新增可选历史字段兼容既有工位 epoch；不迁移旧任务，旧授权与仅续签的回归继续保留。确认来源只记录可回查决定，不提供身份认证。

## 相关文档

- [首次使用指引](../usage-guide.md)：安装与初始化项目工位。
- [权限与安全边界](../security/permissions.md)：凭证、服务器保护与 Workflow 检查点的边界。
- [v1 工程架构](../architecture/agenticops-v1-architecture.md)：单工位、多仓库和四操作模型。

## PR 前同步检出来源

功能与缺陷在每轮实现或返工完成、正式验证开始前同步检出来源，而非每次 PR 写入前同步。回读当前任务登记的仓库、work_branch、target_branch（兼容视图中的 base_branch）和冻结 base_sha；保留任务独立提交，不能以最新来源替换原始基线。Agent 原生查询已登记 origin 的目标分支最新完整 SHA，固定为本轮来源 T，并保存查询来源与时间；本地 remote-tracking ref 或历史缓存不能冒称最新远端事实。

同一轮按“实现或返工完成 → 同步 T 并处理冲突及行为影响 → 首轮验证与候选提交 H → 按需推送或更新 Draft PR → 完成人工验收及 CI → 更新并回读最终提交总结 → PR Ready”推进。候选提交仍遵守现有授权和证据要求，首轮工作区验证不能替代完整 SHA 的最终验收。Draft PR 可用于异地人工验收和触发 CI，不要求所有验收完成后才能推送。总结与结果的对应关系见[PR 正文发布与回读](quality-checkpoints.md#pr-正文发布与回读)。

需要同步时，展示具体仓库、工作分支、来源分支及 SHA，确认本次 Merge 已被明确授权后才原生 fetch/merge。来源同步 Merge 不属于普通 task_execution 授权；具体授权可以在方案确认时单独明确，也可以在同步前取得，仍有效且覆盖当前对象与来源 SHA 的决定不重复询问。PR 合入、发布、Tag、保护分支写入、强推和历史改写始终需要新的明确授权，来源同步决定不能覆盖这些动作。同一工作分支上的编辑、Merge 和最终验证串行执行。工作分支已包含 T 时不制造多余合并提交，核对已有来源材料是否仍适用；来源、候选及绑定未变时复用有效材料，不仅为刷新观察时间而重写质量记录或重新确认。

合并完成后调用只读检查：

```sh
python3 <agenticops-root>/workflow/source_sync.py \
  --repo <source-repository> --work-branch <registered-work-branch> \
  --base-revision <frozen-base-sha> --source-revision <just-queried-source-sha>
```

工具核对工作分支、干净状态及祖先关系；它不查询远端、不授予 Merge 权限、不更新冻结基线，也不证明行为正确。将输出与原生远端查询和 Merge 记录一并保存为当前 run 证据。来源不明、历史改写、文本冲突或结果不明时保留当前工作树，先解决或由研发决定从最新基线重建，不能自动丢弃工作或重置任务。验证期间目标分支正常推进不取消在途验证；推送同一候选、修改 PR 总结或转 Ready 不单独触发再次同步，也不要求提审时追平最新远端 SHA。结果只证明本轮 T 与候选 H 的组合，不宣称远端仍停在 T。提审时已知的后续推进应披露；已发现不兼容或验收结论不再适用时，先处理并更新验证，不能仅作提示后继续。合并时出现冲突可退回后按[PR 审查返工](quality-checkpoints.md#pr-审查返工)开始下一轮，不在预检内循环追赶远端。

### 同步后的分析与重验

同步前保存任务 Head；同步后给上述命令增加 `--before-merge-revision <同步前任务完整SHA>`。输出保留三组范围和差异指纹：原基线到同步前任务、原基线到新来源、新来源到最终任务。任务原提交未保留时拒绝，不能把重建或丢弃后的代码当作普通 Merge。

Agent 原生读取这三组差异，核对原任务目的是否仍满足、来源更新是否改变调用关系/配置/预期，并按最终代码重新确定模块及上层 Jar 消费范围。不能只检查冲突文件或两组路径交集：不同文件的 API、配置和依赖同样可能产生行为交互。工具总是要求行为分析，不自动判断语义等价。

补充必要用例后，执行最终范围的本地验证并回读对应 PR CI；关联当前源码、用例、依赖 Jar 和报告。若 Merge 或后续处理改变版本，旧执行按原版本保留，不能改写为当前结果；来源已包含且代码、用例及依赖全部未变时可引用仍有效的原执行并说明核对依据。失败按同一问题的累计修复记录续办；研发引导处理后重做受影响环节，不默认清空任务或从头开始。

归档材料与原生 Jira 回读操作统一见[持久接续点操作](task-archive-and-resume.md#持久接续点操作)。归档不是自动提交或推送授权；缺少远端代码时先按有效授权保全，或记录研发明确放弃的仓库。

## 清理与退出

用户已明确请求时，直接调用清理脚本；不要求先由 AI 分析、手写精确清单或准备会话输入。环境配置默认保留，源码、索引和工作区修改不被清理覆盖。需要预览时使用 --check，但它不是执行前置。

```sh
python3 <product-root>/workflow/station-clean.py --dir <station> --execute --action work-clean
python3 <product-root>/workflow/station-clean.py --dir <station> --execute --action clean
python3 <product-root>/workflow/station-clean.py --dir <station> --execute --action archive --handoff-input <Agent整理的接续材料.json>
python3 <product-root>/workflow/station-clean.py --dir <station> --execute --action release
```

work-clean 保留任务，clean 结束本次本地运行；release 尝试归档后调用相同清理能力，支持任务中途退出及完成退出。归档写入失败按工位合同保留待决策快照，由 Agent 引导用户授权重试或明确放弃；不重新检查业务完成、不修改 Jira 或默认关闭 PR。配置清理增加 `--clear-config config/<相对对象>`，可重复，未选择配置保留。工位注销使用 `agenticops station purge`，有活动任务时先执行 release。

脚本报告已完成对象、保留项、残留、异常及实际释放结果。有残留或异常时 Agent 接手原范围未完成事项，用户可选择保留、移出、删除或暂停。不得循环强删新内容或重复外部未知调用。

归档失败结果包含 `digest`、`checkpoint` 和失败路径。先向用户说明未保存范围及实际清理进度，回读确认结果；取得明确决定后恢复原操作：

```sh
python3 <product-root>/workflow/station-clean.py --dir <station> --resume --operation-id <原操作> --archive-decision retry --expected-archive-digest <失败快照摘要>
python3 <product-root>/workflow/station-clean.py --dir <station> --resume --operation-id <原操作> --archive-decision discard --expected-archive-digest <失败快照摘要>
```

`retry` 复用冻结材料；`discard` 只放弃该快照，不能删除已保存档案。清理后结果快照有独立摘要，之前的放弃决定不覆盖它。身份冲突、结果无法回读及本地操作日志不可写时暂停相关步骤，不能用授权伪造成功。

### 源码保全预检

当前源码在清理预检中提供可选的 `--source-preservation`，用于准备源码处置决定；旧安装须先核实帮助是否支持。它只读取本地 Git 与项目声明的远端，不推送、不删除、不刷新执行占用，也不改变现役清理保护源码的行为：

```sh
python3 <product-root>/workflow/station-clean.py --dir <station> --check --source-preservation --details
```

输出列出未匹配远端同名引用的分支、stash 等本地引用，暂存与未暂存修改，未跟踪及忽略的唯一文件，仓库外未知文件。被 assume-unchanged 或 skip-worktree 标记的文件单列为待核实，不以 Git 未报告修改证明可丢弃。可再生文件沿既有项目清单识别；只展示路径，不读取或输出文件正文。远端核验失败或未识别的仓库标为未核实，不能用本地 tracking refs 代替远端回读；关联外部工作树或 Git 元数据时保留并提示先处理共享关系。远端分支已前进也会因 SHA 不同需要 Agent 进一步核实，预检不自动推断祖先成果可丢弃。当前任务的受管基线分支若与冻结基线及远端提交一致，不要求推送其本地分支名。reflog 与悬空对象不是持久保存点，需保留的成果应先形成明确引用。

`requires_decision` 表示当前发现了需处理事项，不是删除许可或保全成功凭证。预检期间应协调停写，结果不能当作之后删除的有效授权；两个目标清理操作仍待实现。

### 原清理操作的恢复

脚本异常后用 `--resume --operation-id <原操作>` 恢复。若 Agent 已通过原生工具完成动作，增加 `--verify-result` 按实际结果收尾，不重跑删除。`--prepare-only` 只固定范围后交接。已完成对象再次产生内容时保留；未提交源码及配置保留不作为退出门禁；归档待决策时先处理指定快照。权限、身份或实际绑定写入失败时如实报告未完成。

## 完成预检与等待合并

`task.py next` 在 `ci_validation` 阶段同时核对源码洁净、登记工作分支、最终候选的合并 PR 事实、质量验收、CI 和有效方案授权。多个仓库缺失合并事实时一次列出 `awaiting_merge`，`advance_ready` 为 false；PR Ready 表示所有目标 PR 已推进 GitHub Ready for review 并回读确认，不表示已经合并。合并仍需独立明确授权。

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
