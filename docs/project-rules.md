# AgenticOps 项目规则

## 1. 目的

本文定义 AgenticOps 必须遵守的项目规则。规则用于约束 AgenticOps 源码、文档、Skill、Python Runtime、Shell Bootstrap、Rule、标准资产、项目 AI 工作空间和反馈闭环。

相关设计文档：

- `docs/architecture/agenticops-current-design.md`
- `docs/user-stories/agenticops-user-stories.md`
- `docs/user-stories/project-maintainer-stories.md`
- `docs/user-stories/development-engineer-stories.md`
- `docs/development-style.md`
- `docs/ai-working-rules.md`
- `docs/configuration-standards.md`
- `docs/processes/standard-process-registry.md`
- `docs/strategy/positioning.md`
- `docs/runtime/cli-runtime.md`
- `docs/runtime/python-runtime.md`
- `docs/runtime/problem-resolution-and-update.md`
- `docs/templates/evidence-templates.md`
- `docs/examples/end-to-end-demo.md`

## 2. 产品边界

AgenticOps 是把公司员工执行标准沉淀成 AI 可执行标准流程的 AI 执行控制体系。

AgenticOps 先以研发 Jira 任务为主要落地场景：帮助研发工程师操作 AIAgent 从 Jira 接管任务到完成任务。不同任务必须先分类，再进入对应标准流程。AgenticOps 必须通过 Standard Process Registry、AI 员工手册、操作契约、Task Form Standard、工作流配置、策略门禁、运行手册、模板、事件日志、证据和反馈报告管理这些流程差异，让执行过程可恢复、可复盘、可分析，并把关键状态、关键信息、表单数据和审查结论回写到正确位置。

AgenticOps 必须遵守：

- 不替代 Jira。
- 不替代研发工程师。
- 不替代 拉取请求审查。
- 不创建新的任务管理事实源。
- 不以绕过人工授权、专业审查和策略门禁的全自动开发作为目标。
- 不把某个具体 Jira 工作流硬编码为核心模型。
- 不把所有任务强行压成同一条固定执行流程。
- 不跳过任务分类直接执行开发。
- 不依赖员工记住所有标准流程细节。
- 不绕过研发工程师、代码审查人、QA、运维、安全等专业角色在对应节点的审查责任。
- 不把尚未成熟的流程判断直接固化为脚本或 CLI 命令。
- 不把功能绑定特定 AIAgent 的特性：Python Runtime、操作契约、工作流配置、Skill 和标准资产不得依赖某个具体 AI 产品、模型或推理引擎的专有能力、工具调用格式或交互协议。确需 AIAgent 参与的能力（如模型调用、工具执行、上下文编排），必须通过适配层按 AIAgent 类型切换应用；通用逻辑不得包含 AIAgent 专属分支。

AgenticOps 的主链路必须真实、可控、可复用：

```text
Jira 卡片已进入迭代
-> 研发工程师手动触发 AI
-> AI 拉取 负责人名下待办
-> 研发工程师选择一个卡片
-> AI 识别任务分类并选择标准流程
-> AI 执行任务接管门禁
-> AI 生成 `agentic_run_id` 和接管记录
-> AI 形成版本化设计或修复计划
-> 研发工程师确认计划并授予工作项级连续执行授权
-> AI 实现、验证、提交、推送任务分支并回写必要 Jira 证据
-> AI 创建或更新拉取请求并输出拉取请求审查包
-> 进入既有 CI / 审查 / 合入流程
```

## 3. 文档与计划边界

AgenticOps 文档必须按职责分层：

- `README.md` 只承担终态定位、核心模型、角色入口和稳定目录导航，不记录阶段性成果清单。
- `docs/architecture/` 定义稳定架构边界，包括流程环节、门禁、状态、容错、事实源、角色责任、安全边界、能力边界和标准资产演进机制。
- `docs/user-stories/` 定义故事线，包括主角、目标、触发方式、关键输出、失败路径和验收口径；故事线不记录实施计划或当前完成度。
- `docs/runtime/` 记录稳定运行时设计、命令能力、操作说明和运行边界，不记录阶段性任务、当前完成度或剩余工作。
- Jira 基于稳定架构管理可交付目标、实施步骤、阶段状态、阻塞和验收；Description 保存确认计划，Comment 保存过程轨迹。
- 项目工作空间 `.superpowers/` 只保存工具本地执行状态、检查点、临时分析和缓存，由 Git 忽略；不得保存或提交正式设计、计划、规范和运行资产。
- 工具建议使用 `docs/superpowers/` 等默认路径时，必须服从本仓库分层：正式设计进入 `docs/` 对应主题目录，实施计划进入 Jira，临时草稿留在被忽略的 `.superpowers/`。
- 阶段性范围、阶段任务、当前状态、验收命令、剩余工作和实现说明写入 Jira，不得混入 README、架构设计、项目规则、故事线或运行时设计。
- 设计文档只维护终态设计事实、能力边界、风险和约束，不记录阶段性推进信息。
- 设计文档发现缺口时，只能说明能力边界、风险和约束；如果缺口背后涉及产品、流程、权限或事实源取舍，必须明确标记为需要用户决策，不得伪装成默认计划或默认实现。

