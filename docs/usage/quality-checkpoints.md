# 质量检查与证据

## 同步待办与有限回执恢复

TapData 的 `publication_mode=checkpoint` 启用接管摘要和逐检查点评论待办。接管成功、阶段推进、quality apply/status、task next 及 external_sync status 返回 `sync_actions`；它覆盖已确认但未发布的历史检查点，不只看下一阶段。查询不写状态，不代表已获得外部写入权限或同步已完成。接管摘要只说明准备事实，不冒充 Q1 通过；Q1—Q6 必须使用有效检查点的完整正文。

Agent 在当前授权内处理动作；缺少授权时先询问对应动作范围。评论按现有 draft/confirm/prepare_write、原生调用、receipt/readback 顺序处理。稳定 id 复用原记录；尚未发送的过期草稿重新生成确认，已经发送或结果不明的记录只先回读原目标和原正文。新确认不授权重复发送。`refresh_required` 只表示当前材料变化，不撤销原外部调用事实。

状态、水印和字段按各自入口核验，不自动改写任务描述或猜填审批字段。接管状态同步允许在 task_intake/design_review/implementation 补查；更晚阶段交接人工，不回退 Jira 状态。同步失败明确原因及后续动作，不阻止无依赖的本地工作；unknown 或无法证明未写入的 failed 先核清。安全退出判定不消费容错展示结果。

