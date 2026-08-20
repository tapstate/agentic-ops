# 设计决策记录

本文记录 AgenticOps 第一阶段已经确认的设计决策，以及需要请求用户决策的触发条件。

## 1. 已确认决策

| 编号 | 决策 | 说明 |
| --- | --- | --- |
| D-001 | 第一阶段进入本地实现（已被 D-031 取代） | 该历史决策只记录最初本地实现范围，不再限制当前真实 Jira / GitHub 门禁操作或源头仓库正式发布流程。 |
| D-002 | AgenticOps 是 AI 执行控制体系 | 它不替代 Jira、研发工程师、拉取请求审查、CI 或发布流程。 |
| D-003 | 第一阶段由研发工程师手动触发 | 不做全自动任务接管，低风险任务也先保留人工确认。 |
| D-004 | 主流程从已进入迭代的 Jira 卡片开始 | 需求复杂度、风险、优先级和范围边界应在进入迭代前完成确认。 |
| D-005 | 源头仓库是 `tapstate/agentic-ops` | 源码、规则、AI 员工手册、skills、工作流配置、templates、adapters、CLI / SDK 和通用文档都在这里管理。 |
| D-006 | `~/.agentic-ops` 是全局安装目录（由 D-046 细化） | 该目录不是具体项目或具体任务的运行目录，只交付 developer 工作面。 |
| D-007 | 具体项目 AI 工作空间是运行目录 | 不同工作空间对应不同 Jira 空间、GitHub 仓库、本地源码和任务上下文。 |
| D-008 | AI 员工手册是一等交付物 | 它同时服务 AIAgent 和研发工程师，定义工作方式、停止条件、工具使用和证据回写。 |
| D-009 | 操作契约屏蔽 Jira 事实 | AIAgent 面向操作工作，不直接依赖 Jira 字段、状态名和工作流细节。 |
| D-010 | 工作流配置管理项目差异 | 不同项目通过工作流配置映射 Jira 状态、字段、权限、仓库和人工门禁。 |
| D-011 | 第一阶段控制层采用 Go CLI 运行时（已被 D-038 取代） | 该历史决策只用于解释已删除 Go 实现的背景。现役代码和发布资产不包含 Go Runtime 或平台二进制，需要追溯时从版本分支、Tag 或 Git 历史查阅。 |
| D-012 | Git / GitHub 轻 guard，不完全封装 | Git 和 GitHub 不会换，重点控制危险动作、记录证据和阻止越权。 |
| D-013 | 反馈闭环只生成改进建议 | AIAgent 可以分析工作日志并提出 proposal，但不能未经人工确认自动改写源头规则。 |
| D-014 | 文档可见标题默认中文 | 产品名、工具名、命令、字段、目录名和稳定编号可保留英文或缩写。 |
| D-015 | AIAgent 不按固定角色工作 | AgenticOps 按任务类型、当前阶段和下一步动作驱动 AIAgent，不再使用固定 AI 角色作为第一阶段执行模型。 |
| D-016 | AgenticOps 覆盖任务接管到完成的流程控制 | AgenticOps 帮助研发操作 AIAgent 从 Jira 接管任务到完成任务；不同任务可进入不同流程，但执行过程必须有记录，并回写关键状态、关键信息和证据，用于后续分析优化。 |
| D-017 | AgenticOps 长期目标是公司事务处理标准化 | AgenticOps 首先落地研发 Jira 任务，长期目标是把公司事务处理方式沉淀为可执行、可审计、可回滚的规范资产，让 AI 按公司当前规范处理问题，并通过执行记录持续提出规范优化建议。 |
| D-018 | 正式使用前必须具备成熟修复路径 | AgenticOps 正式给研发日常使用前，必须能按问题类型选择修复载体，支持脱敏诊断、快速发布、资产热更新、同步和回滚；四类首要问题是 CLI 逻辑错误、Jira 流程状态没适配、Jira 卡片属性丢失和关键步骤门禁调整。 |
| D-019 | 当前项目权威源头是 `agentic-ops` | AgenticOps 相关设计、计划和目标都在当前项目中维护；历史 `rd-agentic` / `td-agentic` 后缀项目只作为参考来源，不作为当前事实源或设计依赖。 |
| D-020 | AgenticOps 的目标重心是形成 AI 可执行标准 | AgenticOps 不只是提供命令，而是通过 AI 员工手册、操作契约、工作流配置、策略、运行手册、模板和反馈闭环形成标准；除非问题来自对应工作面的 Runtime 逻辑，否则 AIAgent 应优先按标准资产自助处理、阻断或转人工。 |
| D-021 | 同仓库按目录分管资料，发布时拆分交付物（由 D-045、D-046 细化） | 当前项目不使用不同分支分管源码、设计、计划或运行资产；维护者面对完整仓库，研发工程师和 AIAgent 只接触 developer-only 安装交付物。 |
| D-022 | 发布支持策略采用 latest-only | AgenticOps 不维护旧版本补丁线；BUG 只在最新版本修复，有新版本时推荐自动更新应用。rollback 只用于安装失败或新版本不可用时的本地恢复，不作为旧版本修复策略。 |
| D-023 | 版本号采用运行状态、迭代版本、提交序号和提交编号 | AgenticOps 版本号格式为 `STATE-vMAJOR.ITERATION.COMMIT_INDEX-COMMIT`，例如 `INS-v0.1.3-a68372d`。正常发布准备时维护者创建 annotated `vMAJOR.ITERATION` tag；build 自动按最近迭代 tag 到 HEAD 的提交计数生成 `COMMIT_INDEX`，并注入当前 Git short commit。提交计数允许因合并和 Hotfix 跳号；Hotfix 复用最近版本基线，不创建新 tag。 |
| D-024 | Jira 交互人可见内容使用中文 | 写入 Jira 的标题、描述、评论、工作日志、证据正文、阻塞说明和补卡说明必须使用中文；Jira 字段名、状态名、`transition` 名称、卡片编号、命令、配置字段和协议字段可以保留原始英文或缩写。 |
| D-025 | AI 操作任务表单标准是 AgenticOps 源头标准 | AgenticOps 维护标准任务字段和生命周期要求；不同 Jira 项目、工作流、页面或自定义字段先通过 Jira 表单映射适配。不符合标准的地方记录缺口并请求人工决策，不能让 AIAgent 直接按 Jira 字段猜测。 |
| D-026 | AgenticOps 承载公司员工执行标准 | AgenticOps 的定位从“公司事务处理方式”进一步收敛为“公司员工执行标准”：AIAgent 按标准动作执行任务，每个节点输出标准表单数据，不同专业角色在对应节点审查产出，后续操作根据表单数据、审查结论、失败码和门禁决定继续、重试、重做或停止。 |
| D-027 | 框架稳定，成熟逻辑原子化 | AgenticOps 框架先稳定定义大的流程环节、门禁、状态、容错和演进机制；成熟固化的交互逻辑再沉淀为原子化操作。脚本入口只做受控编排或调用，不承载业务判断。AIAgent 在具体环节内执行任务并沉淀经验，周期性复盘把高频经验、失败模式和人工退回转化为工作流配置、策略、运行手册、模板或操作的改进建议。 |
| D-028 | CLI 组件命名为 AgenticCLI（已被 D-045 取代） | 该历史决策记录旧 Go 统一入口命名，不再定义现役命令。 |
| D-029 | 标准流程由 Standard Process Registry 维护（developer 绑定细节由 D-051 修订） | 任务必须先分类，再进入对应标准流程；工作流配置只负责把标准流程映射到具体 Jira 字段、状态和 transition。旧 developer `agentic_id` 绑定和清理模型不再适用；AO maintainer 专用字段设计不受影响。 |
| D-030 | 代码推送后必须回写 Jira 变更总结 | 能可靠确认对应 Jira 编号时，AIAgent 必须在推送成功后追加中文 Jira 评论。推送总结只描述做了哪些调整，不固定附带分支、提交、验证结果或残留风险。评论失败时明确标记回写未完成，恢复后只重试评论，不重复推送；真实 Jira 写入仍遵守既有门禁。 |
| D-031 | 源头仓库采用正式分支和脚本发布流程 | GitHub 默认分支为 `main`，日常开发使用 `develop`；流程禁止直提直推 `main`，只允许通过 PR 的 Merge commit 合入。现役正常发布使用 `maintainer/scripts/release.sh`，Hotfix 使用 `maintainer/scripts/hotfix.sh`，固定执行完整验证、最终确认、合并事实校验和本地审计。 |
| D-032 | 长期采用 GitHub Free 私有仓库软门禁 | `tapstate/agentic-ops` 保持私有仓库且不升级 GitHub 套餐。硬门禁可用时由 Ruleset 和 Auto-merge 强制；当前 GitHub Free 私有仓库显式使用本地 Hooks、发布脚本、固定发布分支、人工 Merge commit、合并事实校验、二次完整验证和审计组成的软门禁。软门禁不能在 GitHub 服务端阻止其它 clone、`--no-verify` 或网页操作直接修改 `main`，项目明确接受该剩余风险，不再保留套餐升级待办。 |
| D-033 | 设计确认形成工作项级连续执行授权 | 授权绑定 Jira 工作项、运行编号、AIAgent 所有权、仓库、工作分支、目标分支、范围和验证事实，覆盖实现、验证、提交、任务分支推送、必要 Jira 回写以及创建或更新拉取请求，并统一停在拉取请求审查。合并、发布、Git Tag、直接修改受保护分支、强推、历史改写、范围变化和授权失效仍单独确认。 |
| D-034 | 不引入 Web 控制台或后台常驻服务 | 标准操作通过隔离接口、Operation Contract 和项目 AI 工作空间本地文件协作；运行资料按 Jira 编号分类管理。 |
| D-035 | 授权后自动推进到 `develop` PR | 有效工作项级连续执行授权覆盖提交、任务分支推送和创建目标为 `develop` 的 PR，并统一停在 PR 审查；`master`、`main`、`develop`、`release/*` 及同类保护分支禁止自动推送，合并和发布仍单独确认。 |
| D-036 | 任务审计以本地 Jira 编号目录为当前最终位置 | `.agentic-ops/tasks/<ISSUE-KEY>/` 保存运行、审计、证据、反馈和交接材料；Jira 回写关键结论和稳定引用，后续再评估独立审计服务。 |
| D-037 | Jira `transition` 采用严格标准与受控容错 | Profile 声明稳定 ID 时必须优先使用并校验来源、目标状态；仅在没有 ID 且名称唯一、来源状态和目标状态均匹配时允许名称兜底。候选重复、目标不符、当前不可用或回读不一致时阻断；禁止模糊匹配。 |
| D-038 | 目标运行架构采用 Skill + Python Runtime + Shell Bootstrap + Rule | Skill 组织标准流程；Python Runtime 承载契约、状态、API、门禁、证据、恢复和反馈；Shell Bootstrap 只负责安装、更新、回滚、环境准备和启动；Rule 保存事实源、权限、语言、分支、授权和停止条件。Python 环境由 `uv` 和锁文件管理；命令命名后续由 D-045 细化。 |
| D-039 | 目标仓库按运行职责重新分层（目录方案已被 D-045 细化） | 本决策确认 Runtime、Bootstrap、Skill、Rule、标准资产按职责分层；D-045 进一步要求先按工作面隔离，再在工作面内按类型分层，并取代“完整 managed clone”假设。 |
| D-040 | Go 不维护双轨，由版本历史保留 | 旧版本通过版本分支、Tag 和 Git 历史查阅。Go 源码、module、平台二进制、checksum、构建测试和旧分发脚本已从现役结构删除；必须继续用 Python Runtime、标准资产和固定验收保护已提取的契约、失败码、安全门禁和 fixture，不得恢复双轨。 |
| D-041 | Jira 是实施计划与进度的唯一团队事实源 | 仓库不再新增计划文件。Jira Description 保存确认后的目标、范围、非目标、实施计划和验收标准，Comment 保存进度、阻塞和验证；现有 `plans/` 的长期事实迁入正式资料，其余由 Git 历史保留，目标结构删除顶层 `plans/`。 |
| D-042 | 源头维护与业务任务使用双运行模式（已被 D-045 的硬工作面取代） | 该决策确认两类上下文必须隔离；现役实现不再使用可切换 mode，而用目录、命令、包和 AI 入口硬隔离。 |
| D-043 | Jira 适配采用 Connection、Profile 与工作空间分层 | Jira Connection 管理站点、认证引用和 API 能力，Project Profile 管理项目字段与工作流映射，项目 AI 工作空间选择二者。Custom Field 的普通映射缺失按配置修复处理，涉及 Jira 元数据或跨项目语义才进入专题治理；Worklog 记录真实处理耗时、中文标题和明确工作内容。 |
| D-044 | 业务任务结束后可直接形成 AgenticOps 改进 PR | 业务任务优先通过人工校正确保正确完成；任务结束后 AI 基于脱敏证据总结自动化和质量问题。人工确认后离开业务工作空间，在独立 AgenticOps worktree 的 maintainer 工作面完成改进、原场景回归并创建 `develop` PR，无需重新描述问题。 |
| D-045 | 源头维护和业务研发采用两个硬隔离工作面 | 工作面命名为 `maintainer` / `developer`，命令分别为 `ao-maint` / `ao-work`。目录先按工作面再按类型分层；根 AI 入口固定 maintainer，业务项目 AI 入口固定 developer；Runtime、Skill、Rule、授权、配置、状态和测试不得交叉。不得保留 `agentic-cli` 别名或通过 `--mode` 切换；Skill 在标准 frontmatter 的 `metadata.workplane` 声明唯一工作面。 |
| D-046 | `~/.agentic-ops` 只交付 developer 工作面，阶段二起安装目录保存研发员唯一身份与凭证（由 D-048 修订） | 安装目录采用 developer-only sparse managed clone，不包含 maintainer 运行资产。阶段一（D-048 前）：研发员身份和 Jira 凭证保存在各业务项目工作空间。阶段二（D-048 落地后）：`~/.agentic-ops/user/identity.yaml` 与 `user/.env` 保存研发员唯一的 Jira 账户、Git 执行身份与凭证，业务项目工作空间只绑定项目（agent.json schema v4 持 `install_identity_ref` 指纹防错装）；旧工作空间迁移与旧字段失效阻断在正式上线后推进。项目维护必须在源头仓库或独立 worktree 中通过 `ao-maint` 完成。 |
| D-047 | 源码克隆不设超时，用流式进度、停滞提示与阶段日志保障可观测性 | 大仓库 + 慢网络下 `workspace init` 的 `git clone` 无限等待，`--progress` 输出经 stderr 实时转发给用户自行判断快慢；stderr 持续无输出超过 30s 输出停滞提示（只提示、不终止进程）；`Ctrl+C` 中断由初始化回滚清理，不残留污染。该决策修订 2026-08-17 曾选择暂不做的“阶段进度日志（方案 A）”，并取代固定 120s 克隆超时。preflight 的 `ls-remote` 等秒级 git 检查仍保留 20s 超时；失败 JSON 的 `source_checkout_failed` 增加 `stderr_tail` 诊断字段。 |
| D-048 | 中央克隆池 + Git Worktree 源码管理（多仓库分支推导） | 业务源码从「按工作空间独立克隆」改为「中央克隆池 + 任务级子工作树」。池根 `source_pool_root` 写入安装目录 `~/.agentic-ops/user/config.yaml`（必配），未配置 `workspace init` 阻断 `source_pool_root_invalid`、不做兼容回退；池成员为 `<owner>/<repo>` 普通完整克隆并保留主 checkout，浅克隆认领自动流式 `git fetch --unshallow`。任务接管时按 `<pool_root>/<jira_id>/<from_branch>/<repo>` 用 `git worktree add --detach` 挂出任务级子工作树集（from_branch 含 `/` 替换 `-`），身份用 per-worktree config + 运行时 env 双保险。多仓库通过 `repositories.list` + `analysis_mount` 挂载策略 + `branches` 推导接口（derive_from 主仓库 + 同名默认 + overrides 渐进补充）+ Jira 描述「目标仓库」section 解析，配置缺省可用、渐进补充，不阻塞实现。阶段二把身份/凭证上移安装目录并修订 D-046。 |
| D-049 | Jira 任务状态流转采用受控通用 transition 能力，maintainer 面先行（AO-23） | 两个工作面各提供 `jira transition` 命令（plan/apply/readback 同构，复用各自 WritePlan 协议），实施顺序为 maintainer（`ao-maint`）先、developer（`ao-work`）后，包独立实现不互导但 D-037 匹配规则对齐。D-037 落地为共享匹配器：稳定 ID 优先、无 ID 时名称兜底需唯一且 from/to 匹配、候选重复/目标不符/不可用/回读不一致一律阻断、禁止模糊匹配；developer 阶段 `task takeover` 改用共享匹配器且行为兼容。完成态默认禁止 AIAgent 置 `完成/Done`（无合入权），profile 可显式声明例外；maintainer 面由维护者操作不受限。快速适配路径为设计准绳：状态/transition/字段映射全部配置化（developer profile、maintainer 独立工作流映射文件），缺省可用渐进补充（未配 id 走名称兜底、maintainer 无映射可用 `--transition-id` 安全退化），映射失配时 details 输出「当前状态 + Jira 可用 transitions 完整列表 + 已配置条目 + 未映射清单」作为可直接照抄的适配材料，表单属性按名探测 + 降级记录。主链路自动推进状态作为后续增强由版本化 Skill 编排 `jira transition` 实现，本期不实现；不新增 Jira 状态。 |
| D-050 | 研发日常任务操作按「Skill 编排 + Runtime 原子能力补齐」落地（方案 A，全量拆分） | 研发工程师日常操作（看名下任务 / 查任务 / 接管 / 恢复 / 无编号自动接管）以「一句话开始工作」为体验准绳，由 AI 编排版本化 Skill + ao-work 原子命令完成。拆分任务：T2 修复 `task_worktree._run_git` 缺 git 前缀（阻塞池模式主链路）；T3 修复 `-h/--help` 不透传子命令帮助；T4 实现 `list_tasks`（`ao-work jira list`，JQL 用 `profile.task_query`，走 `/rest/api/3/search/jql`，只读）；T5 实现 `resume_takeover`（`ao-work task resume`，按 resume-takeover.yaml 契约校验链）；T6 接管简化（agent-id 从安装身份自动读取）+ 无编号自动接管（候选列表 + 研发工程师确认目标后执行，AI 不擅自选任务，授权引用门禁不弱化）；T7 AI 入口技能重构（仓库新增 `daily-task-operations` Skill，个人技能 agentic-ops 瘦身为工作面识别 + 规则指针，删除「安装位置写死 ~/.agentic-ops」表述，从 cwd 识别工作空间）。安全边界：真实 Jira 写保持授权引用必填、不新增 Jira 状态、能力目录强耦合三条硬约束不破坏、CLI 参数变更同步修正 handbook 第 5 节。决策点 D2-D8 按推荐确认（D5 候选+确认，超时未答时取风险最低默认，不放开全自动）。 |
| D-051 | developer 接管采用 `Assignee + Status + 受管 Comment + 本地状态` | 业务 Jira 的统一 takeover 操作处理新接管、接纳存量和恢复，成功类型固定为 `new_takeover`、`accept_existing_task`、`resume_takeover`，`blocked` 只作为失败结果；后两类在人可见输出和 Comment 中明文提示“不是新接管”。用户明确说“接管 <KEY>”即授权事实明确的常规接管，不再增加普通准入摘要或通用方案摘要确认；接管后连续完成信息分析和方案分级，只在设计审查、代码审查或风险决策暂停。初始接管只验证项目、Assignee、Status/transition、Agent 身份和恢复事实，`task_class`、`process_id`、目标仓库与验证方式在接管后补齐并在实现前校验。developer 不创建、映射、探测或读写 Agentic Jira Custom Field；Comment 记录可见轨迹，Status 表达团队阶段，Assignee 表达负责人，本地状态负责运行恢复和幂等。task-to-PR 以受管接管 Comment 验证正式接管。当前底层 Runtime 路径仍为 `ao-work task takeover`，顶层入口由 AO-48 收敛；当前不把 Comment 声称为并发锁，出现真实并发需求后专题设计。该决策不修改 AO maintainer 工作面的专用字段模型。 |