阶段性文字必须按职责分类处理：

- 终态原则：影响 AgenticOps 长期形态、事实源、角色责任、门禁或安全边界的规则，保留在设计、规则、手册或契约文档中，不使用阶段限定弱化规则。
- 阶段执行限制：只在某个实施期间成立的限制，保留在对应 Jira 工作项中。
- 当前实现缺口：只说明当前版本尚未完成的能力，写入对应 Jira，不写入目标、架构、规则、故事线或运行时设计主叙事。
- 判断一句话归属时，先问它是在定义 AgenticOps 终态形态，还是只解释当前阶段先做什么、暂不做什么；后者必须进入 Jira。

做任何计划前，必须先确认其所依赖的故事线和架构文档已经存在且相对稳定。故事线不清时，应先确定故事线；架构不清时，应先更新或补齐架构；不得直接用零散功能点堆砌计划。

## 4. 事实源

AgenticOps 必须保持事实源边界清晰：

- Jira 是任务、需求、负责人、迭代、状态、评论和执行证据的事实源。
- Git 仓库是代码、测试、提交和分支的事实源。
- GitHub 拉取请求与 CI 是拉取请求审查、CI、审查评论和合入记录的事实源。
- AgenticOps 只提供执行控制、操作契约、证据模板和反馈闭环。

`agentic_run_id` 只用于追踪一次 AI 执行：

- 不替代 Jira 卡片编号。
- 不替代 Jira 状态。
- 不要求研发工程师手工填写。
- 必须能串联 Jira 证据、事件日志、测试结果、拉取请求和反馈分析。

developer 任务所有权由 Jira 负责人和本地运行身份共同控制：

- `agent_id` 标识一个 AIAgent 身份，同一个 `agent_id` 可以产生多个 `agentic_run_id`。
- Jira `Assignee` 标识当前负责人，受管 Comment 记录接管、恢复和终态轨迹，本地 task state 记录当前 `agentic_run_id` 和恢复点。
- developer 不创建、映射、探测或读写 Agentic Jira Custom Field，也不把 Comment 声称为跨工作空间并发锁。
- 任务完成或明确交接结束后，必须写入并回读中文终态 Comment、关闭本地 run；历史 `agentic_run_id` 继续保留用于审计。
- AO maintainer 工作面的 Agentic 字段和并发控制由其独立规则约束。

执行记录必须覆盖：

- 当前任务类型 `task_type`。
- 当前任务分类 `task_class` 和标准流程编号 `process_id`。
- 当前阶段 `current_stage`。
- 下一步动作 `agentic_next_action`。
- 人工门禁状态。
- 当前节点表单状态。
- 专业审查结论。
- 重试和重做依据。
- 关键输入、关键输出和关键失败原因。
- 已回写的位置，例如 Jira 证据、拉取请求评论、项目 AI 工作空间日志或反馈报告。

标准流程出问题时，处理优先级必须是：

- 能按 AI 员工手册、操作契约、工作流配置、策略、运行手册或模板自助处理的，优先自助处理。
- 缺少 Jira 关键字段或上下文时，阻断接管并输出补全动作和模板。
- 标准资产不适配时，生成工作流配置、策略、模板或运行手册的改进建议。
- 存在风险、权限不足、标准冲突或连续失败时，转人工确认。
- 只有确认问题来自对应工作面的 Runtime 逻辑时，才进入 `ao-maint` 或 `ao-work` 的修复路径；不得跨工作面临时调用。

## 5. 仓库边界

当前只有一个公司仓库作为 AgenticOps 的权威源头：

```text
git@github.com:tapstate/agentic-ops.git
```

该仓库管理全局通用资料：