completed 后可通过原 quality receipt/readback、jira_status complete、jira_watermark complete 核对已有意图；不新建或重发。恢复窗口与限制见[工位合同](../architecture/single-task-station.md#外部同步回执恢复)：清理确认或归档证据已绑定时交维护侧，不改状态绕过。无原意图的字段仅作只读观察，不记录终态同步成功。水印回读原版本不等于当前产品版本已同步。

已知未发送事项在结束前列入总结与接力；归档后不自动跨 run 补写。`sync_diagnostics` 表示待办展示失败，应查询原操作；若返回 `local_write=committed`，质量记录已保存，不得重复 apply。兼容旧 `deferred-summary` 配置，但仅 `checkpoint` 主动生成接管及检查点动作。

## 源码定位与实时核验

同类实现证据保存仓库标识、仓库内相对路径与完整 Git SHA。源码位置由工位既有 `source/<owner>/<repo>` 规则即时解析，并核对当前任务登记、源码准备状态和冻结仓库身份；不接受质量事件提供的绝对路径，也不在新质量事件中保存 `source_path`。原始证据、用户正文和报告引用仍按项目规则扫描，不增加内部路径白名单。

历史重放仅恢复记录，不重新执行同类实现的 Git 查询；当前报告、方案确认、实施授权及相关阶段推进必须执行实时核验。核验能力或源码缺失不能解释为通过。临时定位与核验结果不进入业务确认摘要；方案、仓库或引用版本变化仍使相关确认失效。此边界只针对同类实现查询，不表示其它验证材料不再复核。

当前报告的方案检查点返回 `source_issues`：包含类别、引用序号、问题、责任人、补齐动作和受影响检查点。`evidence_gap` 要求研发与 Agent 修正或补齐依据；`tool_failure` 要求恢复工具后重试，无依赖工作继续。不回显本机路径或 Git 原始错误。Q2 核验失败阻止对应确认与实施授权，不阻止 Q1 合规记录和既有评论回执、回读；不新增强制通过入口。其它阶段仍按原配置检查实际输出。

发送准备按当前规则重新扫描已保存的完整正文，拒绝时不增加质量 revision、不生成发送意图。历史外部写入结果未知时先核对原操作，不因新扫描规则或方案核验故障盲目重发。归档继续执行原有脱敏。

旧版本不能重放无 `source_path` 的同类实现确认，新版读写语义使用 epoch 18。旧事件不改写，不承诺全部旧摘要不变，也不在线继承旧 run 或确认；按[更新与回退](update-and-rollback.md)先由原版本保存材料、核对未知外部结果、退出任务并显式 purge，再更新和初始化。该操作不由质量工具自动执行。

## PR 正文发布与回读

PR 正文使用 UTF-8 Markdown 文件和真实换行，通过文件编辑能力生成，不把完整正文拼入 Shell 命令。CLI 创建和更新使用 `gh pr create --repo <owner/repo> ... --body-file <正文文件>` 或 `gh pr edit <编号> --repo <owner/repo> --body-file <正文文件>`；API/MCP 直接传递真实多行字符串，由客户端序列化一次。不要用 `echo -e`、`eval` 或全局反转义转换正文；双引号中的美元符号、反引号及命令替换可能被 Shell 解释，字面 `\n` 也不等于真实换行。

每仓独立准备正文，核对变更、验证结果和未执行项；文件放入当前任务已有证据位置，使用 `task.py interaction-path` 分配。不得将敏感日志或本机绝对路径写入对外正文。纯正文修正不改变源码，不触发来源分支 Merge、代码重验或重新创建 PR；原有代码交付和验收要求保持不变。

```sh
python3 <agenticops-root>/workflow/pr_body.py preflight --body-file <正文文件>
gh pr view <编号> --repo <owner/repo> --json number,url,body > <回读.json>
python3 <agenticops-root>/workflow/pr_body.py compare --body-file <正文文件> --readback <回读.json> --repository <owner/repo> --pr <编号>
```

先预检再由 Agent 在已有授权内原生发布；发布后重新取得包含 `body` 的当前 JSON 并比对。显式检查原生回读的退出状态，不使用失败调用、旧文件或人工拼造的快照证明成功。API/MCP 回读整理成同一对象 `{number, url, body}` 时保留原始来源，不对 body 二次解码。企业 GitHub 通过 `--host <主机名>` 指定目标站点。正文匹配仅证明传输完整，不证明 Markdown 渲染效果、技术内容或验收结论正确，发布前仍需审阅正文。

工具仅本地读取指定文件，不联网、不写状态、不发送或修正正文。返回 0 表示预检有效或正文匹配，3 表示正文或目标不匹配，4 表示输入不可读取或格式无效。空正文、非法 UTF-8、NUL、孤立 CR、BOM 不通过；疑似用字面 `\n` 拼接标题和列表只返回 warning 及行列，合法代码示例可以保留，不能自动替换。比较只规范化 CRLF 为 LF，不裁剪空白或末尾换行，不解释反斜杠转义；JSON 表示的真实换行与正文中的字面转义分别处理。输出只含诊断与摘要，不回显正文。仓库、主机及 PR 编号必须与回读 URL/number 一致。

编辑已有 PR 前保留最新原文，与拟修改正文比较，保留其他人的更改；写前再次回读确认原文未变，变化时重新整理，不能覆盖。普通 CLI 编辑不提供原子条件更新保证，短窗口内并发仍可能发生；已知有同时编辑者时先协调，写后核对并披露冲突，不声称消除了并发风险。

正文回读不匹配时，只暂停相关描述发布/修正及“描述已核验”的结论，不阻塞无依赖编码、测试或 CI 观察；PR 存在、正文匹配、代码与 CI 通过分别报告。调用超时或结果未知先定位原 PR 并回读，不重建 PR 或盲目重发。它不是 PR Ready 新门禁，不增加工位字段、事件或 epoch，也不认证快照来源；旧状态读取和原门禁保持不变。

## 共同验证材料

使用现有 `quality.py apply --issue-key <key> --expected-run-id <run> --expected-revision <当前revision> --input <json> --dir <station>`，输入为 `{"action":"verification","payload":{...}}`。每个仓库分别提交材料，内部自动绑定源码版本与仓库登记；不是新任务类型或执行引擎。新 source_sync 仅绑定所属仓，local/ci/review 仍绑定全部任务仓。写前先 `quality.py status`，source_sync 还会实际读取已准备的任务工作树并核对包含关系。原生报告的真实性、依赖清单和语义分析仍由 Agent 核对，工具不认证来源或判断断言含义。

所有材料提供 kind、repository、target_revision（当前完整 SHA 或首轮本地源码指纹）、source_ref。其余内容如下：

| kind | 材料 |
|---|---|
| local | analysis_ref（变更/用例缺口判断）、case_review_ref、case_version、dependency_analysis_ref；required_scope 列出全部应测模块及场景，results 对每项给出 scope、result、report_ref |
| ci | local 的相同范围及报告字段，加 run_ref、attempt 正整数、head_revision（本次 PR Head）、checkout_ref（实际测试源码及该 Head 的对应依据） |
| source_sync | source_branch（登记的检出来源）、source_revision（刚回读的来源 SHA）、before_merge_revision、observed_at、impact_analysis_ref；sync 由工具实际生成，用户输入不能代替 Git 核对 |
| review | complete=true 表示已完整分页回读；items 逐项记录 id、source_ref、reason、status（fixed/not_applicable/accepted_gap/pending），fixed 必须有 verification_ref；无意见使用空列表并保留回读来源 |

results 中 result 使用 PASS/FAIL/UNKNOWN/NOT_RUN/SKIPPED。PASS 另需报告中的 tests、failures、errors、skipped 非负整数，tests 必须大于零，其余必须为零。非 PASS 保留原结果；若研发明确接受该项缺口，提供 decision，包含 reason、uncovered（精确等于该 scope）、follow_up、proof（actor/source=user_message/reference/at）。不能用一项决定覆盖其它范围。review 的 accepted_gap 也提供这组明确处置。

跨仓 Jar 材料使用 jars 列表，每项包含 built_sha256、consumed_sha256（必须相同）和 loaded_from（实际加载证据）。local 另给 built_path、consumed_path 绝对路径，写入及检查点复核内容哈希；这两个路径仅保存为本地恢复元数据，不进入 Jira 摘要，其它材料仍按项目规则扫描。没有 Jar 时省略列表，但 dependency_analysis_ref 仍须说明分析依据。CI 使用运行内文件与加载证据，不冒充本地文件核验。报告引用、版本及统计从实际执行取得，不能为满足字段填造数字。

TapData 功能和缺陷使用相同配置：Q3、Q4 要求 local/source_sync，Q5、Q6 再要求 ci/review；PR Ready 要求 local/source_sync/ci，同时保留现有 PR Checks 和人工验收规则。local/ci/review 在任一任务仓代码或登记变化时失效；CI 重新观察后需重新核对并登记 CI 材料。本地 Jar 变化也失效。遗漏范围、未处理失败或待处理审查意见阻止对应检查点，人工接受的缺口保留原结果。

新 source_sync 的 `binding_version=2` 由工具生成，禁止调用者指定。仅所属仓的源码版本、repository/base_branch/work_branch/base_sha/catalog_digest/approved_scope/verification_method 变化使它失效；无关仓变化不使该来源事实失效，新增仓仍需自己的材料。旧事件缺少版本时继续全仓绑定，未知版本拒绝，不复活历史确认或授权。记录只证明 observed_at 时核对的 source_revision 包含关系，PR 前仍需刷新来源事实，不保证远端以后不再变化。该语义改变须按[更新与回退](update-and-rollback.md)跨 epoch 退出重建，不在线迁移。

源码同步后已解决问题只需无修改重验时，`failures.py` 使用 revalidate，参数同 finish，不消耗修复轮数；失败则回到 unresolved，后续修改仍须 start 记账。每次重新验证都保留事件，不能改写历史。旧 `ci.py record-fix` 已移除，CI 观察不再维护另一套预算。

研发已手工处理未解决问题时，使用 manual_result，提供 finish 的实际结果/版本/报告字段，并附 reason 和真实用户消息 proof；不消耗自动修复轮数。仍有 running 轮次时先 finish 保留当时实际结果，再记录人工处理。没有明确人工来源或实际验证，不能声明恢复成功。

当前工位 epoch 以机器契约为准；材料只属于当前 run。旧状态由原版清理，新版本不迁移历史确认。生成/清理与跨版编排的顺序见[更新与回退](update-and-rollback.md)。发布、清理和业务验收分别授权。

## PR 审查返工

Agent 原生回读当前 PR Head、审查结论、普通评论和行内审查线程，读取完整分页；只读 PR 总体状态或只看最新一条评论不算意见收集完整。每条必要意见保留原始链接/ID、审查所针对提交、处理结论（已修复/说明不采纳/待研发决定）、理由、对应修改和验证。过时线程仍核对问题是否在当前代码存在，不能仅因 outdated 或线程已关闭就判为已解决。

在已确认预期和授权范围内直接修复、补用例并重验；不同意意见时给出代码/预期依据。验收含义改变、范围扩大或无法确定时交研发决策。只有具备明确外部评论授权时才回复 PR；本地可以先准备回复及证据，不擅自替审查人批准、关闭讨论或撤销请求修改。

审查指出与已有失败相同的问题时，使用 `failures.py` 的原 problem_id 和 stage=review 累计轮次，不新建预算。修改结束后重新核对检出来源、最终差异、本地测试及当前 PR CI；此前提交的批准或测试结果不能证明新提交仍满足要求。推送仍受当前任务授权限制。

Q5 前回读所有必要意见处理结论、当前 PR Head 的审查状态和 CI 报告。未决意见与研发接受的具体缺口分别记录，不能用一条总体“已处理”代替。缺少审查事实时保留未知，不能直接将 Q5/Q6 推进当作交付完成。研发处理后从当前环节继续，保留原提交、意见及验证记录；Jira 测试/处理总结引用对应意见和报告。

## 失败归因与有限修复

Agent 用原生工具分析失败。只有证据指向当前变更才自行修复；已有失败、环境问题及归因不明由研发决定。使用 `workflow/failures.py status --issue-key <key> --dir <station>` 回读当前 run 的失败记录，随后以 `apply --expected-run-id <run> --revision <读取的 revision> --input <事件.json>` 记录操作（同样携带 issue-key、dir）。输入保存在当前 run 的受控交互路径。

| action | 必需内容与含义 |
|---|---|
| observe | repository、稳定 check_id、label、attribution（current_change/preexisting/environment/unknown）、evidence；创建或更新问题，返回 problem_id |
| start | problem_id、stage（local/ci/review）；修改前占用一轮，包含本轮分析、修改、重验 |
| finish | problem_id、result（PASS/FAIL/UNKNOWN/NOT_RUN/SKIPPED）、source_revision、evidence；使用实际版本及报告，未执行如实说明 |
| decide | problem_id、decision、reason、proof；continue 另给 additional_rounds 正整数，accept_gap 另给 uncovered 与 follow_up |

`proof` 使用已有用户消息来源字段 actor、source=user_message、reference 和带时区 at，引用研发真实决定，不是身份认证。人工追加明确限定从当前累计次数起可再试几轮；接受缺口保留 accepted_gap，不写 PASS。环境问题由研发授权 Agent 处理时，也明确续修轮数。

同一问题以首次关联的 repository/check_id 定位，label 改名不改变身份；本地、PR CI、审查和恢复共用累计三轮，不因成功后再次出现而清零。Agent 必须将相同根因的后续失败关联原检查项，不能通过新建 check_id 绕开次数；工具不猜测语义上是否为同一根因。源文件或用例改名时仍使用原 check_id。中断中的轮次先以真实结果（包括 UNKNOWN/NOT_RUN）finish，不重开不记账；事实或范围变化则重新 observe 并核对人工决定是否仍适用。

记录按任务 run 隔离、带 revision 并持锁更新；重放保留归因、每轮来源及结果、决定和报告。新增失败文件不改写已有任务或 CI 文件；接入阶段检查点前，记录本身不代表流程已经强制检查全部失败，也不代替 PR Checks 或 Q4 验收。

质量检查采用“必须核对、用户决定处置”的方式。Agent 提议验收用例及验证方式，用户选择；测试工具提供执行结果，用户决定是否验收、补测、不适用、延期或接受风险。接受风险不会把失败或未执行改为通过。

本文说明稳定操作方式。项目标准来自 `projects/<project>/quality.json` 和 `admission.json`，输入及恢复契约来自 `contracts/quality-action.schema.json`、`quality-state.schema.json`。工作项与最终证据仍在 Jira；本地记录只是任务 run 的执行及恢复材料。[任务授权](task-authorization.md)和[安全边界](../security/permissions.md)独立生效。

Agent 为 Jira/MCP 调用和质量动作准备的 JSON 输入、草稿、回执、回读或日志，先通过下列命令取得当前 run 的受控路径，不要直接写入 `.agenticops/` 根目录：

```sh
interaction_file="$(python3 <agenticops-root>/workflow/task.py interaction-path \
  --issue-key "$task_key" --expected-run-id "$task_run" \
  --name jira-snapshot.json --dir "$project_station")"
```

文件名使用 lowercase-kebab-case，可选扩展名 json/jsonl/log/md/txt。interaction-path 返回 `.agenticops/evidence/interactions/` 内路径，仅当前 run 可写；归档后停止开发，release/clean 收尾移除活动副本，档案保留必要脱敏材料。不用根目录文件名前缀模拟任务归属。

旧状态不在线迁移或猜测归属；退出当前任务后，明确 station purge 注销受管状态，再按新版本生成。未知残留阻止清理；需保留材料先导出核验。见[更新与回退](update-and-rollback.md)。

## 任务类型与质量配置

任务类型可在 Project 的 `admission.json` 中声明 `quality_profile`，值为当前项目目录内的 JSON 文件名，例如 `quality-feature.json`。未声明时沿用 `quality.json`，保留现有缺陷规则及摘要算法。显式声明的文件缺失、配置无效或未启用当前任务类型时，相关质量检查拒绝推进，不回退到缺陷规则。质量配置选择统一用于授权、阶段、证据、PR Ready 及同步告警。

专用配置复用现有质量合同，通过 `task_classes` 声明适用类型，通过 `intake_fact_keys` 和 `plan_fact_keys` 声明参与确认的事实。实施前必须包含人工决定的 `q1-intake` 与 `selection_checkpoint`。未使用既有 `structured_fix_plan` 检查时，必须以 `plan_contract` 声明方案对象的 `fact_key` 和非空 `required_fields` 列表；该事实同时列入 `plan_fact_keys` 并登记为准入配置中的已知事实。`task.py record --key <方案事实> --input <JSON文件>` 可以记录此对象。这里只检查必要内容存在和确认有效性，方案合理性及测试预期仍由 Agent 与研发核对。

任务专用方案摘要绑定任务类型和配置指定的方案事实；授权签发、续签及推进使用同一选择。已确认的方案、验收或规则实质变化后，重新核对相关确认；未参与确认的展示备注不影响方案。没有启用质量检查的任务不能签发方案授权，不能通过空配置代替检查。增加配置支持不等于功能任务已完成 Jira 或真实测试接入。

本次能力扩展不增加本地状态字段、路径或事件格式，`station_state_epoch` 保持 1。未启用专用配置的旧缺陷任务继续使用原规则及相同摘要；旧事件按记录的规则重放。给已有任务切换质量配置属于规则变化，旧确认不会自动迁移为新规则下的确认。专用配置须部署在支持此能力的产品版本；回退旧产品前结束或停用这些任务，不能将新能力下的任务当作已验证的旧版续办路径。

## 流程与检查点

TapData 功能开发使用 `quality-feature.json` 与 `implementation_plan`，Q2 绑定目标、实现变化、验收场景、风险、回滚及范围；不要求缺陷根因。CI 用例在功能任务内维护，不要求独立 Jira Test；实际关联的 Test 仍按当前用例版本与方式核对。Q4 必须有当前代码的验收证据，不能因为无独立测试任务而省略验证。下图中的修复方案和修复步骤，对功能分别对应实施方案与功能实现。

功能 Jira 协作遵循“先读事实、处理过程中补充、按阶段回填、流转后回读”，见[项目任务引导](../../projects/tapdata/skills/tapdata-task/SKILL.md#功能任务的-jira-协作)。Story 与缺陷复用 `jira_status.py prepare/complete`，项目 `status_sync.by_task_class` 分别配置流转和字段采集提示，避免套用缺陷规则。已知未发起转换的预检缺项可补齐后重检；已发起写入的 failed/unknown 先回读，不自动重放。现有状态格式不变，旧记录继续保留。配置支持不等于真实完整交付已验收。

```mermaid
flowchart TD
    A[接管 Jira：读取任务事实] --> Q1[Q1 盘点 Jira 事实、缺失信息]
    Q1 --> B[受控仓库准备与源码分析]
    B --> Q2[Q2 修复方案和验收计划：用户选择用例与方式]
    Q2 --> J2[确认方案及检查点评论回写并回读]
    J2 --> AUTH[核对任务级实施授权]
    AUTH --> DEV[实施修复及选定的首轮验证]
    DEV --> Q3[Q3 自动记录首轮事实]
    Q3 --> PR[有意义变更：Draft PR]
    PR --> CASES[用户与 Agent 创建/复用 Jira Test 并建立关联]
    PR --> Q4[Q4 验收关联用例：补充验证或用户处置风险]
    Q4 --> J4[验收处置评论回写并回读]
    J4 --> Q5[Q5 核对审查、CI、目标提交及合入事实]
    Q5 --> Q6[Q6 交付证据核对：未完成项明确说明]
    Q6 --> J[评论草稿 → 用户确认 → 写入意图 → 外部发送 → 回读]
    Q2 -.有缺口.-> D[补测或返工 / 调整方案 / 不适用 / 延期 / 接受风险]
    Q3 -.有缺口.-> D
    Q4 -.有缺口.-> D
    D --> R[记录事实、理由、决定者、责任人及后续动作]
```

| 检查点 | 核对内容 | 当前阶段强制点 |
|---|---|---|
| Q1 接管与盘点 | Jira 任务事实和缺失信息；接管不要求预先存在 Test | Q1、Q2 的用户处置在进入 `implementation` 前检查；缺普通信息可先分析，仓库基线与权限仍须可靠 |
| Q2 方案与验收 | 已验证事实、根因假设、缺失输入、修复范围、验收场景、每项预期和方式，以及 Test 复用/创建关联意图 | 只有无关键缺口且用户确认后才可签发并进入 `implementation`；Test 的定义和编写由用户与 Agent 处理 |
| Q3 首轮验证与 Draft PR | Q2 已选的全部修复后检查项在最终完整 SHA 上的首轮结果、可审阅变更和风险 | 自动事实检查点，不再重复要求用户接受首轮验证；任一结果不符合预期、证据不完整时停止相应验收；评论回读不明仅记警告 |
| Q4 关联用例验收 | 编码后 Jira「已链接工作项」中的 Test、Test Type、用例版本、当前代码、执行证据和用户逐项确认 | 进入 `ci_validation` 前；Manual/Unit 按执行证据验收，TapTest 按状态判定后可尝试 Tests Passed |
| Q5 审查及 CI | PR 仓库与 Head、检查结果、目标用例是否运行、审查或合入的回读事实 | Q5、Q6 在本地 `completed` 前 |
| Q6 交付证据 | 已完成和待完成事项、风险责任、Jira/发布事实及后续接力 | 本地完成仅代表本轮执行结束，不能据此声称 Jira Done 或已发布 |

首个有意义提交并完成第一轮针对性验证后建议创建 Draft PR。若验证受阻，披露现状并按 TapData 标准中首个有意义提交／一个工作日要求处理；不等待全量验证全绿。正式提审与 Jira `PR Submitted` 仍遵循 `Tests Passed` 等外部条件。

## 非阻断修复策略

规范化类型为 `defect_fix` 的任务在 Q1 只读显示当前修复策略，健康默认配置为“最小充分修复（默认，可在 Q2 前调整）”。默认路径不要求用户选择、确认、执行命令或填写 Jira 字段。Q2 前先读取 `task.py checklist --json` 或 `task.py next` 返回的 `repair_strategy`，用其中 `planning_guidance` 生成方案，并在现有 Q2 确认内容中说明策略如何影响修改范围；策略定义以 `policies/defect-repair-strategies.json` 为通用事实源，项目只可通过 `projects/<project>/planning.json` 覆盖默认选择。

修复策略只是 advisory 调优：配置缺失或损坏、任务覆盖失效、Agent 未记录应用情况或方案偏离都只形成 warning，不进入 `quality.py` problems、`task.py advance` blockers、Gate 或 Authorization 必填绑定。公司目录无法读取时停用本次调优并继续原缺陷流程，不在代码中复制另一份策略正文。功能、技术及未知类型任务不解析、不展示也不保存修复策略。

用户可在 Q2 确认前使用 `task.py repair-strategy list/show/set/clear` 查看或调整当前 run；`set/clear` 必须绑定 `--expected-run-id`。Q2 已确认后不能只改偏好而保留旧方案，需要让新策略作用于当前任务时，更新 `fix_plan` 并沿用现有重新规划、Q2 确认和授权流程。此时重新确认由实际方案变化触发，不是策略门禁。

以上是现有 `task.py advance` 的强制检查点。Q2 的 `fix_plan` 必须以 `structured-v1` JSON 记录：每个问题现象及来源、可回查证据、可证伪假设、未取得的关键输入、修改范围、风险、回滚以及每个验收项的 Test 关联意图。缺输入时只输出一次性材料清单，不得签发授权或将假设称为根因。`case_status=existing` 表示复用已回读的 Test，必须保留 Jira 来源；`case_status=proposed` 表示确认创建并关联的意图，必须提供步骤、预期、方式和责任人。Q4 才回读真实关联、版本和执行结果；计划创建的 Test 在创建后补入真实 key/version 不会推翻 Q2 的创建意图。Q3 只在全部已确认的修复后检查项满足证据合同（TapTest 按状态，其它方式按当前完整 SHA 的预期结果）时使用 `auto_checkpoint` 记录事实并回写 Jira。它不等同于用户验收，也不能在失败、跳过、未知、计划变化时推进。原生工具由平台权限处理；Workflow 只保证不满足条件不能推进。advance 需要 expected-run-id 和 expected-stage；Jira prepare/complete 需要 expected-run-id，均从当前 task.py status 固定。不要跳过 Workflow，也不要因本地处置而绕过服务端 Validator 或保护分支。

## 非阻断 Jira 状态同步

项目配置按消费者分工：`profile.json` 的 `jira.status_sync` 由 `jira_status.py` 消费，约束可准备的同步节点；`jira.transitions_config` 引用的字段与人工接力规则由 `jira_collect.py` 消费。`workflows_by_issue_type` 仅供 `project_rules.py workflow` 按事务类型查询，返回的 `stage` 是只读参考，不驱动本地阶段。Jira 回读为 Done、完成或 Tests Passed 均不等于本地任务完成；本地阶段仍由 Workflow 检查点及成果验收推进。未配置自动执行的原生转换只形成交接决策包，不生成自动写入意图。

初始化后先用 `task.py snapshot --issue-key <issue> --expected-run-id <run> --input <snapshot.json> --dir <station>` 保存已读的 issue/source_ref。该入口同 run 幂等，独立于水印、Jira 权限和产品版本查询；后续本地普通事实由 record 维护，在相关检查点确认。

接管时复用一次 Jira 初始快照，`jira_watermark.py prepare` 保存本地初始事实和产品版本，再准备原生回写。失败或未知结果记录为同步待办，允许推进 task_intake；未知写入先回读，同 run 恢复不重复导入或覆盖初始事实。

缺陷进入 `task_intake` 后，Agent 立即读取当前 Jira issue、当前用户和可用 transitions，以 `takeover` 节点执行一次 `jira_status.py prepare`。当前状态已是 `In Progress` 时直接记录；当前状态为 `Analyzed`、Assignee 为当前用户且 Jira 返回配置的 transition 时，工具生成精确 transition 意图，Agent 当场调用原生 Jira 工具一次并用 `complete` 导入回读。状态不匹配、必填字段缺失、权限或外部调用失败只记录和提示，不回退本地阶段、不重试。

Q4 有效确认并进入 `ci_validation` 后，以 `tests_passed` 节点执行相同步骤。接管阶段不要求创建 Test；编码完成后由用户与 Agent 根据已确认的验收方案创建或复用 Test，并通过「已链接工作项」关联缺陷。只有当前状态为 `In Progress`、Q4 总体处置为 `accept`、Manual/Unit 有当前完整提交 SHA 的 PASS 证据及逐项 `accept`，TapTest 满足项目状态条件、并且 Jira 返回目标 transition 时才生成意图。`Pull Request Submitted` 不自动执行。

输入由 Agent 从 Jira 实时读取，至少包含任务、字段、当前用户、可用 transitions 和可回查来源：

```json
{
  "source_ref": "可回查的 Jira 读取来源",
  "current_user": {"accountId": "当前 Jira 用户 accountId"},
  "issue": {"key": "TAP-123", "fields": {"status": {"id": "状态 ID", "name": "Analyzed"}, "assignee": {"accountId": "当前 Jira 用户 accountId"}, "issuelinks": []}},
  "linked_test_details": [
    {"key": "TAP-TEST-1", "test_type": "Manual", "case_version": "Jira updated 或 Xray 版本引用", "source_ref": "该 Test Details 的可回查 Jira 来源"}
  ],
  "transitions": [{"id": "421", "name": "Start Investigation", "to": {"id": "目标状态 ID", "name": "In Progress"}, "fields": {}}]
}
```

`linked_test_details` 只在 Tests Passed 或 PR Ready 核对时必需：它是 Agent 从 Jira/Xray Test Details 读取的事实，不由 AgenticOps 推断或写入。无法读取时，工具会要求用户提供关联 Test key、Test Type、用例版本引用和 Jira 来源。`Manual`、`TapTest`、`Unit` 是当前受管类型；`TapCE` 显式忽略但不算通过。若只有 TapCE、类型不支持、关联缺失或 Jira Validator 仍要求 TapCE，用户调整 Jira 或验收方案后重新读取并重做预检；不得盲目重放已经发起的 Jira 状态转换。

```sh
python3 "$agenticops_root/workflow/jira_status.py" prepare --expected-run-id "$task_run" \
  --issue-key "$task_key" --trigger takeover --operation-id <op-id> --input "$jira_snapshot" --dir "$project_station"

python3 "$agenticops_root/workflow/jira_status.py" complete --expected-run-id "$task_run" \
  --issue-key "$task_key" --trigger takeover --operation-id <op-id> --outcome failed \
  --input "$jira_readback" --message "Jira 原始错误摘要" --dir "$project_station"
```

`prepare.outcome=ready` 时只使用返回的 `transition_id` 调用一次原生 Jira transition；这是 Agent 协作约定，Workflow 不拦截原生调用。调用超时或结果不明时先回读；已到目标状态由 `complete` 记为成功，否则按 `unknown` 记录，不盲目重放。同一 run 的同一节点再次 prepare 只返回原记录。

转换 metadata 标记的必填字段为空时，`prepare` 不尝试写入，按 Project `field_mappings` 输出字段名、采集时机、本地来源是否已具备、可否自动填写和处理方式。根因、Module、Tester、测试设计结论、Xray 和测试例外等专业事实不能由 Agent 猜测；可从本地已确认方案和证据形成填写依据，但必须由责任人确认后在 Jira/Xray 补齐并回读。当前节点不重新尝试，最终在 PR Ready 输出人工待办。

| Jira 目标状态 | Jira 属性或关系 | 本地任务映射 | 处理时机与边界 |
|---|---|---|---|
| `In Progress` | Assignee / Engineering DRI | 无可替代的本地事实 | 接管前核对当前 Jira 用户与 Assignee；不一致不改派，跳过转换 |
| `Tests Passed` | Issue Classification、Root Cause Category | `facts.fix_plan` 仅提供已确认根因依据 | Q2 形成；Jira 枚举值由责任人选择，不从文本猜测 |
| `Tests Passed` | Module | 任务 `repositories` 与问题归属证据 | 目标仓库确认时形成；映射不唯一时人工选择最终一级 Module |
| `Tests Passed` | Issue Analysis | `facts.fix_plan`、Q2 已回读评论 | Q2 后补失效机制、触发条件、影响与证据，不生成未确认结论 |
| `Tests Passed` | Fix Details | `facts.fix_plan`、实际提交、Q4 验证 | 实现与验收后补处理方式、结果、边界和限制；计划不能冒充完成事实 |
| `Tests Passed` | Tester、Test can be automated | Q2 验收方案及实际责任人 | 由责任人确认；手工用例可选 `No`，但不等于免测 |
| `Tests Passed` | Fix Version | `facts.issue_version_plan` 只提供版本与修复线依据 | Tests Passed 前选择实际交付版本；不得把分支名或影响版本猜成 Jira 选项 ID |
| `Tests Passed` | Xray Test 关联 | Q1-Q4 检查项和 Jira「已链接工作项」中的 Test 任务 | 正常路径至少关联一项正式 Test 任务；每项均需以 PASS 执行证据获得用户 `accept` 确认，本地检查项不能替代 Jira 关联 |
| `Tests Passed` | Test Coverage Decision / Exception Details | 无默认本地自动值 | 仅在合规 T3 低风险例外获批后人工填写；否则不能借例外绕过测试 |

转换面板实时返回的 required fields 是本次尝试的最终事实源；上表用于提前采集和解释，不覆盖 Jira Workflow。若本地来源已具备，Agent 引导责任人据此回填；若来源缺失、值需专业判断、选项 ID 无法可靠解析或写后回读不一致，就跳过本节点转换，保留具体字段和 Jira 原始错误的脱敏摘要，在 PR Ready 一次列全人工事项。

## PR Ready 核对

正式提审前，先为每个任务仓库记录 PR，并使用 `ci.py watch` 取得当前 PR Head 的最新 Checks。Agent 同时从 Jira 读取当前任务 `fields.issuelinks` 与每个关联 Test 的 Test Details：只接受 Project 配置的关系（TapData 缺陷侧为 `tests`）指向、且任务类型为 `Test` 的关联项，输入 `pr_ready.py`：

```json
{
  "source_ref": "可回查的 Jira 读取来源",
  "issue": {
    "key": "TAP-123",
    "fields": {
      "issuelinks": [
        {
          "type": {"outward": "tests"},
          "outwardIssue": {
            "key": "TAP-TEST-1",
            "fields": {"issuetype": {"name": "Test"}}
          }
        }
      ]
    }
  },
  "linked_test_details": [
    {"key": "TAP-TEST-1", "test_type": "Manual", "case_version": "Jira updated 或 Xray 版本引用", "source_ref": "该 Test Details 的可回查 Jira 来源"}
  ]
}
```

```sh
python3 "$agenticops_root/workflow/pr_ready.py" \
  --issue-key "$task_key" --jira-input "$jira_test_tasks" --dir "$project_station"
```

工具只在以下三组均通过时返回 ready：从 Jira「已链接工作项」派生的受管 Test 非空，每个 Test 的 Test Type 与用例版本可回读，且Manual/Unit 在 Q4 以同一 `case_ref`、当前 Jira 用例版本和对应方式建立检查项并由用户基于当前 SHA 的 PASS 证据逐项 `accept`；TapTest 使用下述 Jira 状态判定；每个任务仓库都记录 PR，最新 Checks 为 `success` 且 Head 等于当前任务代码；Q1-Q4 及其检查项均有效，Jira 评论同步仅列警告。Jira Test 工作项本身不要求为 Done。没有符合关系和类型的 Test、未逐项确认、Checks 为空、跳过、未知、等待或失败、Head 漂移、`defer/accept_risk/rework` 都会列为待办。`Test Coverage Decision / Exception Details` 只能记录合规例外，不能替代 Test 关联。Jira 状态同步失败单独列入 `jira_status_todos`，不改变三类验收事实；Engineering DRI 处理待办后重新核对，并手工执行 `Pull Request Submitted`。

## 影响版本与优先修复线

任务开始保留一次 Jira 初始快照；本次采用的版本由用户确认并记录在本地，后续读取不覆盖它。版本名称不映射 Git 分支。先核验 develop，存在同一缺陷时优先在 develop 修复；否则独立确认一条真实实施分支。只核验优先分析分支与实际选择分支，其它受影响版本的合并与验证列为后续事项。

用 `task.py issue-versions --issue-key <issue> --expected-run-id <run> --input <json> --dir <station>` 导入；source_ref、完整 SHA 和确认来源必须来自实际记录：

```json
{
  "issue": {"key": "TAP-123", "fields": {"versions": [{"id": "1001", "name": "v4.22"}]}},
  "source_ref": "Jira 初始读取来源",
  "develop": {"status": "present", "revision": "完整 SHA", "source_ref": "源码或复现证据"},
  "effective": {
    "versions": [{"name": "4.22.0"}],
    "execution_branch": "develop",
    "release_follow_up": "其它影响版本的合并与验证待确认",
    "proof": {"actor": "实际确认人", "source": "user_message", "reference": "真实用户回复", "at": "2026-09-07T10:00:00+08:00"}
  }
}
```

effective.versions 无需 Jira ID。省略时采用初始版本，但仍需用户确认实施分支及来源。相同 run 的再次导入保留初始 observed，可在设计阶段修正版本；已准备的分支和 base_sha 必须保持一致。实际修复线改变仍需归档清理后重接并重新确认。版本差异进入同步警告，下一次 Jira 评论说明；不能把本地修正声称为 Jira 字段已更新。

用输出的 `primary_branch` 进行产品分支对齐，模块按其对齐结果准备，不把主仓分支名套给所有模块。TapData 的 `--tapdata-root` 指包含模块仓库的产品目录；`hazelcast` 固定参与并使用 `release-v5.5.0`。版本核验不改变 source 或当前分支。

## 检查项、执行及决定

一个检查点可以有多个检查项；一个检查项只能对应一个用例和一种验证方式。需要两种方式时建两个检查项。同一用例可用于修复前、后两个检查项，各自写明预期，保留独立证据。

- `plan`：稳定检查项 ID、检查点、`before_fix/after_fix`、用例引用及版本、`existing/proposed`、单一方式、仓库、目标代码版本、范围、预期及 `expected_result`。
- `executions`：可有多次执行；每次保留唯一编号、用例与代码版本、环境、来源、报告引用、观察时间、观察内容和原始结果。重试用新编号，不覆盖历史。
- `selection`：用户选择了该用例和方式的来源记录。先展示用例、预期、时机、成本与可行性，再记录用户选择；摘要哈希不是用户审批对象。
- `decision`：用户针对当前证据的处置、理由和确认来源。通过需明确选择当前用例及代码最后一条适用执行；计划变更及新证据使相关旧确认失效。
- `auto_checkpoint`：仅供 Project 标记为自动的检查点使用；它不接受伪造的用户确认，要求全部已选修复后项满足各自证据合同，TapTest 按 Jira 状态，其它方式按当前完整提交 SHA 的原始结果。TapData 的 Q3 据此记录首轮事实；Q4 中 Manual/Unit 仍需用户最终验收，全部修复后项均为合格 TapTest 时自动汇总状态接纳。

Q2 前通过 `task.py record --key fix_plan --value "根因、范围、修复方式、风险及回滚"` 保存实际方案，使确认和 Jira 回写包含修复内容而不只是测试清单。手工用例必须提供可操作 `steps`。修复后计划可先填 `target_revision: pending`，执行前用 `item` 更新为精确代码；只更新该字段不使已选用例失效，但会使该项旧执行处置失效。步骤、用例、方式、预期或范围实质变化仍需重新选择。

手工执行导入不接受分支名、短 SHA 或工作区占位符，必须绑定完整提交 SHA。首轮本地 Maven 自动验证允许 `git_revision` 的精确工作区指纹，以支持提交前测试；Q4 起的通过处置必须绑定完整提交。提交后须重新核对/执行，不能仅把旧报告版本字段改成新 SHA。实际测试在旧 SHA 时保留原证据并明确缺口，不能冒充当前提交已验证。

修复前不能执行时可以保留 `NOT_RUN`，解释原因并由用户延期、调整计划或接受风险。修复后项在 Q2 是 `not_due`，不能误报为缺失或失败。修改时机通过 `item` 更新并写理由，原计划保留在事件日志，新计划需要重新选择。

`PASS / FAIL / SKIPPED / NOT_RUN / UNKNOWN` 是原始执行结果。修复前复现可预期 `FAIL`，但必须是已确认符合目标故障的断言失败；环境失败不能算复现成功。用户认可复现不会把原始 `FAIL` 改名为 `PASS`。

| 处置 | 必需内容与效果 |
|---|---|
| `accept` | 理由、确认来源、`evidence_id`；实际结果满足预期，不能选过时、跳过或未知执行冒充通过 |
| `rework` | 理由、责任人、后续动作；当前检查点保持未解决 |
| `not_applicable` | 理由及确认来源；明确不适用，保留已有结果 |
| `defer` | 理由、责任人、后续动作及晚于确认时间的带时区期限；到期再推进时须重新处置 |
| `accept_risk` | 理由、责任人、后续动作及确认来源；允许带风险继续，保留失败／未执行事实 |

每个已到期项都要有处置，不能用一句“全部同意”隐去未解决项。检查点也要确认整体事实与缺口。没有任何用例时，Q2 之后不能声明验收通过，需要明确不适用或风险决定。用户最终决定验证方式，但无权通过本地质量记录绕过独立权限、合并、发布或 Jira Validator。

## 与测试结果联动

| 验证方式 | 执行来源 | 关联方式 |
|---|---|---|
| `taptest` | `taptest` | 从 `t-layer3-test` 读取 `write-xray-test`、`write-test-script` 的实际能力；由原生 Agent 使用技能生成／实现，导入具体 Xray Test Execution、报告、版本与单用例结果 |
| `unit` | `local_maven` 或 `ci` | 对应 Jira Test Type `Unit`；已有覆盖则复用，新覆盖由用户与 Agent 在所属产品模块工程实现；核对实际 class/method、报告、产品提交、测试版本、CI run/attempt |
| `manual` | `manual` | 用户选定的真实手工用例、执行人、环境、步骤预期及观察结果，关联可回查附件／评论 |
| `other` | `external` | 用户批准的其它方式；用例中明确方法细节，并导入其执行证据 |

AO 不新增测试执行器或测试平台客户端。Agent 用现有工具读取结果，或记录用户提供的证据，再通过 `execute` 导入。导入是对来源的记录，不能独立认证外部结果；验收时必须展示来源并请用户确认。报告不能映射到具体用例时保留 `UNKNOWN`。

本地 Maven 与 CI 是同一集成测试方式的不同执行来源。`ci.py watch` 只观察 PR 返回的检查，绿色不证明目标集成用例运行；必须核对路径过滤、矩阵、跳过条件及测试报告。未知、空检查、跳过均不会被当作成功。CI 记录按任务、run、仓库和 PR 隔离，旧版无身份记录不作为当前证据。

公司 wiki 索引在项目 `quality.json`；目标分支的代码、POM 和 CI 是实现事实源。Failsafe 项目在核对配置后可使用 Project Runbook 指定的 Maven 命令运行 `test-compile failsafe:integration-test failsafe:verify -DskipITs=false`，指定用例用 `-Dit.test=Class#method`；Runbook 指定任务本地仓库时不得退回裸 `mvn` 或共享缓存。`mvn test` 或仅编译成功不能证明该集成用例已执行，先核对实际模块及报告。

## 操作接口

首先使用 `task.py` 建立任务、登记仓库，再读取质量快照：

```sh
python3 "$agenticops_root/workflow/quality.py" status \
  --dir "$project_station" --issue-key "$task_key"
```

`status` 返回当前 `run_id/revision`、项目方式、缺失事实、各项计划及证据、检查点的 `due/not_due/problems`、具体 `handoff`、评论是否 `published` 和确认所需 digest。`task.py next --issue-key <issue> --dir <station>` 汇总下一阶段门禁、检查点和待回读评论，只读且不授予权限。每次写入从最新快照取得版本，避免覆盖别人的决定：

```sh
python3 "$agenticops_root/workflow/quality.py" apply \
  --dir "$project_station" --issue-key "$task_key" \
  --expected-run-id "$run_id" --expected-revision "$revision" \
  --input "$quality_input"
```

`quality_input` 是普通 JSON 文件，格式为 `{"action":"操作名","payload":{...}}`。完整字段、枚举和必需内容见契约；不接受未声明字段。以下是单项用户选择，`proof` 必须来自真实用户回复，不能由 Agent 编造：

```json
{
  "action": "select",
  "payload": {
    "item_id": "regression-1",
    "digest": "status 返回的该项 plan_digest",
    "proof": {
      "actor": "实际决定者",
      "source": "user_message",
      "reference": "可回查的用户回复引用",
      "at": "2026-09-03T10:00:00+08:00"
    }
  }
}
```

| action | payload 主要字段 |
|---|---|
| `item` | `plan`、变更 `reason`；更新同 ID 需重新核对受影响确认 |
| `select` | `item_id`、`plan_digest` 对应的 `digest`、`proof` |
| `execute` | `item_id`、`execution`；导入一次实际执行或未执行说明 |
| `decide` | `item_id`、当前项 `digest`、`decision` |
| `checkpoint` | `checkpoint`、当前检查点 `digest`、`decision` |
| `auto_checkpoint` | 自动检查点、当前 `automatic_digest`、事实理由；不携带用户 proof |
| `draft` | `id`、准确 `body`；检查点回写还需 `checkpoint`，正文必须是该点的 `publication_body` |
| `confirm` | 草稿 `id/digest/proof`；确认完整正文及目标 Jira |
| `prepare_write` | 草稿 `id/digest`；成功保存后返回 `operation_id` 与 `intent` |
| `receipt` | `id/operation_id/result`；`created` 必须有 `comment_id`；已知未写入记 `deferred` 并给 `reason`，不明结果记 `unknown` |
| `readback` | `id/operation_id/site/issue_key/comment_id/body/source_ref`；匹配后才为 `verified` |

`proof.source` 支持 `user_message/jira_comment/review`，必须包含决定者、来源引用和带时区时间。该记录提供审计出处，不提供用户身份认证或密码学签名。平台和服务器权限负责外部操作，Workflow 在检查点核验记录。

## 回写和恢复

TapData 每个检查点确认后尽力回写 Jira，评论失败或未回读仅产生警告，不阻止阶段推进或 PR Ready。使用 status 返回的简洁 publication_body；完整结构化证据保存在本地。真实测试、方案确认及必要验收条件仍须满足。

方案确认时一并告知用户将把 Q1/Q2、合格的 Q3 首轮事实和最终 Q4 内容回写 Jira。用户可在同一真实回复确认方案、验收方式、任务授权及合格 Q3 的自动回写/推送/Draft PR；该回复可被引用完成多项选择和评论确认，不要求逐条回复。Q4 的 Manual/Unit 仍须基于当前 SHA 的证据请求最终验收；全部为 TapTest 时按状态自动汇总。若内容、授权范围或风险发生实质变化，再请求缺少的决定；不能由 Agent 自行编造用户同意。每个步骤成功后继续已授权编码、验证、提交/推送、PR、CI 和回写，不把一次工具成功当作最终停点。合并、发布等独立授权边界不变。

确需停下时，必须解释当前检查点要核对什么，并把 `handoff` 转成用户可执行的说明：用例/步骤、预期、执行人、环境、仓库及完整 SHA，要求返回日志/报告与实际结果，列出可选处置和仍可继续的工作。缺少 SHA 的日志先补来源，不猜目标版本；恢复同一 run 时不重复问已有效确认的问题。

`evidence.py` 生成可审阅证据摘要。将准备发送的精确正文保存为草稿，展示给用户确认后，先 `prepare_write`，再由原生 Jira 工具发送，随后导入回执和回读。`verified` 仅代表该评论的目标、编号和正文匹配，不代表 Jira 状态流转或任务发布完成。Markdown／ADF 被外部工具规范化时应使用双方一致的正文表示；不匹配时保留现场，不能忽略差异宣布成功。

发送前正文或关联质量快照变化，会使确认失效。检查点评论只绑定该点快照和处置，后续无关阶段不使它失效；普通汇总草稿仍绑定整体快照。拿到意图后发生超时或中断，先回读核对，不重复调用新增评论。未拿到评论 ID 时只能通过可核实的原始请求与远端记录定位；多个同文评论无法消歧时人工接力。`operation_id` 是本地关联编号，不是 Jira 服务端幂等键。

日志按当前 task/run 隔离，追加操作复用任务锁、revision 比对、原子替换和持久化。旧 revision、错误 run、损坏或未知版本不会覆盖原记录。实际代码变化、相关仓库 CI 变化或用例／方式／预期变化使相应确认失效；工作目录指纹只保存摘要，不保存源码。修复前复现仍绑定原版本，不因开始修复而丢失。

Q1 仅绑定准入事实和已到期项；Q2 绑定修复方案、稳定用例选择及修复前处置，不绑定修复后 SHA、执行结果或 CI。后续编码、验证结果、PR 编号和 verification 文本不会迫使用户重复确认 Q1/Q2；实质方案或准入事实变化仍使相关确认失效。验收点仍随目标代码及相应证据变化而失效。历史事件按其记录时的规则重放，新规则不改写旧证据；升级后旧规则下的确认需按当前快照核对，不能静默伪造迁移。

受控 cleanup 在成功移除干净工作树时保存 `final_revision`，质量快照继续核对该提交，避免仅因移除了工作目录便让验收确认失效。移除前代码已变化仍会失效；旧记录缺少最终 SHA 时不能猜测补齐。

清理后重接的新 run 不继承旧验收；旧未知评论只作为原档案证据，不借新 run 重发。恢复未知外部调用必须核对原操作，不删除账本消除未知结果。

接管进入 `task_intake` 后尝试一次 `Analyzed → In Progress`，Q4 验收完成并进入 `ci_validation` 后尝试一次 `In Progress → Tests Passed`。每次都先读取 Jira 当前状态、可用转换和转换必填字段；状态不匹配、字段补不了、权限不足或外部调用失败时记录人工指引并继续本地主流程，不重试，也不影响后续节点按各自事实再尝试。附件规定与线上 Validator 若不一致，应报告并以实时 Workflow 为准；手工用例仍是用例，不能冒充缺陷免测。`Pull Request Submitted`、实际合并、发布和 `Done` 仍分别以标准流程及外部事实人工确认。

本地 `accept_risk` 不等于公司的免测批准。T3 标准中的低风险例外仍需核对优先级、替代验证、责任和回滚措施，以及规定的模块负责人和审批人批准；P0/P1、数据安全、权限或高可用等要求不得据此自动豁免。发生冲突时先报告用户并确认后续处理，保留 Jira 的真实状态。

Jira 评论采用简洁可读文本。当前 Rovo addCommentToJiraIssue 的 commentBody 为 Markdown 字符串，不接受直接传入 ADF 对象；不增加重复 Jira 客户端。正文只规范化 CRLF 和行尾空白，仍核对完整可见文本与目标、评论编号。短标识用于定位，不独立证明内容正确；若工具返回 ADF，由无状态协议转换提取文本，不能靠猜测反转义伪造回读。

每个阶段均可运行 task.py next 查看 blockers 与 warnings，PR 提交后运行 evidence.py 汇总执行过程被跳过的处理与警告。外部同步失败只暂停该写入或重复尝试，完整测试、CI 和用户验收条件继续由检查点核验。

字段写入成功后，用 `external_sync.py status --issue-key <issue> --dir <station>` 获取本地 fact_digests，再以 `readback --expected-run-id <run> --input <json>` 导入 `{fact_key, expected_fact_digest, jira_field, issue, source_ref}`。issue 是实际 Jira 回读，字段必须与本地有效值一致；problem_version 按配置的影响版本字段核对版本名称，不要求本地有 Jira ID。同步回执单独持久化，不改变原始快照、方案确认和阶段；本地事实再次改变后旧回执失效。

## 质量输入预检

`python3 <agenticops-root>/workflow/quality.py validate --input <json>` 只校验单个 `{"action":"...","payload":{...}}` 操作对象的格式，不接受数组或批量 JSON，也不读取或修改工位；成功仅表示契约格式有效，不证明授权、测试证据或检查点已通过。错误包含所选 action 的具体字段路径；JSON 解析错误保留行列位置。

`apply --expected-revision` 使用 `quality.py status` 返回的质量日志 revision，不使用任务 current 的 revision。冲突诊断同时显示 expected 与 actual，并保持未写入。

新增 `execute` 操作的 `NOT_RUN` 必须提供 `execution.nonexecution_reason`，例如环境缺失的具体配置项；`failure_kind` 仍为 `none`，不能把未执行写成环境测试失败。原始结果与版本不改名，历史事件读取不补造原因。此字段改变旧版本对事件契约的读取能力，工位 epoch 提升至 10；跨代际按更新与回退指引在原版本退出并 purge，不在线转换任务。


### TapTest 的 Jira 状态依据

使用 `quality.py apply` 导入 `{"action":"jira_status","payload":{"snapshot":{...}}}`。snapshot 必须有当前 `run_id`、`source_ref`、带时区的 `observed_at`、当前 issue 的 `issuelinks` 和 `linked_test_details`。每项 Detail 包含 `key/test_type/case_version/source_ref/updated/status`，status 为 Jira 原生 `{id,name}` 对象。先用原生工具回读，不能根据旧报告推断。

TapData 精确接受 `Tests Passed`、`PULL REQUEST SUBMITTED`、`MERGED`、`完成`；实际显示名 `Pull Request Submitted`、`Merged`、`已完成` 及稳定 ID 在项目配置中显式映射。Q3、Q4、Tests Passed 和 PR Ready 共用此判定。TapTest 无需额外本地 execute PASS、逐项 accept 或测试版本 SHA；Q2 的用例选择仍保留。仅含此类修复后项的 Q4 自动汇总，混合 Manual/Unit 等验收仍保留整体研发决定。

状态材料绑定回读时的代码指纹；代码改变后重新回读并导入 Jira，不要求为此重复变更 Jira 状态。真实 A 版本执行保持 A，展示本地旧报告未证明当前代码；这不是自动重测门禁，补测依据已确认方案及影响分析决定。每仓源码同步、CI 和其它验证要求独立保留。PR Ready 与 Jira 转换核对使用本次传入的实时快照，不能仅依赖之前导入的通过状态。

本次新增质量事件不能被旧版解释，工位 epoch 提升至 12；旧工位须在原版本结束并清理后切换，不在线迁移。


### Jira 全流程统一决策包

项目 `profile.json` 引用 `jira-transitions.json`，按任务类型声明字段 ID/别名、类型、来源、条件必填、采集节点、决策责任人和依赖。已核验状态目录及转换均标明来源；`coverage.complete_workflow_export=false` 表示没有管理员完整工作流导出，未见转换的拓扑、未来表单和 Validator 不猜填。Agent 可采集任意原生转换的实时表单，未获自动授权的转换只交研发处理。

在 intake、design_review、acceptance、pr_review、transition 的相关人工节点执行 `jira_status.py collect --checkpoint <节点> --issue-key <key> --expected-run-id <run> --input <json> --dir <station>`。输入包含 `snapshot`（原生 issue、transitions.fields、source_ref）与可选 `proposals`（字段 ID 到建议值）。输出按字段 ID 去重，保留所有转换用途，统一展示已有值、可复用确认、本节点待决、未来待采及动态未知项；建议值不是 Jira 已写事实。

一次向研发展示全部本节点 pending。取得真实决定后，使用相同输入增加 `field_digests`（每个提议值对应 collect 返回的字段摘要）和 `proof`（actor/source/reference/at），执行相同参数的 `confirm`。确认绑定值、选项、依赖和责任人；无关评论及内部 revision 不导致重问。条件必填根据拟确认值计算，未来才知道的验收结果不在设计阶段索要。确认不执行 Jira 写入，Agent 按原生权限回填后仍须回读。

`prepare/complete` CLI 必须携带同一个 `--operation-id op-...`。新一次退回重进使用新编号，旧编号始终指向原尝试。ready、unknown、failed 的前次结果未消解时禁止换编号重发；状态仍未改变不能证明没有写入。明确未写入需在 complete 使用 `--outcome not_written`，快照增加 `operation_result={operation_id,effect:"not_written",source_ref}`，来源为原调用的明确失败/回查事实。到达目标即回读为 succeeded。

`--trigger native:<实时转换ID>` 仅返回人工交接与统一决策包，不返回自动执行许可。审批、PR 提审、合并、发布和终止不因字段齐全而获得新授权。缺项只暂停本次转换，继续无依赖准备。持久确认和尝试账本语义变更使工位 epoch 升至 13，旧工位须由原版本退出。


## 功能方案完整性与一次决策包

TapData 功能方案仍使用 implementation_plan，项目 quality-feature.json 的 plan_contract.review 定义 Q2 必需结构。保留 objective、changes、acceptance、risks、rollback，同时提供：

| 字段 | 内容与检查 |
|---|---|
| reference_implementations | found：repository、path、完整 source_revision、difference；检查本地 Git 对象中的文件。not_found：search_scope、source_revision、evidence_ref、difference，说明实际检索未发现，不编造参考 |
| acceptance_mapping | 每行 criterion 原样对应 acceptance_criteria 文本或文本列表的一项，并填写 behavior、repository、module、case_ids、expected；所有 AC 均有映射，用例 ID 必须是已登记的 after_fix 验收项 |
| scope_rationale | changes 每行 repository、module、layer（local/shared）、necessity、impact；non_changes 列明确不改范围；blocking_inputs 列尚待研发决定的冲突。公共层修改同样必须说明必要性和影响 |
| delivery_dependencies | 每仓 repository、depends_on（仓库 ID 列表）、delivery、test_relation、state（ready/planned/unresolved）、source_ref；登记仓不能遗漏，不要求尚未创建的 PR 已存在 |
| environment_readiness | config_source、checks（name/source_ref/result/detail）、blocking_inputs；检查结果 ready/not_needed/missing。已知 missing 与未决输入一次列全，不以空列表隐藏 |
| verification_plan | commands、scenarios、invalidation_conditions、evidence_ref；不是执行报告，实施后仍需真实验证 |

acceptance_criteria 多项建议保存为文本列表；单个文本按一个完整 AC 精确映射，不由工具猜测自然语言的拆分。作用域模块须与 AC 实现映射对应。结构检查和 Git 文件存在均不能证明参考恰当、公共层修改必要或断言有效；Agent 必须比较源码与预期，研发确认实质方案。完整方案中没有阻塞项后才允许 Q2 通过与授权；旧简略方案须补全后再确认，不在线迁移或伪造确认。此变更不增加持久状态字段，继续使用 epoch 14 的现有事实与质量日志结构；配置变化自然使旧确认摘要失效。

Q2 前先补齐 Agent 可查事实，读取当前 Jira 字段与 transitions.fields，然后一次展示：

```sh
python3 <agenticops-root>/workflow/quality.py review --dir <station> --issue-key <issue> --input <原生Jira快照.json>
```

此命令只读，返回方案、验收检查点、全部方案缺口和 jira_status collect 的当前/未来字段包；未来验收结论不提前索取。用户一次答复可作为质量确认和 Jira 字段确认的共同来源，按各自摘要记录，不再重复询问相同事实。Jira 动态未知表单如实显示；Jira 字段未齐只暂停相关转换，不新增整个研发流程的同步门禁。已确认后改变实质方案，使用[同周期返工](task-authorization.md#同周期方案返工)。

例如 MySQL 视图功能，先读取目标分支现有表/视图发现、元数据映射和执行用例，再决定在连接器局部扩展还是修改共享层。将“可发现视图”“可读取视图数据”“权限不足/不支持时的行为”分别映射到实现模块与用例；没有源码依据不能预设必须修改引擎或 sql-core。
