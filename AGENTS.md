# AgenticOps 仓库指令

## 目标文档必读

涉及设计、优化、计划、架构调整、流程调整、标准资产调整或会影响项目演进方向的变更前，必须先读取 `docs/strategy/project-goals.md`。

普通小修、测试、构建和不影响方向的局部文档措辞调整，不因本条额外增加阅读要求。

## 项目结构

核心目录约定：

- `maintainer/`：项目维护工作面，保存 `ao-maint`、维护 Runtime、维护 Skill、源头规则、故事质量门禁、发布脚本和维护测试。
- `developer/`：研发工程师工作面，保存 `ao-work`、业务 Runtime、业务 Skill、AI 执行规则、标准资产、Shell Bootstrap 和业务测试。
- `shared/`：经过明确审查、两个工作面都可读取的极少量中立资料；不得把它作为绕过隔离的公共代码区。
- `docs/`：人读文档，包括架构、规则、用户故事、流程和设计说明。

旧 Go Runtime、平台二进制、`agentic-cli`、`install-resources/` 和根目录旧运行资产已从现役结构删除；历史实现只通过版本分支、Tag 和 Git 历史查阅，不得从历史路径恢复兼容入口。

项目工作空间下的 `.superpowers/` 只保存 Superpowers 等工具的本地执行状态、检查点、临时分析和缓存，不属于项目资料，不维护、不提交，也不能作为计划、任务状态或审计事实源。正式设计必须写入 `docs/` 的对应主题目录；实施计划、进度、阻塞和验收写入 Jira，不得创建新的仓库计划文件或提交 `docs/superpowers/`。

## 项目边界与工作区隔离

当前规则只适用于 `tapstate/agentic-ops` 项目本身。不得把其它项目的研发规范、分支策略、验证命令、目录约定或历史临时规则合并进 AgenticOps 当前项目规则。

不同项目的 AI 工作空间必须分开维护。AgenticOps 源头仓库、全局安装目录 `~/.agentic-ops`、以及 `tapstate`、`tapdata` 等具体项目 AI 工作空间不能混用；只有明确标注为跨项目通用资产的规则，才可以沉淀到 AgenticOps 通用资料中。

## 工作面硬隔离

AgenticOps 只有两个工作面：`maintainer` 和 `developer`。目录先按工作面划分，再在工作面内按 `runtime`、`skills`、`rules`、`standards`、`scripts`、`tests` 等资产类型划分。

- 源头仓库必须包含内容为 `maintainer` 的 `.agentic-ops-source` 固定标记；不得用通用文档路径猜测源头身份。
- 根仓库及 AgenticOps worktree 的 AI 入口固定为本文件，并继续加载 `maintainer/AGENTS.md`；不得自动加载 `developer/AGENTS.md`、业务 Skill、业务授权或业务项目配置。
- 业务项目工作空间的 AI 入口固定由 `ao-work workspace init` 生成，只加载 `developer/AGENTS.md`、业务 Skill、业务 Rule、业务标准资产和该工作空间配置；不得加载根 `AGENTS.md`、`maintainer/`、项目目标、源头分支策略或发布流程。
- 项目维护命令只有 `ao-maint`，研发任务命令只有 `ao-work`。不得提供 `agentic-cli` 兼容别名，也不得通过 `--mode`、环境变量或聊天指令在同一进程中切换工作面。
- Python 包、Shell 入口、授权、配置、状态目录和测试必须分别归属一个工作面。两个 Runtime 不得互相导入；Skill 必须在标准 frontmatter 的 `metadata.workplane` 声明唯一工作面，不得声明多工作面。
- `~/.agentic-ops` 只安装 `developer` 工作面，采用 developer-only sparse managed clone；正常文件树中不得出现 `maintainer/` 的 Runtime、Skill、Rule、脚本、授权或配置。
- developer-only sparse managed clone 是防误入的工作树与入口隔离，不是 Git 内容权限边界；developer AI 不得通过 `.git`、`git show` 或修改 sparse 范围恢复 maintainer 资产。若要求内容级不可达，必须采用独立 developer 分发仓库或导出制品并专题验收。
- 任何确需跨工作面复用的资料必须先证明不含角色权限、工作流决策或副作用能力，再显式进入 `shared/` 并补充隔离测试；默认不共享。
- 当前工作面与命令、仓库、AI 入口或资源归属不一致时必须停止，不能由 AI 猜测、降级或跨面调用。

## 规范类型边界

当前项目维护规范只约束维护 `tapstate/agentic-ops` 源头仓库的维护者或项目维护代理，不等同于安装后 AIAgent 执行业务 Jira 任务的运行规范。

安装后 AIAgent 的执行规范必须维护在 AI 员工手册、操作契约、工作流配置、运行资产、模板和对应运行文档中。不得把当前项目的提交规则、分支规则或仓库维护流程直接套用为 AIAgent 运行期执行规范；也不得把某个业务项目的 AIAgent 执行细则反向写成 AgenticOps 当前项目维护规则。

## 规则类别与优先级