```text
maintainer/    项目维护工作面：ao-maint、Runtime、Skill、Rule、故事门禁、发布和测试
developer/     研发工程师工作面：ao-work、Runtime、Skill、Rule、标准、Bootstrap 和测试
shared/        经明确审查允许跨面读取的中立资料，默认不共享代码或规则
docs/          人读文档，包括架构、目标定位、故事线、流程和设计
.superpowers/  本地执行状态、检查点、临时分析和缓存，不提交
```

旧 Go Runtime、平台二进制、`agentic-cli`、`install-resources/` 和根目录旧运行资产已从现役仓库结构删除。历史实现只通过版本分支、Tag 和 Git 历史查阅，不得作为兼容入口或发布资产恢复。

仓库内文档、目录和脚本文件名默认使用英文 ASCII lowercase-kebab-case。面向用户的正文优先使用中文。

同一个仓库内先按 `maintainer` / `developer` 工作面隔离，再按资产类型划分；不使用不同分支分管源码、设计、计划或运行资产。正式交付时通过稳定 `main` 的 developer-only sparse managed clone 只提供 developer Skill、Rule、标准资产、Python Runtime 和 `ao-work`。

根 `AGENTS.md` 固定进入 maintainer 并加载 `maintainer/AGENTS.md`；业务项目工作空间 `AGENTS.md` 固定进入 developer。命令分别为 `ao-maint` 和 `ao-work`，不得提供兼容别名或 `--mode` 切换。两个 Python 包、Skill、授权、配置、状态和测试不得交叉；Skill 必须在 `metadata.workplane` 声明唯一工作面。

当前项目规则只适用于 `tapstate/agentic-ops` 项目本身。不得把其它项目的研发规范、分支策略、验证命令、目录约定或上线前临时规则合并进 AgenticOps 当前项目规则。

不同项目的 AI 工作空间必须分开维护。AgenticOps 源头仓库、全局安装目录 `~/.agentic-ops`、以及 `tapstate`、`tapdata` 等具体项目 AI 工作空间不能混用；只有明确标注为跨项目通用资产的规则，才可以沉淀到 AgenticOps 通用资料中。

当前项目维护规范只约束维护 `tapstate/agentic-ops` 源头仓库的维护者或项目维护代理，不等同于安装后 AIAgent 执行业务 Jira 任务的运行规范。

安装后 AIAgent 的执行规范必须维护在 AI 员工手册、操作契约、工作流配置、运行资产、模板和对应运行文档中。不得把当前项目的提交规则、分支规则或仓库维护流程直接套用为 AIAgent 运行期执行规范；也不得把某个业务项目的 AIAgent 执行细则反向写成 AgenticOps 当前项目维护规则。

## 6. 规则类别与优先级

AgenticOps 中所有规则写入前必须先区分类别，不能因为同一条规则会被 AIAgent 读取，就把个人偏好、公司硬规定、项目例外和 AIAgent 执行要求写在同一层。

规则冲突时按以下优先级执行：

```text
项目规则 > AIAgent 规则 > 公司规则 > 个人规则
```

- 个人规则：个人偏好、本机身份、个人 wiki 和本地工作流，只能维护在个人记忆库或本地 `~/.agentic-ops/user/`，不得写入公司或项目标准资产。
- 公司规则：TapData 跨项目硬规定、事实源边界、人工门禁、保密、审查职责和通用提交要求，位于 `developer/standards/company/`。
- 项目规则：具体项目的语言、分支、提交、验证、工具和流程差异，维护在项目仓库规则、项目 AI 工作空间或 `developer/standards/projects/<project>/`。
- AIAgent 规则：AIAgent 执行时的停止条件、交互语言、门禁、证据、审计和工具调用要求，维护在 AI 员工手册、操作契约、策略、运行手册、模板或当前工作空间 `AGENTS.md`。

项目规则覆盖公司规则或 AIAgent 规则时，必须在项目规则文件或项目工作空间配置中显式体现来源；不得只依赖聊天上下文。个人规则只能在缺少更高优先级规则时补充执行偏好，不能覆盖项目、AIAgent 或公司规则。

## 7. 配置规范

AgenticOps 配置项必须按 [配置规范](configuration-standards.md) 维护。新增、调整或删除配置时，必须先确认配置分类、来源优先级、密钥落点、统一读取入口、初始化表单、帮助信息、操作契约、上手文档和测试是否同步更新。

任何绕过统一配置模块、让功能直接解析 YAML / `.env`、新增第二个 token 名称、把 secret 写入 YAML，或让文档与 CLI 行为不一致的调整，都必须先停止实现，输出审查分析和推荐方案，等待研发工程师决策。

## 8. 源头仓库分支与发布规则

