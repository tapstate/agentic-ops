# Tapdata 开发硬规定

> 工作面：`developer`

本文是 Tapdata 项目级开发规范，供 AIAgent 执行 Tapdata Jira 任务前读取。它只记录硬规定和禁止项，不收录泛泛最佳实践。

规则类别：项目规则。本文只记录 Tapdata 项目规则；AIAgent 执行时必须同时遵守 AI 员工手册、操作契约、策略门禁和工作空间 `AGENTS.md` 中的 AIAgent 规则。

规则冲突时按 `项目规则 > AIAgent 规则 > 公司规则 > 个人规则` 执行。

## 适用范围

- 适用于 `tapdata` 项目 AI 工作空间中的 Tapdata Jira 研发任务。
- 适用于 Tapdata 业务仓库，例如 `tapdata/tapdata`、`tapdata/tapdata-web`、`tapdata/tapdata-connectors`。
- 不适用于维护 AgenticOps 源头仓库；遇到源头维护需求必须退出当前业务项目工作面，并由人工从独立的项目维护 AI 入口重新开始，不能在 developer 工作面读取、推断或执行维护规则。

## 身份与任务

- Git identity 必须使用研发工程师在本地配置中确认的姓名和邮箱；未确认或与当前执行身份不一致时必须停止。
- 单次执行只处理一个 Jira 卡片。
- 用户明确要求“接管 <KEY>”后，必须先通过 `ao-work takeover <KEY>` 完成 Jira Comment、必要 Status transition 和本地状态回读；随后连续分析任务类型、准入信息和项目准入标准。
- AIAgent 面向研发工程师、流程负责人、审阅者或 Jira 参与者的自然语言交互必须使用中文。
- Jira 人可见摘要、标题、描述、评论、工作日志、证据正文、阻塞说明、补卡说明和任务审计记录必须使用中文。
- Jira 字段名、状态名、`transition` 名称、`issue_key`、命令、配置字段、错误码、代码标识和日志关键字可以保留原始英文或缩写，但必须用中文解释结论、风险和下一步。
- 调用任何 AgenticOps 操作前必须先执行 `ao-work capability show <operation>`。只有 `implemented` 且目录列出的现役命令可以调用；目标契约和本文中的流程要求不能替代实现状态。
- 缺陷修复前，AIAgent 必须查询 `jira_inspect` 并执行 `ao-work jira inspect --issue-key <issue-key>` 读取基础 Jira 事实；当前命令不提供 Comment、Custom Field 或富门禁事实，缺失部分必须通过 Jira 界面或项目认可的只读工具补齐后再按项目准入资产判断。
- 缺陷修复准入不通过时，AIAgent 必须一次性列出全部缺失或冲突信息，结合 Jira 卡片、候选仓库和问题版本代码形成“准入分析与补卡建议”；在当前工作项授权覆盖范围内按 `jira_comment` 的受控协议写入 Jira Comment，然后停止进入实现，等待缺失事实补齐。
- 研发工程师确认补卡内容后，AIAgent 必须按 `jira_description` 的 `plan -> apply` 协议更新 Jira Description 中的问题分支、问题版本、问题现象、复现路径和验收标准，并按 `jira_comment` 协议追加补卡确认结果；完成后结束本次接管。
- 补卡后的下一次执行必须重新执行 `jira_inspect` 并补充读取 Jira Description 和 Comment 事实，重新判断准入与设计状态。不得在补卡写入后沿用旧判断；正式接管只允许顶层 `ao-work takeover <KEY>`，不能用内部 `task init` 冒充接管。

## 人机协作边界

- 研发工程师负责设计审查、代码审查、风险与范围决策，以及合并、发布等独立高风险门禁。
- AIAgent 负责在已确认的目标和边界内完成分析、设计、编码、测试、文档和证据整理。
- AIAgent 应依据 Jira、Project Profile、源码和 Runtime 证据补全缺陷描述模板、提出候选分支、模块、修复与验证方案；只有事实无法确定、存在冲突或需要取舍时进入风险决策。
- `takeover_task` 当前为 `implemented`，唯一公开入口是 `ao-work takeover [<KEY>]`。带编号调用自动判断新接管、接纳存量或恢复；后两类必须明文提示“不是新接管”。
- 接管后必须先完成代码分析并制定版本化修复计划，至少包含根因与证据、修改和不修改范围、目标模块或文件、实施步骤、测试与验收映射、风险与回滚方式。
- 完整修复设计必须展示根因证据、变更与不变范围、验证方式和逐项风险并进入设计审查。确认后形成工作项级连续执行授权；正常实现、验证、Jira 进度回写、提交、任务分支推送与 PR 创建连续推进到代码审查，不再增加准入摘要确认或通用方案摘要确认。
- 目标分支、范围、风险或核心方案发生实质变化时，必须生成新版本计划并重新确认，不得以口头补充替代 Jira 决策记录。

## Jira 信息归属