规则写入前必须先区分类别，不得把个人规则、公司规则、项目规则和 AIAgent 规则混写。

规则冲突时按以下优先级执行：

```text
项目规则 > AIAgent 规则 > 公司规则 > 个人规则
```

- 个人规则：只记录个人偏好、本机身份、个人 wiki 和本地工作流，维护在个人记忆库或本地 `~/.agentic-ops/user/`，不得写入公司或项目标准资产。
- 公司规则：只记录 TapData 跨项目硬规定、事实源边界、人工门禁、保密和通用提交要求，维护在 `developer/standards/company/`。
- 项目规则：只记录具体项目的语言、分支、提交、验证和工具例外，维护在对应项目仓库规则、项目 AI 工作空间或 `developer/standards/projects/<project>/`。
- AIAgent 规则：只记录 AIAgent 执行时的停止条件、交互语言、门禁、证据、审计和工具调用要求，维护在 AI 员工手册、操作契约、策略、运行手册、模板或当前工作空间 `AGENTS.md`。

项目规则覆盖公司规则或 AIAgent 规则时，必须能从项目规则文件或项目工作空间配置中看到明确来源；不得只依赖聊天上下文。

## 语言与命名

面向用户、研发工程师和审阅者的可见文档标题默认使用中文。产品名、角色名、工具名、命令、配置字段、协议字段、文件名、目录名和稳定编号可以保留英文或缩写。

AIAgent 面向用户、研发工程师、流程负责人、审阅者或 Jira 参与者的自然语言交互必须使用中文。

Jira 交互中的人可见内容必须使用中文，包括摘要、标题、描述、评论、工作日志、证据正文、阻塞说明、补卡说明和任务审计记录。Jira 字段名、状态名、transition 名称、issue key、命令、配置字段、协议字段、错误码、代码标识和日志关键字可以保留原始英文或缩写，但必须用中文解释结论、风险和需要人工处理的动作。

目录名和文件名默认使用英文 ASCII lowercase-kebab-case。Markdown 正文以中文为主，首次出现关键术语时可中英并列。

## 运行时方向

目标运行架构是 `Skill + Python Runtime + Shell Bootstrap + Rule`。维护工作面入口为 `./maintainer/bin/ao-maint`，研发工程师工作面入口为安装后的 `ao-work`。Python Runtime 承载契约、状态、API、门禁、证据、恢复和反馈；Shell Bootstrap 只负责认证安装引导、轻量环境检测、developer-only sparse managed clone 更新、`uv` 环境准备、启动和回滚。维护 AgenticOps 源头仓库时，`maintainer/scripts/release.sh`、`maintainer/scripts/hotfix.sh` 及 `maintainer/scripts/lib/` 可以作为项目级例外。AgenticOps 现役实现不包含 Go Runtime、项目自有平台二进制或 `agentic-cli` 兼容入口。

`~/.agentic-ops` 是稳定 `main` 的 developer-only sparse managed clone，代表一名研发员的 developer 安装并保存安装级身份与凭证，但不是具体项目运行目录。具体项目运行目录是业务项目 AI 工作空间，例如 `tapstate` 或 `tapdata`；同一安装下的工作空间继承同一研发员身份，只保存项目绑定与任务状态。

## 测试与验证

引入运行代码后，必须在同一变更中补充可执行验证命令。现役固定完整验证为：

```sh
bash maintainer/scripts/test-python-runtime.sh
bash maintainer/scripts/test-resources.sh
bash developer/tests/bootstrap/test_install_boundary.sh
bash maintainer/scripts/test-release-workflow.sh
```

这四项必须分别覆盖 maintainer/developer Runtime、命令解析、AI 入口、Skill 归属、授权与配置隔离、developer-only 安装、更新、回滚、E2E 和发布门禁。固定发布验证以 `maintainer/scripts/release.sh` 和 `maintainer/scripts/hotfix.sh` 的固化编排为准，不得替换或跳过。

所有 secrets、tokens、private keys 和原始敏感日志都不得提交。

## 分支与发布规则