`tapstate/agentic-ops` 的 GitHub 默认分支必须是 `main`，日常开发必须在 `develop` 进行。`main` 不允许直接提交，正常发布不允许直接推送；版本化 `.githooks` 是策略源，Git 必须通过 common directory trusted launcher 加载已接受 `HEAD` 的 Hook，不能直接执行 candidate 工作树 Hook。正常发布只允许 PR 的 Merge commit。硬门禁模式还必须通过无 bypass 的 GitHub Repository Ruleset 禁止直接推送、强推和删除，并要求至少 1 个独立人工批准、最后推送者不能自批、dismiss stale approvals、解决全部 review threads；GitHub Free 私有仓库使用显式软门禁时，接受服务器端无法阻止其它入口直推和强制独立审批的剩余风险。Hotfix 是唯一脚本化直推例外，边界见下文。

正常发布必须使用统一入口：

```sh
maintainer/scripts/release.sh prepare --version vX.Y
maintainer/scripts/release.sh publish --version vX.Y
```

`prepare` 先固定 HEAD，并完整验证 Python、两个工作面、Skill、Rule、标准资产、developer-only Bootstrap 和安装边界；软门禁模式在验证通过后创建或复用本地固定 `release/vX.Y` 分支，但绝不创建 Tag。它不构建项目自有平台二进制，不暂存、不提交、不推送。研发工程师审查后，`publish` 必须再次执行完整本地验证并取得最终确认。硬门禁模式推送 `develop`，创建或复用 `develop -> main` PR，启用 Merge Auto-merge；软门禁模式只允许使用 prepare 固定的 `release/vX.Y -> main` PR，并在创建后每 5 秒轮询状态，最多等待 30 分钟。研发工程师仍在 GitHub 执行 Merge commit，检测到合并后脚本自动再次完整验证；传入 `--no-wait-for-merge` 时保留返回状态码 `2`、人工合并后以同一命令续跑的方式。两种模式都必须验证 `origin/main` 包含固定发布 HEAD；确认实际 Merge commit 后，脚本必须先将 `develop` 快进到已验证的 `origin/main`，快进不成立即失败关闭，最后才创建且推送指向该 Merge commit 的不可变 tag。

紧急修复必须使用统一入口：

```sh
maintainer/scripts/hotfix.sh publish --jira-id <KEY>
```

Hotfix 只能从干净且与 `origin/develop` 精确同步的本地 `develop` 执行。Jira key 为必填 Git 审计标识，脚本不读取、不修改、不评论 Jira，也不调用 `gh`。脚本不创建修复分支、PR 或 Tag，不执行完整发布验证，不等待额外人工确认；调用命令本身就是本次快速修复授权。脚本以固定的 `origin/main` 和 `origin/develop` 自动计算合并 tree，以两者为父提交生成带 Jira key 的 Merge commit，原子推送到远端 `main` 和 `develop`，再同步本地 `develop` 并回读。自动合并冲突、未同步、脏工作区或原子推送失败时关闭，禁止交互式冲突处理、rebase、cherry-pick、强推或部分更新。

正常发布的 `prepare` 和 `publish` 必须在临时 worktree 中固定执行以下完整验证；Hotfix 不在执行期运行这些发布验证，但 Hotfix 实现本身必须由 `test-release-workflow.sh` 回归覆盖：

```sh
bash maintainer/scripts/test-python-runtime.sh
bash maintainer/scripts/test-resources.sh
bash developer/tests/bootstrap/test_install_boundary.sh
bash maintainer/scripts/test-release-workflow.sh
```

正常发布验证命令不得通过参数替换或跳过；验证失败和最终确认前不得产生远端写入。非交互正常发布必须显式传入 `--confirm-release`。

脚本必须在执行前检查 trusted Hook launcher、远端 `develop` 和 GitHub 默认分支。硬门禁模式还检查 Auto-merge 和 `main` Ruleset，发现缺失或漂移时应逐项展示并取得确认后幂等修复；非交互配置必须显式传入 `--configure-workflow`。软门禁只放宽 Ruleset 和 Auto-merge，仍强制检查 Merge commit 可用、固定发布或修复 HEAD、人工合并、合并事实和二次完整验证。publish 还必须用刷新后的 `origin/main` Runtime 检查固定 candidate；基线缺失或信任根发生净变更时，自动 publish 失败并改走受保护 `main` 的独立人工审查 PR。PR 和 Merge commit 是发布事实源，`.local/release-runs/` JSON 是本地执行审计。

## 9. 安装边界

AgenticOps 默认安装到：

```text
~/.agentic-ops
```