- Jira Description 只保存确认后的稳定任务契约：问题分支、问题版本、问题现象、复现路径和验收标准。
- Jira Comment 保存过程和决策轨迹：准入分析、补卡建议、确认结果、修复计划、计划变更、阻塞说明和最终证据。已有评论不得覆盖或改写。
- Jira Custom Field 目标上通过 profile 逻辑字段映射保存问题分析、修复详情和测试计划；当前 `update_task_form` 是 `capability_gap`，不得自动写入，也不得在 AIAgent 中硬编码 `customfield_*`。
- Jira Worklog 只记录真实投入时间，不保存门禁、计划、决策或证据。
- 修复完成后，Custom Field 更新由研发工程师人工完成或进入专题适配；最终证据按 `jira_comment` 现役协议以 `category=evidence` 写入。未实现的字段写入不得伪造成成功。

## 仓库与分支

### 仓库归类

TapData 多仓按分支联动关系分三类，对齐分支时据此判定：

- 联动仓（随 `tapdata` 主仓分支对齐）：`tapdata`、`tapdata-enterprise`、`tapdata-web`、`tapdata-connectors`、`tapdata-connectors-enterprise`、`tapdata-license`、`tapdata-common-lib`。
- 运维仓（`status` 可见但不联动，保持当前分支）：`tapdata-application`、`feishu_robot`。
- 单独管理（不纳入分支联动）：`tapdata-cloud`、`t-layer3-test`、`docs`、`docs-en`、`mcp-tap-server`、`solutions`、`fhir-solution`、Hazelcast(fork)、mongo(fork)。

新功能开发（问题版本为 `tapdata` 主仓 `develop`）时，产品域任务工作树必须先运行 `tap_align_branches.py plan`，以脚本结果作为各仓库实际目标分支；`profile.yaml` 的 `branches.dev_branches` 只作为不经过产品域任务工作树链路时的静态项目映射，不能覆盖 PluginKit 推导结果。

### 分支类型

`tapdata` 主仓分支分三类：

- 为主（标准）：`main`、`develop`、`release-vX.Y.Z`。
- 为辅（任务/工作分支）：`<user>/<jira_id>/<from_branch>[-<summary>]`。
- 非规范（历史遗留，按全名匹配）：`LDP-x.y`、`master`、`develop-vX.Y`、`release-X.Y`（无 `v`）等。

其它联动仓的分支命名与 `tapdata` 主仓一致。注意 `tapdata-connectors`、`tapdata-connectors-enterprise`、`tapdata-common-lib` 的 `release-v*` 是 PluginKit 版本号（v1.2.6~v2.0.x），与主仓的产品版本号（v2.x~v3.x）不是同一套数字，不能按同名对齐，必须按 pluginKit 版本推导。

### 工作分支命名

- 分支名必须包含用户名、Jira 任务编号和检出分支三项；summary 可选。
- 格式：`<user>/<jira_id>/<from_branch>[-<summary>]`。
  - `user`（必填）：GitHub 用户名，小写。
  - `jira_id`（必填）：Jira 任务编号，`TAP-xxxx`。
  - `from_branch`（必填）：检出/基准分支，规范化后写入：`develop`、`main` 原样；`release-vX.Y.Z` → `vX.Y.Z`（去掉 `release-` 前缀）；含 `/` 的分支 `/` 替换为 `-`。
  - `summary`（可选）：kebab-case 简短描述（≤ 4 词）；变更类型作前缀书写（`fix-`/`feat-`/`perf-`/`test-`/`docs-`/`chore-`/`refactor-`），不单独占位。
- 示例：`harsen/TAP-1234/develop`、`harsen/TAP-1234/v3.8.0`、`harsen/TAP-1234/develop-fix-connector-timeout`。
- 无法确认用户名、Jira 任务编号或检出分支时必须停止并请求研发工程师补齐，不得创建不可追踪分支。

### 分支对齐规则

