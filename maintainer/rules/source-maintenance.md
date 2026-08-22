# AgenticOps `maintainer` 工作面规则

## 故事质量门禁

AgenticOps 只维护两类项目质量故事：

- 项目维护故事：主角是公司员工指导员，保护源头项目的维护、安装、发布和演进质量。
- 研发工程师故事：主角是业务项目工作空间代表的研发工程师，保护安装、授权、任务执行、恢复、证据和审计质量。

维护源头仓库时，任何代码、规则、Skill、标准、测试或文档变更都必须先执行 `ao-maint story impact`。Runtime 命中故事保护路径后，在 worktree / staged 阶段输出候选预警、整理精确候选并运行固定验收，不请求人工确认；代码审查只在形成版本化分支策略指定的 commit 或 PR 事实后发生。

直接修改故事文档、故事注册表、保护路径、验收检查或证据要求时，现有任务级连续执行授权失效。功能、修复和任务分支在固定验收后继续形成 commit、推送任务分支并创建或更新 PR，用户在 PR 当前 Head 上逐项审查；`develop` 等其它允许分支在固定验收后形成本地 commit，但必须保持未推送并在推送前逐项审查。Runtime 将人工事实与 `impact_id`、commit SHA 或 PR Head、报告摘要共同绑定；用户只确认可查阅的代码事实、确认事项、变更点和风险，不查看或复制 `impact_id`。任意非空字符串、旧提交、旧 PR Review、旧版审批记录或仅有任务级授权都不能作为故事批准依据。

禁止行为：

- 不得使用 `git commit --no-verify`、临时修改 Hook、删除注册表或伪造本地确认记录绕过门禁。
- 不得由 AI 猜测缺失的故事映射并默认放行。
- 不得把任意 Shell 命令写入故事注册表；验收只能引用 Runtime 固定白名单。
- 不得把 Jira 计划、当前进度或临时实现状态写入故事质量合同。

治理范围内路径缺失故事映射时，返回 `maintenance_story_mapping_missing` 并请求公司员工指导员处理。

Git 必须通过 common directory 中的 trusted launcher 从已接受 `HEAD` 加载版本化 Hook，再由 `HEAD` Runtime 检查隔离 index 快照。不得把工作树或 candidate 的 Hook、`ao-maint`、故事 Runtime、注册表或发布脚本作为自己的信任证明。发现未暂存门禁差异时立即停止。

正常发布 `publish` 只允许刷新后的 `origin/main` 基线 Runtime 检查固定 candidate。基线缺失时返回 `release_story_gate_baseline_upgrade_required`；候选修改 Hook、门禁 Runtime、注册表或发布脚本等信任根时返回 `release_story_gate_trust_root_changed`。两者都必须改走受保护 `main` 的独立人工审查 PR，不能由自动 publish 放行。Hotfix 不执行故事门禁；它只按 D-055 对已同步 `develop` 做 Jira key 绑定的原子直合。

本地 Hook 是防误操作层，无法阻止本机控制者使用 `--no-verify` 或修改 Git 配置。硬门禁必须由无 bypass 的 `main` Ruleset 强制至少 1 个独立人工批准、最后推送者不能自批、dismiss stale approvals 和解决全部 review threads；即使 candidate 删除仓库内门禁调用也不能自动合并。`origin/main` 发布基线负责确定性复检；不得把单一本地 Hook 或仓库内脚本描述成不可绕过的信任根。

## 维护任务处理约束

maintainer 面处理任何 Jira 任务（建卡、实现、修复、规则演进等）时，必须遵守以下处理流程：

`ao-maint` 的 Jira 任务边界固定为 AO 项目。CLI 必须在读取 maintainer 凭证、联网、写计划或写决策审计前校验 issue key、project key、父任务和计划文件；Service 必须对直接调用、摘要重新计算后的计划以及远端 Issue key/project 回读做第二层校验。任何非 AO 输入或回读统一返回 `maintainer_jira_project_scope_mismatch`。`jira auth` 仅管理 maintainer 凭证并输出 AO 允许范围，不执行全站字段探测，也不授予或暗示 TAP 等业务项目操作能力；业务项目只能在对应 developer 工作空间使用 `ao-work`。

1. 用户要求“接管 `<AO-KEY>`”时只调用 `ao-maint takeover <AO-KEY>`；Runtime 自动判断新接管、恢复、接纳存量或阻断。
2. 新接管：Runtime 先向任务写一条中文评论（说明开始处理的内容与计划），再把任务状态流转为「正在进行」，并逐项回读。
3. 恢复或接纳存量：必须向用户明文说明判定模式并留下审计；所有权、范围或外部事实不明确时进入风险决策门禁。
4. 设计审查确认后：形成绑定 Jira 工作项、`maintainer_run_id`、Agent、源头仓库、工作分支、设计摘要、修改范围和验证方式的工作项级连续执行授权。
5. 授权范围内：分析、实现、验证和必要 Jira 进度回写连续推进，不逐项暂停。
6. 代码审查：功能、修复和任务分支推进到 PR 后提供 PR 地址与当前 Head 并停止在 PR Review；其它允许分支形成本地 commit 后提供提交编号并保持未推送。两种通道都逐项展示确认事项、变更点和风险，确认只绑定当前代码事实。
7. 合并代码后：向任务写一条中文总结评论（说明实现内容、验证结果与风险），再把任务状态流转为「已完成」。