`~/.agentic-ops` 是稳定 `main` 的 developer-only sparse managed clone，代表一名研发员的 developer 安装并保存安装级身份与凭证，但不是具体项目或任务运行目录；正常文件树不得包含 `maintainer/`。

`~/.agentic-ops` 可以保存：

- 已安装的 `bin/ao-work`、developer 运行资产和 `.local/` 安装元数据。
- 全局配置。
- 本机个人配置目录 `user/`；该目录必须保持 local-only，不得提交。
- 通用 AI 员工手册。
- 通用 skills。
- 通用 templates。
- 操作契约。
- 可安全重建的缓存。

`~/.agentic-ops` 不得保存：

- 具体业务任务的长期上下文。
- 业务仓库代码变更。
- 未脱敏的原始 Jira 内容。
- 未脱敏的测试日志。
- 提交资产中的 secrets、tokens、private keys；本机个人配置如需使用凭据，优先引用环境变量或系统凭据，不得输出到日志、事件或提交内容。

安装入口约定为：

```sh
(
  set -e
  bootstrap="$(gh api -H 'Accept: application/vnd.github.raw' \
    '/repos/tapstate/agentic-ops/contents/developer/bootstrap/install.sh?ref=main')"
  printf '%s\n' "$bootstrap" | bash
)
```

安装前必须通过 `gh auth status` 确认 GitHub CLI 已登录并具备访问 `tapstate/agentic-ops` 私有仓库的权限；未登录时先执行 `gh auth login -h github.com -p ssh -s repo`。GitHub API contents 路径必须加引号，避免 zsh 把 `?ref=main` 当作通配符；必须先完整取得脚本并检查 `gh api` 成功，再交给 `bash`，禁止把 404 等错误响应直接管道执行。产品安装与更新固定使用受信 `tapstate/agentic-ops` 和稳定 `main`，不提供仓库或分支覆盖入口；遗留的身份覆盖环境变量只要非空就必须阻断。离线发布测试必须使用隔离 `PATH` 的 Git wrapper，只对 `fetch`、`push` 和非 `--get-url` 的 `ls-remote` 单进程注入 `-c url.<fixture>.insteadOf=<official>`；不得向仓库或 HOME 持久化 rewrite。产品脚本和 Runtime 始终看到并校验官方 origin，不得为测试保留第二套受信身份。

安装脚本必须支持 Linux 和 macOS，并且不得覆盖用户已有本地配置。它必须用 sparse checkout 把 developer 工作面更新到 `~/.agentic-ops`，用锁文件准备 Python 环境并生成 `bin/ao-work`；不得检出 maintainer 运行资产，也不构建项目自有平台二进制。

如果 `~/.agentic-ops` 已存在，安装脚本必须先展示当前 ref 和目标分支，并要求研发工程师确认后才更新。交互式终端由用户输入确认；非交互环境必须先取得用户确认，再显式设置 `AGENTIC_OPS_ASSUME_YES=1`。未确认时安装脚本必须停止，不能静默更新。

## 10. 项目 AI 工作空间边界

具体项目的运行目录必须是对应项目 AI 工作空间，例如：

```text
tapstate/
tapdata/
```

不同项目 AI 工作空间可以对应不同：

- Jira 空间。
- GitHub organization。
- GitHub repositories。
- 本地源码目录。
- 工作流配置。
- 任务执行上下文。
- feedback 记录。

具体工作空间产物必须写入项目 AI 工作空间、目标业务仓库、Jira / PR 证据，或受控的任务执行记录位置。

项目 AI 工作空间运行资料按 Jira 编号分类管理：

```text
<project-ai-workspace>/
  .agentic-ops/
    tasks/
      <ISSUE-KEY>/
        runs/
        audit/
        feedback/
        handoff/
```

## 11. AI 员工手册规则

AgenticOps 必须包含 AI 员工手册，并将其作为一等交付物。

AI 员工手册必须同时服务：

- AIAgent：明确任务类型、当前阶段、下一步动作、工具、流程、门禁、证据和停止条件。
- 研发工程师：提供快捷操作方式，让研发能用自然语言或 CLI 指挥 AI 完成任务。

AI 员工手册必须覆盖：