- 业务仓库必须位于项目 AI 工作空间的 `repos/` 目录下，例如 `<project-ai-workspace>/repos/tapdata`。
- 修改前必须先更新代码。
- 不得直接提交到 `main`、`develop`、`master` 或 `release-*`；即使远程凭证允许，也不得直接推送到受保护分支；受保护分支必须走 PR 流程。
- 多仓开发必须以 `tapdata` 主仓分支为输入对齐相关仓库，不得凭直觉把所有仓库切到同名分支。
- 分支对齐通过项目脚本 `scripts/tap_align_branches.py` 执行（项目工具，非 ao-work 通用命令）：AIAgent 必须先以 `plan` 模式生成只读对齐清单，由研发工程师确认后再 `apply`；不得凭直觉切分支或声称 Runtime 已自动对齐。
- Runtime 创建产品域任务工作树时只读取 `plan --remote-only --json`，并以 `--repositories` 限定当前领域；remote-only 模式禁止回退本地同名分支或本地 PluginKit 内容，且不执行 `apply`。
- `branch_spec` 可以是 `develop`、`main`、`release-vX.Y.Z`、任务分支，或 `<tapdata>,<enterprise>,<web>` 格式；enterprise/web 分支不明确时必须显式指定或停止。Runtime 接收三段式规格时，任务路径中的「问题版本」只使用第一段 tapdata 基线分支，完整规格仅传给只读对齐计划。
- `tapdata` 为 `main` 时：所有联动仓切到 `main`。
- `tapdata` 为 `develop` 时：`tapdata-enterprise`、`tapdata-web`、`tapdata-connectors`、`tapdata-connectors-enterprise` 切 `develop`；`tapdata-common-lib` 无 `develop` 分支，按 PluginKit 推导 release，无法读取时 `UNRESOLVED`；`tapdata-license` 切 `main`。
- `tapdata` 为其它分支时：先按 `TAP-xxxx` 标记匹配；非标准分支名（非 `main`/`develop`/`release-v*`）按全名匹配同名分支；仍未命中时，`tapdata-enterprise`/`tapdata-web` 用同名分支（缺失则 `UNRESOLVED` 阻塞，不猜测）；`tapdata-common-lib`、两个 connector 分别按同一 TapData 版本的 PluginKit 推导各自仓库的 release 分支，任一仓库无法读取版本或找不到对应分支即 `UNRESOLVED`；`tapdata-license` 取版本号 ≥ 主仓分支的 release（取不到回退 `main`）。
- `tapdata-application`、`feishu_robot` 默认保持当前分支，不参与自动对齐。
- pluginKit 推导：读 `tapdata` 分支 `iengine/iengine-app/src/main/resources/pluginKit.properties` 的 `tapdata.api.verison`（源码拼写即 `verison`，按字面读，勿当 typo 改），去 `-SNAPSHOT` 得 `release-v<version>`，在各仓 `release-v*` 分支中取第一个版本 ≥ 该值的分支。
- 人工对齐脏仓库时，只有研发工程师确认后才允许按计划临时 `stash push -u`、切换分支后 `stash pop`；若 stash 或 pop 失败必须停止，不能继续跨仓切换。

### 新功能开发静态分支映射

`profile.yaml` `branches.dev_branches` 保留下列静态项目映射（确定性，不靠 AI 猜测）。产品域任务工作树创建仍以前述 `tap_align_branches.py plan` 为事实源，尤其 `tapdata-common-lib` 必须按 PluginKit 推导，不能用本表的静态回退覆盖：

| 仓库 | 新功能开发分支 |
| --- | --- |
| `tapdata/tapdata` | `develop` |
| `tapdata/tapdata-cloud` | `develop` |
| `tapdata/tapdata-common-lib` | `main` |
| `tapdata/tapdata-connectors` | `develop` 时为 `develop`；release 时按自身远端的 PluginKit release 分支推导 |
| `tapdata/tapdata-connectors-enterprise` | `develop` 时为 `develop`；release 时按自身远端的 PluginKit release 分支推导 |
| `tapdata/tapdata-enterprise` | `develop` |
| `tapdata/tapdata-license` | `main` |
| `tapdata/tapdata-web` | `develop` |
| `tapdata/hazelcast` | `release-v5.5.0` |
| `tapdata/tapdata-application` | `main` |
| `tapdata/feishu_robot` | `master` |
| `tapdata/t-layer3-test` | `develop` |
| `tapdata/docs` | `main` |
| `tapdata/docs-en` | `main` |
| `tapdata/mcp-tap-server` | `main` |
| `tapdata/solutions` | `main` |
| `tapdata/fhir-solution` | `main` |

- `dev_branches` 只在 `from_branch` 等于 `tapdata/tapdata` 声明的开发分支（`develop`）时生效；其它 `from_branch`（release 等）仍走 `same_name` 与 `overrides`。
- 未在 `dev_branches` 声明的仓库在开发分支场景回退 `same_name`（与主仓同名）。
- 推导结果必须在刷新后的 `origin` 中精确解析；同名或显式映射的远端分支不存在时以 `branch_derivation_failed` 失败关闭，不得静默回退 `default_branch`。`hazelcast` 为 fork，开发基准是 `release-v5.5.0`。

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

- 未经研发工程师对当前动作独立确认或授予仍有效的工作项级连续执行授权，不得推送。
- 未经研发工程师对当前动作独立确认或授予仍有效的工作项级连续执行授权，不得创建或更新 PR。
- 工作项级连续执行授权必须绑定当前 Jira、运行编号、仓库、工作分支、目标分支、范围和验证方式；合并、发布、范围变化或授权失效仍需新的人工确认。
- 未经代码审查人确认，不得合并。
- 未经确认，不得修改 Jira 状态为完成。
- 不得提交 secrets、tokens、private keys、原始敏感日志或完整 Jira 描述。

## 规范沉淀

- 一次任务中的临场判断不得直接升级为 Tapdata 默认规范。
- 重复出现的问题应先进入任务审计或反馈事件，再形成规范改进建议。
- 规范改进必须标明规则类别：个人规则、公司规则、项目规则或 AIAgent 规则。
- 未经研发工程师或流程负责人确认，不得自动修改项目级规范、工作流配置或默认策略。