AO 工作项因权限、外部事实、依赖、环境或能力缺口无法在当前运行完成时，不得仅因任务阻断而回退已经完成且通过范围匹配验证的变更。AIAgent 必须先将该工作项的精确已验证候选转存到其专用本地分支、受管快照或可回放补丁，记录候选文件、当前 HEAD、验证证据和恢复入口；随后恢复 `develop` 的工作树与 index 为不含该候选的干净状态，继续推进其它任务。不得提交、推送或把未验证候选混入其它任务的 `develop` 提交。

通过 `ao-maint` 向当前 Jira 工作项写入并回读中文阻断评论，至少说明阻断原因、已完成验证、转存位置与候选摘要、未完成项、当前 HEAD/分支、恢复入口及恢复前必须重查的外部事实。恢复时优先使用 `ao-maint takeover <AO-KEY>` 恢复原运行，并重新核对 Jira、转存候选、故事影响和验证证据；符合条件后再将候选合并回 `develop` 统一处理。只有验证失败、候选越界、含敏感信息、与外部事实冲突、妨碍更高优先级已授权工作，或用户明确要求放弃时，才允许按精确文件范围删除转存候选；不得使用 `reset --hard`、`checkout --` 或清理整个工作树。

接管、评论与状态流转必须通过 `ao-maint` Runtime 执行。底层 Jira 写入仍执行 plan → apply → readback：人工门禁使用精确的 `user-confirmation:<KEY>:<plan_id>`，设计确认后的常规写入可使用 Runtime 回读有效的 `work-authorization:<KEY>:<RUN>:<DESIGN-DIGEST>`，并留下决策审计记录。工作项连续授权不覆盖 Jira 建卡和任务描述整体替换，也不覆盖 `main`、合并、正常发布、Git Tag、强推、历史改写、范围变化或风险决策。唯一例外是显式调用 `maintainer/scripts/hotfix.sh publish --jira-id <KEY>`：它不使用 Jira Runtime，不回写 Jira，只把 key 写入 Merge commit，并直接原子同步 `main` 与 `develop`。不得绕过 Runtime 直接调用 Connector、Jira REST API 或 Shell 网络请求；发现能力缺口时先补齐 Runtime 能力，再执行任务操作。

以下是 maintainer 工作面的固定暂停点：

- 设计审查：确认版本化设计或当前任务设计摘要，并据此建立连续执行授权。
- 代码审查：PR 通道绑定 PR 地址与当前 Head；commit 通道绑定本地提交编号并发生在推送前。两种通道都绑定故事影响、验证结果、确认事项、变更点和风险。
- 风险决策：所有权、范围、仓库、分支、验证、外部事实发生变化，或外部写入结果不明确、连续失败时。

除上述暂停点和独立的受保护操作门禁外，正常推进不得反复请求确认。

原子步骤成功不是会话终点。AIAgent 必须在当前用户目标、授权、范围和风险边界内持续消费 Runtime 返回的 `agentic_next_action`，直到到达真实人工节点、流程终态或明确阻断。接管、分析、补卡、实现、验证、证据回写、提交、推送或 PR 等任一原子操作的 `ok=true`、`status=completed`、内部 action、digest、plan id 或“操作成功”都不能单独作为最终答复。

停止时必须展示公司员工指导员可以直接判断的完整中文内容：信息补充节点展示缺失项和补卡建议，设计审查展示完整设计、范围、验证方式和风险，代码审查展示当前 commit 或 PR 事实、确认事项、变更点和风险，风险决策展示事实、选项、推荐和影响；恢复任务必须恢复真实 pending gate 及其正文。只有到达这些真实人工节点、完成/交接/取消等流程终态、出现需人工输入或权限/外部事实变化/能力补齐才能解除的结构化阻断，或用户显式限定为单步、只读或诊断时才允许停止。允许自助恢复或受控重试时必须先按 Runtime 指引执行；不得借连续推进跨越人工门禁、扩大授权、绕过能力目录或重试上限，也不得在输入不变时循环执行。

评论正文必须按公共评论模板（`shared/standards/jira-comment-template.schema.json`，人读版见 `docs/templates/evidence-templates.md`）组织：`progress`（进度上报/开始处理，重点=进度与任务状态）与 `evidence`（结果反馈/完成总结，重点=总结）评论必须包含 Schema 声明的全部必填键（运行 ID、当前阶段/已完成动作/执行计划/风险 或 完成内容/验证结果/残留风险/已输出表单字段），缺失即被 `ao-maint jira comment plan` 阻断。执行者与工作空间只在任务接管评论声明（代表哪个 Agent 正在处理），字段为执行者 agent_id、Agent 类型、模型、接管环境、运行 ID；身份来自 `ao-maint install identity set` 配置（plan 输出带出 agent_id/agent_type/model/environment）。写评论前先读取任务 Description 与模板提取事实，不得只写一句话或凭记忆临场发挥。