- GitHub 默认分支是 `main`，日常开发分支是 `develop`。
- `main` 禁止直接提交和直接推送。版本化 `.githooks` 是 Hook 策略源，Git `core.hooksPath` 必须指向 common directory 中从已接受 `HEAD` 加载策略的 trusted launcher，不能直接执行 candidate 工作树 Hook。硬门禁模式还必须通过无 bypass 的 GitHub Repository Ruleset 要求 PR 合入、至少 1 个独立人工批准、最后推送者不能自批、dismiss stale approvals、解决全部 review threads，并禁止强推和删除；GitHub Free 私有仓库使用显式软门禁时，接受服务器端无法阻止其它入口直推和强制独立审批的剩余风险，但仍不得由本项目流程直接推送 `main` 或自动合并。
- 正常发布使用 `maintainer/scripts/release.sh prepare --version vX.Y` 对固定 HEAD 完成完整验证，验证通过后才准备本地 annotated tag；再使用 `maintainer/scripts/release.sh publish --version vX.Y` 从 `develop` 创建或复用 PR。`main` 只通过 Merge commit 合入；软门禁模式必须显式增加 `--allow-soft-gate`。
- Hotfix 使用 `maintainer/scripts/hotfix.sh create --jira-id <KEY>` 从最新 `origin/main` 创建 `<user>/<jira-id>/fix-main`，再用同一入口执行 `prepare` 和 `publish`。Hotfix `prepare` 也必须对固定 HEAD 执行完整验证；它不创建新 tag。完成后由研发工程师把修复同步回 `develop`。
- 发布脚本在执行前检查 trusted Hook launcher、远端 `develop` 和默认分支。硬门禁模式还检查 Auto-merge 和 `main` Ruleset，配置缺失时逐项引导确认，非交互配置必须显式传入 `--configure-workflow`；软门禁模式只放宽 Ruleset 和 Auto-merge，并强制检查 Merge commit 可用、固定发布 HEAD、人工合并、合并事实和二次完整验证。
- `release` / `hotfix publish` 必须从刷新后的 `origin/main` 快照执行故事门禁，candidate Hook、launcher 或 Runtime 不得自证。`origin/main` 缺少新门禁，或候选修改 Hook、故事门禁、注册表、锁文件和发布脚本等信任根时，自动 publish 必须失败关闭，改走受保护 `main` 的独立人工审查 PR。
- `publish` 只有在完整验证通过后才展示最终确认；非交互发布必须显式传入 `--confirm-release`。脚本必须等待 PR 实际合并并验证 `origin/main` 包含发布 HEAD，正常发布最后才允许推送不可变 tag。

## 提交规则

本节属于 `tapstate/agentic-ops` 项目规则，只约束维护 AgenticOps 源头仓库。

提交信息推荐格式：

```text
<type>(<scope>): <tag> <subject>
```

AgenticOps 是内部项目，提交标题和提交描述正文使用中文。

`type`、`scope`、Jira key、命令和配置字段作为结构化标识可以保留英文。`tag` 指 Jira 任务编号，例如 `TAP-1234`。TapData 公司代码提交必须绑定 Jira 任务卡片，不得省略 `<tag>`。常用类型：`Feat`、`Fix`、`Docs`、`Style`、`Refactor`、`Test`、`Chore`。

Jira 任务编号必须能从分支名、用户指令、Jira 卡片或任务上下文中确认；无法确认时必须停止并请求研发工程师补齐，不得创建无 Jira 绑定的代码提交。

`scope` 使用英文模块名、包名或目录名；没有清晰模块时可以省略。`subject` 使用中文，简洁说明本次提交做了什么，末尾不加句号。

commit body / description 使用中文，说明做了什么、解决什么问题、为什么这样做、验证结果和风险。非平凡提交必须包含 body。

在 shell 中创建非平凡提交时使用以下模板：

```bash
git commit \
  -m "<type>(<scope>): <tag> <subject>" \
  -m "<body>"
```

Git 会在两个 `-m` 之间生成一个空行。`<body>` 可以包含真实换行，但不得使用字面量 `\n` 代替换行。

每个提交只包含一个逻辑变更。不得在提交信息中粘贴完整 Jira 描述、敏感日志、凭证或未经脱敏的客户信息。

AIAgent 只有在研发工程师明确要求“提交变更”或“提交代码”，或确认版本化设计或修复计划并授予工作项级连续执行授权后，才能执行 `git commit`。只有在明确要求推送、执行发布或有效工作项级连续执行授权覆盖推送时，才能执行 `git push`。

工作项级连续执行授权必须绑定 Jira 工作项、`agentic_run_id`、目标仓库、工作分支、目标分支、修改范围和验证方式。授权范围内可以连续完成实现、验证、提交、任务分支推送、必要 Jira 回写以及创建或更新 PR，并统一停在 PR 审查；不再为每个已覆盖动作重复确认。所有权或绑定事实变化、范围或风险扩大、必要验证受阻、连续失败或外部写入结果不明确时，授权立即失效。合并、发布、Git Tag、直接修改 `main`、强推、历史改写和范围变化始终需要新的人工确认。

故事影响只在 worktree / staged 阶段预警并运行固定验收，不得在形成代码审查事实前要求确认。功能、修复和任务分支推进到 PR，向用户提供 PR 地址与当前 Head，并在 PR 上逐项审查确认事项、变更点和风险；`develop` 等非任务分支推进到本地 commit 但不推送，向用户提供提交编号并在推送前逐项审查。`impact_id` 只用于 Runtime 内部绑定 Git 指纹，不能作为用户确认主题。PR Head 或本地 commit 变化后旧确认立即失效。

日常变更只推送 `develop` 或符合规则的任务分支，不得直接推送 `main`；正式发布和 Hotfix 必须使用对应脚本并经过最终确认。若能可靠确认 Jira 编号，推送成功后应将中文变更总结评论到对应 Jira 任务；评论失败时必须明确反馈“代码已推送但 Jira 回写未完成”，后续只重试评论，不重复推送。