- 任务类型：安装、工作空间初始化、AIAgent 初始化、新任务接管、恢复接管、拉取请求审查意见修复、任务完成审计、AgenticOps 改进建议。
- 任务分类：需求变更、缺陷修复、技术任务、排查分析和流程改进等标准分类。
- 阶段模型：`已接收`、`预检中`、`等待接管`、`分析中`、`开发中`、`验证中`、`证据回写中`、`等待人工确认`、`阻塞`、`已交接`。
- 下一步动作：由操作契约、工作流配置、当前证据和人工门禁共同决定。
- 工作入口：拉待办、任务接管、继续失败任务、修复拉取请求审查意见、回写证据。
- 行为边界：无独立确认或有效工作项授权时不推送、不创建拉取请求；不自动合并、不扩大需求范围、不泄露敏感信息。
- 停止条件：需求不清、风险扩大、权限不足、测试无法运行、连续修复失败、需要人工判断。
- 交付要求：代码差异、测试结果、风险说明、Jira / 拉取请求证据、下一步建议。

所有技能、操作契约、工作流配置、CLI 命令和证据模板必须与 AI 员工手册保持一致。

## 12. 操作契约规则

AgenticOps 必须通过操作契约管理 AIAgent 可执行操作的输入、输出、失败模型和副作用。

AIAgent 不应直接面对 Jira 字段、Jira 状态、Jira `transition` 或 Jira 评论模板。AIAgent 必须面向稳定操作工作。

核心操作包括：

```text
install
workspace_init
agent_init
list_tasks
takeover_task
resume_takeover
read_task_context
write_evidence
mark_blocked
request_owner_confirmation
prepare_pr
fix_pr_comments
feedback_collect
feedback_analyze
feedback_report
feedback_propose
```

每个操作契约必须定义：

- 操作名。
- 契约版本。
- 操作意图。
- 适用的任务类型。
- 允许执行该操作的阶段。
- 完成后建议的下一步动作。
- 结构化输入。
- 前置门禁。
- 结构化输出。
- 稳定失败码。
- 人工动作建议。
- 副作用。
- 是否需要人工确认。

写操作必须声明副作用。任何涉及 Jira 写入、Git 提交、Git 推送、GitHub 拉取请求创建或拉取请求更新的操作必须经过策略、门禁和人工确认检查。

## 13. 工作流配置规则

AgenticOps 核心绑定研发流程语义，不绑定某一套具体 Jira 工作流。

工作流配置负责把 操作契约映射到具体项目流程。

工作流配置必须能表达：

- Jira `base_url`、Jira 项目、JQL。
- Jira Form Mapping，把 `owner`、`sprint`、`acceptance_criteria`、`target_repo`、`risk` 等 AgenticOps 标准字段映射到具体 Jira 字段、描述模板、评论模板或工作空间配置。
- Jira 状态和 `transition` 映射。
- 专业审查节点和对应角色，例如研发工程师、代码审查人、QA、运维或安全。
- 每个关键阶段允许重试还是必须重做前序表单。
- GitHub 组织和代码仓库映射。
- 本地源码路径。
- 允许的写操作。
- 人工确认点。
- 证据模板。

TapData / TapState 方案 C 可以作为第一套默认工作流配置，但不得硬编码进核心模型。

## 14. CLI 运行时规则

控制层必须采用本地优先的 Python Runtime。Skill 负责组织标准流程，Rule 负责保存不能由当前任务临场改变的约束。

Shell Bootstrap 只用于安装、更新、回滚、环境准备和启动，例如 `gh api | bash` 的 `developer/bootstrap/install.sh`。安装后 AIAgent 的业务逻辑、操作、策略、适配器、日志和反馈分析不得写在 shell 中。维护 AgenticOps 源头仓库时，`maintainer/scripts/release.sh`、`maintainer/scripts/hotfix.sh` 和 `maintainer/scripts/lib/` 是项目级例外。

工作面入口为：

```sh
./maintainer/bin/ao-maint
ao-work
```

developer 安装位置：

```text
~/.agentic-ops/bin/ao-work
```

CLI 必须遵守：

- stdout 只输出结构化 JSON。
- stderr 输出人类诊断日志。
- 所有失败返回稳定 `code`。
- 退出码有固定语义。
- 写操作必须检查策略、门禁和人工确认。
- secrets 不允许出现在 stdout、stderr 或事件日志中。
- 本地任务状态必须使用版本化 JSON / NDJSON、任务级锁和原子替换，不能由 shell 文本命令直接修改。
- 外部写操作必须遵守 `plan -> apply -> readback`，结果不明确时阻断，不得猜测成功。
- Linux 和 macOS 必须通过仓库锁定的同一 Python 主链路运行，不构建 AgenticOps 自有平台二进制。

