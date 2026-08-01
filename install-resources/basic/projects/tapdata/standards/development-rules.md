# Tapdata 开发硬规定

本文是 Tapdata 项目级开发规范，供 AIAgent 执行 Tapdata Jira 任务前读取。它只记录硬规定和禁止项，不收录泛泛最佳实践。

规则类别：项目规则。本文只记录 Tapdata 项目规则；AIAgent 执行时必须同时遵守 AI 员工手册、操作契约、策略门禁和工作空间 `AGENTS.md` 中的 AIAgent 规则。

规则冲突时按 `项目规则 > AIAgent 规则 > 公司规则 > 个人规则` 执行。

## 适用范围

- 适用于 `tapdata` 项目 AI 工作空间中的 Tapdata Jira 研发任务。
- 适用于 Tapdata 业务仓库，例如 `tapdata/tapdata`、`tapdata/tapdata-web`、`tapdata/tapdata-connectors`。
- 不适用于维护 `tapstate/agentic-ops` 源头仓库；AgenticOps 源头仓库规则由 `AGENTS.md`、`docs/project-rules.md` 和源码发布流程约束。

## 身份与任务

- Git identity 必须使用研发工程师在本地配置中确认的姓名和邮箱；未确认或与当前执行身份不一致时必须停止。
- 单次执行只处理一个 Jira 卡片。
- 开始前必须读取 Jira 当前状态、`assignee`、任务类型、准入信息和项目准入标准。
- AIAgent 面向研发工程师、流程负责人、审阅者或 Jira 参与者的自然语言交互必须使用中文。
- Jira 人可见摘要、标题、描述、评论、工作日志、证据正文、阻塞说明、补卡说明和任务审计记录必须使用中文。
- Jira 字段名、状态名、`transition` 名称、`issue_key`、命令、配置字段、错误码、代码标识和日志关键字可以保留原始英文或缩写，但必须用中文解释结论、风险和下一步。
- 缺陷修复前，AIAgent 必须先执行 `agentic-cli inspect-task <issue-key> --workspace tapdata` 读取 Jira 事实、表单值和项目资产引用，再按项目准入资产判断卡片信息是否足够。
- 缺陷修复准入不通过时，AIAgent 必须一次性列出全部缺失或冲突信息，结合 Jira 卡片、候选仓库和目标分支代码形成“准入分析与补卡建议”；研发工程师确认真实写入后，使用 `add-task-comment` 写入 Jira Comment，然后结束本次接管。
- 研发工程师确认补卡内容后，AIAgent 必须使用 `update-task-description-sections` 更新 Jira Description 中的问题分支、修复分支、问题现象、复现路径和验收标准，并使用 `add-task-comment` 追加补卡确认结果；完成后结束本次接管。
- 补卡后的下一次执行必须重新运行 `inspect-task`，以 Jira 当前 Description、结构化字段和 Comment 事实重新判断准入与计划确认状态。不得在补卡写入后沿用旧判断自动调用 `takeover-task`。

## 人机协作边界

- 研发工程师负责确认任务目标、问题分支、修复分支、范围边界、风险和关键项目决策。
- AIAgent 负责在已确认的目标和边界内完成分析、设计、编码、测试、文档和证据整理。
- AIAgent 可以尽量补全缺陷描述模板、提出候选分支、候选模块和修复建议，但不得替研发工程师确认问题分支、修复分支、风险等级、验收边界或是否开始修改代码。
- 缺陷修复准入通过后，AIAgent 才能请求执行 `agentic-cli takeover-task <issue-key> --workspace tapdata --confirm-real-jira-write` 绑定所有权。
- 接管后必须先完成代码分析并制定版本化修复计划，至少包含根因与证据、修改和不修改范围、目标模块或文件、实施步骤、测试与验收映射、风险与回滚方式。
- 修复计划必须先使用 `add-task-comment --category plan --run-id <run-id>` 写入 Jira 并等待研发工程师确认；确认结果必须再使用 `add-task-comment --category decision` 写入 Jira。确认结果写入前不得修改代码。
- 目标分支、范围、风险或核心方案发生实质变化时，必须生成新版本计划并重新确认，不得以口头补充替代 Jira 决策记录。

## Jira 信息归属

- Jira Description 只保存确认后的稳定任务契约：问题分支、修复分支、问题现象、复现路径和验收标准。
- Jira Comment 保存过程和决策轨迹：准入分析、补卡建议、确认结果、修复计划、计划变更、阻塞说明和最终证据。已有评论不得覆盖或改写。
- Jira Custom field 通过 profile 逻辑字段映射保存问题分析、修复详情和测试计划；不得在 AIAgent 中硬编码 `customfield_*`。
- Jira Worklog 只记录真实投入时间，不保存门禁、计划、决策或证据。
- 修复完成后必须使用 `update-task-form` 更新 `issue_analysis`、`fix_details`、`verification_method`，并使用 `add-task-comment --category evidence` 写入最终证据。

## 仓库与分支