## 2. 当前无需决策事项

目标目录结构已经由 D-045、D-046 确认；本次重构的实施计划、进度和验收统一由 Jira `AO-11` 管理。

当前不需要决策的事项：

- 是否新增 Jira 状态。
- 是否强制 AI Review。
- 是否完整设计集成测试体系。
- 是否在已有本地 CLI 形态之外引入新的远程运行时（当前已确认不引入 Web 控制台或后台常驻服务）。

这些事项可以作为后续演进主题，但不阻塞第一阶段文档审阅。

## 3. 必须请求用户决策的触发条件

AIAgent 遇到以下情况时，必须停止并请求用户决策：

- 需要改变 AgenticOps 的产品定位或第一阶段目标。
- 需要改变源头仓库、全局安装目录或项目 AI 工作空间边界。
- 需要把设计样例升级为运行时默认配置。
- 需要新增或删除顶层目录，且会影响长期信息架构。
- 需要新增 Jira 状态、改变 Jira 工作流或改变进入迭代的前置规则。
- 需要进一步放宽人工门禁，例如向保护分支自动推送、自动合并、自动发布或自动修改规则。
- 需要把 Git / GitHub 从轻 guard 改成完整封装。
- 需要引入新的运行时形态，例如后台 daemon、Web 控制台或远程服务。

## 4. 决策记录维护规则

- 已经确认且会长期影响项目行为的结论写入“已确认决策”。
- 只影响某个项目 AI 工作空间的本地选择，不写入本文件。
- 需要用户判断但尚未决定的事项，先写入对应任务上下文，不擅自改写源头规则。
- 决策变更后，必须同步检查项目规则、当前设计、AI 员工手册、操作契约、工作流配置和故事线。