Bootstrap 允许依赖 `bash`、`curl`、Git 和 `uv`。Python 版本由 `.python-version` 固定，依赖分别由 `maintainer/pyproject.toml`、`developer/pyproject.toml` 和各自 `uv.lock` 锁定；运行时不得依赖业务项目自己的 Python 环境。

`ao-work` 的前置检查必须验证 GitHub、Jira 授权、工作流配置、当前业务仓库和 developer 工作面；`ao-maint` 必须验证 AgenticOps 源头仓库、maintainer AI 入口和维护规则。两者不得通过 mode 参数互换。

CLI 操作和脚本入口必须遵守成熟度边界：

- 成熟固化的交互逻辑可以沉淀为原子化操作。
- 安装后脚本入口只做受控编排或调用，不承载 AIAgent 的 Jira、GitHub、Git、策略、门禁、证据或反馈业务判断；源头仓库发布脚本按第 8 节例外执行。
- 原子操作必须输入输出稳定、失败码明确、副作用可审计。
- 原子操作必须能说明失败后应重试、重做、阻断还是转人工。
- 尚未稳定的流程判断必须先进入运行手册、工作流配置、策略草案或反馈建议。
- 框架负责大的流程环节、门禁、状态和演进机制，不把每个任务的临场细节写死。
- AIAgent 在具体环节内执行任务并沉淀经验，周期性复盘再决定是否固化为标准资产。

## 15. Git 和 GitHub 规则

GitHub / Git 当前不会替换，因此不需要做可替换平台级抽象，但必须做安全操作级封装。

AIAgent 可以直接读取：

```text
git status
git diff
git log
git show
```

以下动作必须通过 AgenticOps 操作或 CLI 防护管控：

```text
git commit
git push
git merge
git rebase
git clean
gh pr create
gh pr edit
```

推送、创建拉取请求、重新提交修复和合并都属于人工门禁，只有在研发工程师明确授权的范围内才能执行。研发工程师确认版本化设计或修复计划时，可以形成工作项级连续执行授权，覆盖同一 Jira 工作项内的实现、验证、提交、任务分支推送、必要 Jira 回写以及创建目标为 `develop` 的拉取请求；这些动作不再逐项暂停，但必须统一停在拉取请求审查。`master`、`main`、`develop`、`release/*` 及同类保护分支禁止普通自动推送；正常合并和发布必须取得独立确认。第 8 节 Hotfix 是唯一例外，显式命令调用即为授权，不追加确认。

## 16. 人工门禁规则

以下动作必须暂停并等待人工确认：

- 任务接管前 负责人不匹配。
- 需求范围、验收标准、目标仓库或验证方式缺失。
- 实际影响范围超出 Jira 已确认边界。
- 需要改变复杂度、风险等级或需求范围。
- AI 连续修复失败或无法解释失败原因。
- 未获得明确授权的推送、创建拉取请求或重新提交修复。
- 工作项级连续执行授权的所有权、任务、仓库、分支、范围、验证或风险事实已经变化。
- 拉取请求审查意见存在需要取舍的修改。
- 合入、发布、Git Tag、直接修改受保护分支、强推、历史改写或线上风险相关动作。

AIAgent 必须能向研发工程师说明暂停原因、当前证据、建议下一步和需要谁确认。

## 17. 反馈闭环规则

AgenticOps 必须包含 AIAgent 反馈通道，用于在任务完成、阻塞或交接时提交任务级审计记录，并在需要时按执行记录分析和优化 AgenticOps。

反馈闭环必须遵守：

```text
Python Runtime 执行操作
-> 产生结构化事件日志
-> 到达完成、阻塞或交接节点
-> AIAgent 将任务级审计记录写入本地 Jira 编号目录，并回写 Jira 关键结论和稳定引用
-> 维护者按需按运行、任务类型、失败码、时间范围或工作空间聚合分析
-> AIAgent 分析失败、卡点、重复人工确认、专业审查退回、重试、重做、有效经验和规则缺口
-> 生成改进建议
-> 人确认后更新 AgenticOps Skill、Rule、标准资产和 Python Runtime
```

反馈通道只做分析和建议，不允许 AIAgent 根据日志自动修改 AgenticOps 源头规则。

事件日志和任务审计必须写入具体项目 AI 工作空间的 Jira 编号目录：

```text
<project-ai-workspace>/
  .agentic-ops/
    tasks/
      <ISSUE-KEY>/
        runs/
        audit/
        feedback/
        handoff/
```

事件日志必须使用安全摘要，不得记录 secrets、原始敏感日志、完整 Jira 描述或敏感代码片段。

反馈进入 AgenticOps 源头规则前必须经过：

