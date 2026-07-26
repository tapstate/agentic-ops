# Tapdata 开发硬规定

本文是 Tapdata 项目级开发规范，供 AIAgent 执行 Tapdata Jira 任务前读取。它只记录硬规定和禁止项，不收录泛泛最佳实践。

## 适用范围

- 适用于 `tapdata` 项目 AI 工作空间中的 Tapdata Jira 研发任务。
- 适用于 Tapdata 业务仓库，例如 `tapdata/tapdata`、`tapdata/tapdata-web`、`tapdata/tapdata-connectors`。
- 不适用于维护 `tapstate/agentic-ops` 源头仓库；AgenticOps 源头仓库规则由 `docs/project-rules.md` 和 `docs/development-phase-rules.md` 约束。

## 身份与任务

- Git identity 必须使用研发负责人在本地配置中确认的姓名和邮箱；未确认或与当前执行身份不一致时必须停止。
- 单次执行只处理一个 Jira 卡片。
- 开始前必须读取 Jira 当前状态、`assignee`、任务类型、验收要求、目标仓库和验证方式。
- Jira 人可见标题、描述、评论、工作日志、证据正文、阻塞说明和补卡说明必须使用中文。
- 缺少验收标准、目标仓库、验证方式或权限时必须停止，请研发负责人补齐。

## 仓库与分支

- 业务仓库必须位于项目 AI 工作空间的 `repos/` 目录下，例如 `<project-ai-workspace>/repos/tapdata`。
- 修改前必须先更新代码。
- 不得直接提交到 `main`、`develop`、`master` 或 `release-*`。
- 即使远程凭证允许，也不得直接推送到受保护分支。
- 受保护分支必须走 PR 流程。
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
- 涉及接口、存储结构、权限、任务调度、数据同步、许可证、告警或性能路径时，必须提升风险等级并请求研发负责人确认。

## 测试与验证

- 修改业务逻辑必须补充或更新自动化测试。
- 缺陷修复测试必须能在修复前失败、修复后通过；无法构造失败测试时，必须说明原因和替代验证。
- 优先运行受影响模块的最小 Maven 测试；跨模块影响时扩大到相关模块。
- 测试无法运行、依赖缺失或环境不满足时，不得声称已验证，必须写明阻塞。
- 不得把“编译通过”替代“缺陷已验证”。

## 提交

- 提交信息必须使用英文并写详细。
- 提交格式必须是 `<type>(<scope>): <tag> <subject>`。
- 允许的 `type`：`Feat`、`Fix`、`Docs`、`Style`、`Refactor`、`Test`、`Chore`。
- 从分支名或 Jira key 提取 `TAP-1234` 风格 tag。
- 存在 tag 时 footer 必须添加 `Refs: <tag>`。
- 非平凡提交必须包含 body，说明根因、修复方式、验证和风险。

## 高风险动作

- 未经研发负责人确认，不得推送。
- 未经研发负责人确认，不得创建或更新 PR。
- 未经代码审查人确认，不得合并。
- 未经确认，不得修改 Jira 状态为完成。
- 不得提交 secrets、tokens、private keys、原始敏感日志或完整 Jira 描述。

## 规范沉淀

- 一次任务中的临场判断不得直接升级为 Tapdata 默认规范。
- 重复出现的问题应先进入任务审计或反馈事件，再形成规范改进建议。
- 规范改进必须标明层级：公司级、项目级或 AIAgent 执行级。
- 未经研发负责人或流程负责人确认，不得自动修改项目级规范、工作流配置或默认策略。