- 业务仓库必须位于项目 AI 工作空间的 `repos/` 目录下，例如 `<project-ai-workspace>/repos/tapdata`。
- 修改前必须先更新代码。
- 不得直接提交到 `main`、`develop`、`master` 或 `release-*`。
- 即使远程凭证允许，也不得直接推送到受保护分支。
- 受保护分支必须走 PR 流程。
- 创建工作分支时，分支名至少必须包含用户名、Jira 任务编号和检出分支。
- 检出分支为 `release-vX.Y.Z` 时，工作分支名中只保留 `vX.Y.Z`，不得保留 `release-` 前缀。
- 推荐工作分支格式为 `<username>/<jira-key>/<source-branch>`，例如 `harsen/TAP-1234/develop`、`harsen/TAP-1234/v3.8.0`。
- 无法确认用户名、Jira 任务编号或检出分支时，必须停止并请求研发工程师补齐，不得创建不可追踪分支。
- TapData 多仓开发必须以 `tapdata` 主仓分支为输入对齐相关仓库，不得凭直觉把所有仓库切到同名分支。
- AIAgent 应优先使用 `agentic-cli tapdata branch-align plan <branch_spec>` 生成分支对齐计划，确认无 blocked 行后才能执行 `agentic-cli tapdata branch-align apply <branch_spec>`。
- `branch_spec` 可以是 `develop`、`main`、`release-vX.Y.Z`、任务分支，或 `<tapdata>,<enterprise>,<web>` 格式；enterprise/web 分支不明确时必须显式指定或停止。
- `tapdata-application` 默认必须保持当前分支，不参与自动对齐。
- `tapdata` 为 `develop` 时，`tapdata-license` 必须切到 `main`，不得切到 `develop`。
- `tapdata-connectors`、`tapdata-connectors-enterprise`、`tapdata-common-lib` 必须根据 `tapdata` 仓库 `iengine/iengine-app/src/main/resources/pluginKit.properties` 中的 `tapdata.api.verison` 推导 release 分支；找不到满足版本的 release 分支时才允许回退 `main`。
- `tapdata branch-align apply` 允许按计划对脏仓库临时 `stash push -u`、切换分支后 `stash pop`；若 stash 或 pop 失败必须停止，不能继续跨仓切换。

## 修改范围

- 代码修改必须围绕当前 Jira 卡片。
- 不得做无关重构、格式化、依赖升级或风格清理。
- 缺陷修复必须先说明根因，再给出最小修复。
- 涉及默认配置时，必须评估是否会引入可选外部依赖、启动副作用或安全默认值变化。
- 涉及接口、存储结构、权限、任务调度、数据同步、许可证、告警或性能路径时，必须提升风险等级并请求研发工程师确认。

## 构建与依赖

- JDK、Maven profile、依赖版本、包管理器和构建命令必须以目标分支的 `pom.xml`、`package.json`、构建脚本和 CI 配置为准，不得直接沿用其它分支或历史文档中的示例。
- Tapdata 4.x 核心工程默认使用 JDK 17；目标分支声明不同版本时必须按目标分支执行，并在验证证据中记录实际 JDK 版本。
- 多仓构建必须遵循依赖方向：先构建公共库，再构建和验证核心工程、企业版或连接器等消费仓库。
- 修改 `tapdata-common-lib` 的公共 API、`tapdata-api`、`tapdata-pdk-api` 或 `plugin-kit` 版本时，必须分析并同步所有受影响消费仓库和版本属性，不得只修改单个 POM。
- 制品发布、内部仓库部署和创建 release 分支必须获得研发工程师确认，不得把本地构建成功视为发布授权。

## 测试与验证

- 修改业务逻辑必须补充或更新自动化测试。
- 缺陷修复测试必须能在修复前失败、修复后通过；无法构造失败测试时，必须说明原因和替代验证。
- 优先运行受影响模块的最小 Maven 测试；跨模块影响时扩大到相关模块。
- `-DskipTests` 只允许用于临时打包或诊断，不得作为任务验证证据；使用后仍必须执行与变更范围匹配的测试。
- 测试无法运行、依赖缺失或环境不满足时，不得声称已验证，必须写明阻塞。
- 不得把“编译通过”替代“缺陷已验证”。

## 提交

- 提交信息必须使用英文，一个提交只包含一个 Jira 卡片或一个独立标准资产变更。
- `tag` 指 Jira 任务编号，例如 `TAP-1234`。
- 提交格式必须是 `<type>(<scope>): <tag> <subject>`，不得省略 `<tag>`。
- 允许的 `type`：`Feat`、`Fix`、`Docs`、`Style`、`Refactor`、`Test`、`Chore`。
- `scope` 使用英文模块名、包名或目录名；没有清晰模块时可以省略。
- 从分支名、用户指令、Jira 卡片或任务上下文提取 `TAP-1234` 风格 tag；无法确认 Jira 任务编号时必须停止并请求研发工程师补齐。
- `subject` 使用英文祈使句，末尾不加句号。
- 非平凡提交必须包含 body，说明 root cause、change、verification 和 risk。
- 不得在提交信息中粘贴完整 Jira 描述、敏感日志、凭证或未经脱敏的客户信息。

## 高风险动作

- 未经研发工程师确认，不得推送。
- 未经研发工程师确认，不得创建或更新 PR。
- 未经代码审查人确认，不得合并。
- 未经确认，不得修改 Jira 状态为完成。
- 不得提交 secrets、tokens、private keys、原始敏感日志或完整 Jira 描述。

## 规范沉淀

- 一次任务中的临场判断不得直接升级为 Tapdata 默认规范。
- 重复出现的问题应先进入任务审计或反馈事件，再形成规范改进建议。
- 规范改进必须标明规则类别：个人规则、公司规则、项目规则或 AIAgent 规则。
- 未经研发工程师或流程负责人确认，不得自动修改项目级规范、工作流配置或默认策略。