```text
Observation -> Proposal -> Accepted Change
```

## 18. 安全规则

严禁提交或持久化：

- secrets
- tokens
- private keys
- 真实 `.env`
- 原始敏感日志
- 未脱敏 Jira 原文
- 未脱敏业务代码片段

本地凭证只能通过被忽略的环境文件、系统凭证管理或运行时注入。

Jira / GitHub 写操作必须可审计。任何写操作都必须关联 `operation`、`workspace`、`issue_key`、`agentic_run_id`、`task_type`、`current_stage`、`agentic_next_action` 和事件日志。

## 19. 文档规则

项目至少维护：

- 目标定位文档。
- 设计审阅清单。
- 设计决策记录。
- 项目规则文档。
- 故事线文档。
- 当前设计文档。
- 项目开发风格文档。
- AIAgent 防幻觉工作规则。
- AI 员工手册。
- 操作契约文档。
- 工作流配置说明。
- 反馈闭环说明。
- 端到端演示脚本。
- Python Runtime 与 Shell Bootstrap 设计说明。
- 证据模板设计说明。

文档必须保持简洁、可执行、便于试点研发直接使用。

面向用户、研发工程师和审阅者的可见文档标题和正文默认使用中文。只有以下内容保留英文或缩写：

- 属性名、状态名、配置键、协议字段和错误码，例如 `agentic_run_id`、`takeover_comment_id`、`side_effects`、`missing_form_field`。
- 命令、参数、文件路径、目录名和代码符号，例如 `ao-work workspace init`、`--jira-project`、`developer/standards/contracts/operations/`。
- 产品名、平台名、组件名和行业通用稳定名词，例如 `AgenticOps`、`AIAgent`、`Jira`、`GitHub`、`CI`、`CLI`。
- 故事线、任务或契约的稳定编号，例如 `PM-001`、`DE-001`。

中文正文使用“研发工程师”“流程负责人”“代码审查人”等中文角色名。只有在字段名、配置项、协议字段、代码示例或模板占位符中，才保留 `owner`、`reviewer` 等英文标识。自然描述中的动作、职责、流程、证据、门禁、策略、模板、运行手册和反馈报告必须使用中文；例如描述动作时写“推送”“合并”“创建拉取请求”，只有引用命令时才写 `git push`、`git merge` 或 `gh pr create`。

面向用户、研发工程师、流程负责人、审阅者或 Jira 参与者的自然语言交互必须使用中文。

Jira 交互中的人可见内容必须使用中文，包括摘要、标题、描述、评论、工作日志、证据正文、阻塞说明、补卡说明和任务审计记录。Jira 字段名、状态名、`transition` 名称、卡片编号、命令、配置字段、协议字段、错误码、代码标识和日志关键字可以保留原始英文或缩写，但必须用中文解释结论、风险和需要人工处理的动作。

以下提交规则属于 `tapstate/agentic-ops` 项目规则，只约束维护 AgenticOps 源头仓库。

AgenticOps 提交信息推荐格式为 `<type>(<scope>): <tag> <subject>`。`tag` 指 Jira 任务编号，例如 `TAP-1234`。TapData 公司代码提交必须绑定 Jira 任务卡片，不得省略 `<tag>`。

AgenticOps 是内部项目，提交标题和提交描述正文使用中文。

`type`、`scope`、Jira key、命令和配置字段作为结构化标识可以保留英文。`subject` 使用中文，简洁说明本次提交做了什么，末尾不加句号。

Jira 任务编号必须能从分支名、用户指令、Jira 卡片或任务上下文中确认；无法确认时必须停止并请求研发工程师补齐，不得创建无 Jira 绑定的代码提交。

非平凡提交必须包含 body，用中文说明问题、处理方式、验证结果和风险。提交信息不得包含完整 Jira 描述、敏感日志、凭证或未经脱敏的客户信息。

AIAgent 只有在研发工程师明确要求“提交变更”或“提交代码”后才能执行 `git commit`，只有在明确要求推送或执行发布后才能 `git push`。不得直接提交 `main`；日常开发使用 `develop`，正式发布与 Hotfix 使用第 8 节脚本。普通推送若能可靠确认 Jira 编号，应将中文变更总结评论到对应 Jira 任务；Hotfix 不与 Jira 交互，编号只保存在 Merge commit 中。

当规则变化影响 AIAgent 行为时，必须同步更新：

- AI 员工手册。
- 操作契约。
- CLI 命令说明。
- 故事线或验收标准。
- AIAgent 工作规则。
- 项目开发风格。
