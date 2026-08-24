# AgenticOps Go 实现迁移基线

> **冻结历史 / 迁移基线，不是现役操作：** 本文记录旧 Go 实现与统一 `agentic-cli` 所承载的设计事实，只用于迁移能力核对。现役事实以《AgenticOps Skill 与 Python Runtime 驱动项目全景》《项目结构》和《Python Runtime》为准：工作面为 `maintainer` / `developer`，入口为 `ao-maint` / `ao-work`，实施进度与验收以 Jira `AO-11` 为准。

> 其中旧 developer `agentic_id` 字段绑定与清理模型已由 D-051 取代；AO maintainer 工作面的专用字段设计不受影响。

## 1. 定位

AgenticOps 是把公司员工执行标准沉淀成 AI 可执行标准流程的 AI 执行控制体系。

AgenticOps 主要落地研发 Jira 任务：帮助研发工程师操作 AIAgent 从 Jira 接管任务到完成任务。AgenticOps 不替代 Jira、不替代研发工程师、不替代拉取请求审查，也不以绕过人工授权、专业审查和策略门禁的全自动开发作为目标。它的核心价值是把 AI 员工从临时聊天助手变成流程内可管理、可追踪、可复盘的执行主体。

不同任务会涉及不同流程，例如新任务接管、恢复接管、拉取请求审查意见修复、阻塞上报和任务完成审计。AgenticOps 通过操作契约和工作流配置选择流程，通过 Task Form Standard、事件日志、`agentic_run_id`、证据、任务级审计记录和按需反馈分析记录执行过程；当前完整运行材料保存在按 Jira 编号隔离的项目 AI 工作空间，并将关键结论和稳定引用回写 Jira。

一句话定义：

```text
AgenticOps = AI 员工手册（含 AIAgent 工作规则）+ 项目规则 + 操作契约 + 任务表单标准 + 工作流配置 + 策略门禁 + 运行手册 + 模板 + Go CLI 运行时 + 证据与反馈闭环
```

## 2. 设计目标

目标主链路是一条真实、可控、可复用的研发任务执行链路：

```text
Jira 卡片已进入迭代
-> 研发工程师手动触发 AI
-> AI 拉取 负责人名下待办
-> 研发工程师选择一个卡片
-> AI 执行任务接管门禁
-> AI 生成 `agentic_run_id` 和接管记录
-> AI 形成版本化设计或修复计划
-> 研发工程师确认计划并授予工作项级连续执行授权
-> AI 实现、验证、提交、推送任务分支并回写必要 Jira 证据
-> AI 创建或更新拉取请求并输出拉取请求审查包
-> 进入既有 CI / 审查 / 合入流程
```

集成测试完整设计、低风险自动化门槛和 AI Review ROI 不作为核心主链路的前置条件。这类能力进入 AgenticOps 前，需要先明确适用场景、风险门禁和收益判断。

## 3. 故事线驱动推进模型

AgenticOps 后续推进使用故事线驱动模型：

```text
确定故事线
-> 确定设计
-> 制定计划并开发
-> 按故事线验收
```

故事线分为两类：

- 项目维护者故事：面向维护 `tapstate/agentic-ops` 源头仓库的人，覆盖设计治理、标准资产维护、发布、诊断、反馈、回滚和兼容性。
- 研发工程师故事：面向具体业务项目中使用 AgenticOps 管理 AIAgent 执行 Jira 任务的人，覆盖安装、初始化、任务接管、恢复、人工确认、证据和任务审计。

AIAgent 是流程执行者，`agentic-cli` 是受控运行时，不单独作为故事线主角。新增能力必须先能归属到明确故事线；如果能力改变产品形态、流程权限、自动化程度、发布权限、事实源归属或冲突裁决，必须先形成用户决策。

## 4. 核心原则

- Jira 是任务、需求、负责人、迭代、状态、评论和执行证据的事实源。
- Git 仓库是代码、测试、提交和分支的事实源。
- GitHub 拉取请求与 CI 是拉取请求审查、CI、审查评论和合入记录的事实源。
- AgenticOps 不创建新的任务管理事实源。
- `agentic_run_id` 只追踪一次 AI 执行，不替代 Jira 卡片编号，也不替代 Jira 状态。
- `agent_id` 是 AIAgent 的稳定身份；旧实现曾以 `agentic_id` 表达 Jira 任务绑定，现役 developer 改由 `Assignee`、受管 Comment 和本地 run 共同表达。
- AIAgent 可以推进研发阶段，但不能绕过工作流配置、门禁和人工确认点。
- 控制规范不能只靠提示词；提示词负责指导，Go CLI 运行时负责强制检查和结构化输出。
- 每次执行都必须产生可聚合记录，关键状态和信息必须回写到对应事实源或项目 AI 工作空间。
- 每个流程节点必须有可解释的标准动作、表单输出、审查结论和下一步规则。
- 不同专业角色在对应节点审查任务结果，以专业知识判断产出是否合格，以及流程标准是否需要优化。
- AIAgent 必须基于表单数据、事件记录、失败码和门禁判断重试、重做、继续或停止，不能只依赖聊天上下文。
- 框架只稳定定义大的流程环节、门禁和演进边界；成熟固化的交互逻辑才下沉为原子化操作，脚本入口只做受控编排或调用。
- AIAgent 在具体环节内执行任务并沉淀经验；周期性复盘把高频经验和失败模式转化为标准资产改进建议。
- 除非问题来自 `agentic-cli` 二进制逻辑错误，否则 AIAgent 应优先通过标准资产自助处理、阻断或转人工。

## 5. 仓库与运行边界

当前只有一个公司仓库作为 AgenticOps 的权威源头：

```text
git@github.com:tapstate/agentic-ops.git
```

该仓库统一管理全局通用资料：

```text
docs/          架构、目标定位、故事线、流程、计划
install-resources/basic/
               运行期通用安装资源：AI 资产入口、手册、契约、配置、策略、runbook、模板
install-resources/<os-arch>/
               已编译平台二进制 agentic-cli
bin/           本地安装后的命令目录，只提交 .gitkeep
.local/        本地安装和更新状态，只提交 .gitkeep
skills/        AgenticOps 技能和 AI 员工工作规则
packages/      agentic-cli Go CLI 运行时
examples/      端到端演示样例
tests/         自动化测试
scripts/       本地和 CI 辅助脚本
```

用户本机默认安装到：

```text
~/.agentic-ops
```

`~/.agentic-ops` 是 `tapstate/agentic-ops` 的完整 managed clone。它的目录结构与 GitHub 仓库一致，不是具体项目或具体任务的运行目录。安装脚本只负责 clone/update、校验 `install-resources/checksums.txt`、复制当前平台二进制到 `bin/agentic-cli`，并写入 `.local/` 本地状态。

具体项目的运行目录应是项目 AI 工作空间，例如：

```text
tapstate/
tapdata/
```

不同项目 AI 工作空间对应不同 Jira 用户、Jira 空间、代码仓库集合、本地源码目录、工作流配置和任务执行上下文。一个 Jira 空间可以对应若干代码仓库，仓库选择规则必须在工作流配置中维护，不能由 AIAgent 在接管时猜测。

## 6. 资料边界

AgenticOps 必须严格区分两类资料。

项目管理范围资料：

- AgenticOps 源代码
- 项目规则
- AI 员工手册
- 技能
- 操作契约
- 工作流配置
- 模板
- 适配器 / CLI / SDK
- 通用文档和示例

具体工作空间产物：

- 业务仓库代码变更
- 任务级分析记录
- 测试结果
- 本地执行日志
- Jira 评论和证据
- GitHub 拉取请求、CI、审查意见
- `agentic_run_id` 对应的证据

具体工作空间产物不应混入 `~/.agentic-ops` 的全局安装和配置资料；需要保留时应写入对应 AI 工作空间、目标业务仓库、Jira / 拉取请求证据链，或受控的任务执行记录位置。

## 7. AI 员工手册

AgenticOps 必须包含 AI 员工手册，作为 AIAgent 在研发流程中工作的核心交付物之一。

AI 员工手册同时服务两个对象：

- AIAgent：明确任务类型、当前阶段、下一步动作、工具、流程、门禁、证据和停止条件。
- 研发工程师：提供快捷操作方式，让研发能用自然语言或 CLI 指挥 AI 完成任务。

AI 员工手册应覆盖：

- 任务类型：安装、工作空间初始化、AIAgent 初始化、新任务接管、恢复接管、拉取请求审查意见修复、任务完成审计、AgenticOps 改进建议。
- 阶段模型：`已接收`、`预检中`、`等待接管`、`分析中`、`开发中`、`验证中`、`证据回写中`、`等待人工确认`、`阻塞`、`已交接`。
- 下一步动作：由操作契约、工作流配置、当前证据和人工门禁共同决定。
- 工作入口：拉待办、任务接管、继续失败任务、修复拉取请求审查意见、回写证据。
- 行为边界：无独立确认或有效工作项授权时不推送、不创建拉取请求；不自动合并、不扩大需求范围、不泄露敏感信息。
- 停止条件：需求不清、风险扩大、权限不足、测试无法运行、连续修复失败、需要人工判断。
- 交付要求：代码差异、测试结果、风险说明、Jira / 拉取请求证据、下一步建议。

AI 员工手册不是普通说明文档，而是 skills、操作契约、工作流配置、CLI 命令和证据模板的行为依据。

## 8. 操作契约

操作契约是 AgenticOps 的操作契约层，用于屏蔽 Jira / GitHub / Git 的底层事实差异，向 AIAgent 暴露稳定、统一、可验证的任务操作输入输出规范。

AIAgent 不应直接理解 Jira 字段、状态、`transition` 或 `comment` 格式。AIAgent 应理解 AgenticOps 暴露的操作：

```text
list_tasks
inspect_task
add_task_comment
update_task_description_sections
update_task_form
takeover_task
resume_takeover
read_task_context
write_evidence
write_pr_evidence
mark_blocked
request_owner_confirmation
prepare_pr
fix_pr_comments
feedback_collect
feedback_analyze
feedback_report
feedback_propose
```

每个操作至少定义：

- `operation`：操作名。
- `version`：契约版本。
- `purpose`：操作意图。
- `task_type`：适用的任务类型。
- `allowed_stages`：允许执行该操作的阶段。
- `input`：结构化输入。
- `preconditions`：前置门禁。
- `output`：结构化输出。
- `failure`：稳定错误码和人工动作。
- `side_effects`：是否可能写 Jira、创建记录、推送代码、创建拉取请求。
- `human_gate`：是否需要人工确认。

示例：

```yaml
operation: takeover_task
version: 1
purpose: 研发工程师授权 AIAgent 接管一个已进入迭代的任务。

task_type: task_takeover

allowed_stages:
  - waiting_takeover
  - takeover_gate

input:
  issue_key:
    type: string
    required: true
  workspace:
    type: string
    required: true

preconditions:
  - current_user_must_match_owner
  - managed_takeover_comment_must_be_verified
  - task_class_must_be_mapped_to_standard_process
  - issue_must_be_in_allowed_project
  - jira_status_must_map_to_entry_stage

output:
  agentic_run_id:
    type: string
  current_stage:
    enum:
      - takeover_started
      - blocked
      - waiting_owner_confirmation
  target_repo:
    type: string
  agentic_next_action:
    enum:
      - proceed
      - ask_owner
      - blocked

failure:
  code:
    enum:
      - owner_mismatch
      - assignee_mismatch
      - agent_ownership_conflict
      - task_class_mapping_gap
      - standard_process_mapping_gap
      - unknown_jira_status
      - invalid_takeover_stage
      - missing_permission
      - workflow_transition_not_allowed
  message:
    type: string
  required_human_action:
    type: string

side_effects:
  - may_write_jira_ownership
  - may_create_takeover_record
  - must_not_modify_code
  - must_not_create_pr
```

## 9. 工作流配置

AgenticOps 核心绑定研发流程语义，不绑定某一套具体 Jira 工作流。

Task Form Standard 定义 AI 操作任务从创建到完成所需的标准字段和生命周期要求。AIAgent 面向这些标准字段工作，不直接以 Jira 自定义字段、描述段落或工作流状态为判断依据。

工作流配置负责把操作契约映射到具体团队流程：

- Jira `base_url`、Jira 用户、Jira 空间、`issue_type`、JQL。
- Jira 表单映射，例如把 `owner`、`sprint`、`acceptance_criteria`、`target_repo`、`risk` 等 AgenticOps 标准字段映射到具体 Jira 字段、描述模板或工作空间配置。
- Jira 空间到代码仓库的映射，包括默认仓库、按 `component` / `label` / `issue_type` 匹配的仓库，以及本地源码目录。
- Jira 状态和 `transition` 映射。
- 专业审查节点映射，例如研发工程师确认、PR 代码审查人退回、QA 验证、运维或安全审批。
- GitHub 组织和代码仓库映射。
- 本地项目 AI 工作空间路径。
- 允许的写操作。
- 人工确认点。
- 证据模板。

不同 Jira 工作流对接时应先通过 Jira 表单映射适配 AgenticOps 标准。不符合标准的地方记录缺口并请求人工决策，不能让 AIAgent 直接猜测。工作流配置还必须说明哪些节点允许重试、哪些节点必须重做前序表单，以及哪些审查结论会把 `agentic_next_action` 置为 `ask_owner`、`fix_and_verify`、`redo_previous_stage` 或 `blocked`。

TapData / TapState 的方案 C 是第一套默认工作流配置，但不能硬编码进核心模型。

## 10. 控制层运行时

控制层采用本地优先的 Go CLI 运行时，不默认引入常驻 daemon 或 Web 平台。

shell 只用于 `gh api | bash` 认证安装引导。安装后 AIAgent 的业务逻辑、操作、策略、适配器、事件日志和反馈分析由 Go CLI 承载。维护 AgenticOps 源头仓库时，版本化 Hooks、`scripts/release.sh`、`scripts/hotfix.sh` 和共享库是项目级发布编排例外，不进入业务任务运行时。

推荐形态：

```text
packages/agentic-cli/
  cmd/
    agentic-cli/
  internal/
    cli/
    config/
    contract/
    feedback/
    git/
    github/
    jira/
    policy/
    workspace/
  testdata/
```

操作契约的机器可读源头在 `install-resources/basic/contracts/operations/`。Go CLI 可以在构建或运行时读取这些契约，但不在 package 内维护第二份契约源头。

AIAgent 始终调用统一入口：

```sh
agentic-cli list-tasks
agentic-cli takeover-task TAP-123
agentic-cli write-evidence --run-id ...
agentic-cli prepare-pr --run-id ...
```

Go CLI 运行时 的要求：

- stdout 输出结构化 JSON。
- stderr 输出人类诊断日志。
- 所有失败返回稳定 `code`。
- 退出码有固定语义。
- 写操作必须检查策略、门禁和人工确认。
- secrets 不允许出现在 stdout、stderr 或事件日志中。
- Linux (linux-amd64 / linux-arm64)、macOS Intel (darwin-amd64) 和 macOS Apple Silicon (darwin-arm64) 都应通过对应平台二进制运行。
- 发布流程必须支持快速构建、发布和自更新。

主 CLI 发布目标：

```text
darwin-arm64
darwin-amd64
linux-amd64
linux-arm64
```

源头仓库以 `main` 为 GitHub 默认分支，以 `develop` 为日常开发分支。正常发布通过 `scripts/release.sh` 把完整验证后的代码以 PR 的 Merge commit 合入 `main`，并在合并事实验证后推送二段式 annotated tag：硬门禁模式使用 `develop -> main` PR、Ruleset 和 Auto-merge；GitHub Free 私有仓库必须显式启用软门禁，从已验证的 `develop` HEAD 创建固定 `release/vX.Y -> main` PR，等待人工 Merge commit 后以同一命令恢复并再次完整验证。软门禁接受服务器端无法阻止其它入口直推的剩余风险。紧急修复通过 `scripts/hotfix.sh <KEY>` 自行切换和同步 `develop`，生成 Jira key 绑定的 Merge commit，并原子更新远端 `main/develop`；该流程不创建分支、PR、Tag，不调用 Jira/`gh`，也不追加人工门禁。详细规则见 [源码发布工作流设计](source-release-workflow-design.md)。

安装 bootstrap 允许依赖 `bash`、`curl` 和系统解压工具。`agentic-cli` 运行时不得依赖 `jq` 或本地 Python 环境。

`agentic-cli preflight` 应检查 OS、CPU 架构、GitHub CLI、GitHub 登录状态、Jira 凭证、工作流配置和当前业务仓库匹配关系。

## 11. Git 和 GitHub 边界

Jira 需要强封装，因为 Jira 工作流、字段、状态和空间差异较大。

GitHub / Git 不需要做可替换平台级封装，因为当前不会替换。但需要做安全操作级封装，用于控制 AI 员工能怎么使用它们。

允许 AIAgent 直接读取的 Git 操作包括：

```text
git status
git diff
git log
git show
```

高风险动作应通过 AgenticOps 操作或 CLI 防护管控：

```text
git commit
git push
git merge
git rebase
git clean
gh pr create
gh pr edit
```

建议由 Go CLI 防护管控的内部能力。这些能力不直接暴露为 AIAgent 可自由调用的底层动作，AIAgent 仍应调用操作契约中定义的操作：

```text
inspect_workspace
summarize_diff
run_verification
prepare_commit
record_commit
prepare_pr
read_pr_comments
classify_pr_comments
fix_pr_comments
check_ci_status
write_pr_evidence
```

工作项级连续执行授权生效后，AIAgent 可以自动完成 `git commit`、推送任务分支和创建目标为 `develop` 的拉取请求，并统一停在拉取请求审查节点。以下分支模式禁止自动推送：

```text
master
main
develop
release/*
```

任务分支推送与 PR 目标分支是两个独立事实：允许推送任务分支，不代表允许向目标分支推送。合并、发布、Tag、强推、历史改写和范围变化仍需独立人工确认。保护分支匹配规则必须由 policy、操作契约和测试共同验证，不能只依赖提示词或分支命名约定。

## 12. 反馈闭环

AgenticOps 应包含 AIAgent 反馈通道。反馈通道的主路径是任务完成、阻塞或交接时提交任务级审计记录；按 `workspace`、时间范围或失败码聚合只是后续分析方式，不作为每日必交付。

反馈闭环：

```text
Go CLI 执行操作
-> 产生结构化事件日志
-> 到达完成、阻塞或交接节点
-> AIAgent 将任务级审计记录写入本地 Jira 编号目录，并回写 Jira 关键结论和稳定引用
-> 维护者按需按 `agentic_run_id`、任务类型、失败码、时间范围或 `workspace` 聚合分析
-> AIAgent 分析失败、卡点、重复人工确认、规则缺口
-> 生成改进建议
-> 人确认后更新 AgenticOps 规则、手册、契约和 Go CLI
```

反馈通道只做分析和建议，不允许 AIAgent 根据日志自动修改 AgenticOps 源头规则。

运行日志和任务审计的当前管理位置是具体项目 AI 工作空间中的 Jira 编号目录；Jira 卡片回写关键结论、状态和稳定引用，后续再评估对接独立审计服务。

```text
<project-ai-workspace>/
  .agentic-ops/
    tasks/
      TAP-123/
        runs/
          <agentic_run_id>/
            events.ndjson
            summary.json
            evidence.md
        audit/
        feedback/
        handoff/
```

事件日志使用 NDJSON，每条事件只记录安全摘要，不记录 secrets、原始敏感日志、完整 Jira 描述或敏感代码片段。

示例事件：

```json
{
  "timestamp": "2026-07-21T10:30:12+08:00",
  "workspace": "tapstate",
  "agentic_run_id": "TAP-123-takeover-20260721103012-a8f3",
  "issue_key": "TAP-123",
  "task_type": "task_takeover",
  "operation": "takeover_task",
  "current_stage": "takeover_gate",
  "agentic_next_action": "ask_owner",
  "ok": false,
  "code": "real_jira_confirmation_required",
  "duration_ms": 842,
  "human_gate": true,
  "requires_human_action": true,
  "audit_target": "jira_issue",
  "audit_submitted": false,
  "audit_reference": null,
  "safe_message": "真实 Jira 写入需要研发工程师确认"
}
```

建议提供反馈命令：

```sh
agentic-cli write-evidence --workspace tapstate --run-id <agentic_run_id>
agentic-cli release-agent --workspace tapstate --run-id <agentic_run_id> --issue-key TAP-123 --completion-evidence evidence.md
agentic-cli feedback bundle --workspace tapstate --run-id <agentic_run_id> --redact
agentic-cli feedback report --workspace tapstate --date 2026-07-21
agentic-cli feedback analyze --workspace tapstate --date 2026-07-21
agentic-cli feedback propose --workspace tapstate --date 2026-07-21
```

`feedback report` 是按需分析报告，不是日报。反馈分析可以按 `agentic_run_id`、`issue_key`、`task_type`、失败码、时间范围或 `workspace` 查询。

反馈进入 AgenticOps 源头规则前必须经过：

```text
Observation -> Proposal -> Accepted Change
```

## 13. 人工门禁

AgenticOps 使用工作项级连续执行授权减少重复中断。研发工程师确认版本化设计或修复计划后，可以授权 AIAgent 在绑定的 Jira 工作项、`agentic_run_id`、仓库、工作分支、目标分支、范围和验证方式内连续完成实现、验证、提交、任务分支推送、必要 Jira 回写以及创建或更新拉取请求，并统一停在拉取请求审查。该机制复用已有人工确认，不取消高风险 gate。

当前自动推进规则限定为：任务分支允许自动提交和推送，拉取请求目标为 `develop`；`master`、`main`、`develop`、`release/*` 及同类保护分支禁止自动推送。合并、发布、Git Tag、范围变化、强推和历史改写不在连续授权范围内。

以下动作必须暂停并等待人工确认：

- 任务接管前 负责人不匹配。
- 需求范围、验收标准、目标仓库或验证方式缺失。
- 实际影响范围超出 Jira 已确认边界。
- 需要改变复杂度、风险等级或需求范围。
- AI 连续修复失败或无法解释失败原因。
- 未获得当前动作独立确认或有效工作项级连续执行授权的推送、创建拉取请求、重新提交修复。
- 工作项级连续执行授权绑定事实变化、范围或风险扩大、验证受阻、连续失败或外部写入结果不明确。
- 拉取请求审查意见存在需要取舍的修改。
- 合入、发布、Git Tag、直接修改受保护分支、强推、历史改写或线上风险相关动作。

## 14. 安装与工作空间流程

全局安装目标入口：

```sh
gh api -H 'Accept: application/vnd.github.raw' \
  '/repos/tapstate/agentic-ops/contents/scripts/install.sh?ref=main' \
  | AGENTIC_OPS_REPO_URL='git@github.com:tapstate/agentic-ops.git' bash
```

默认安装到：

```text
~/.agentic-ops
```

首次安装直接 clone managed clone。检测到 `~/.agentic-ops` 已安装时，安装脚本进入更新模式，先展示当前 ref 和目标分支，并要求研发工程师确认；非交互环境只能在用户确认后通过 `AGENTIC_OPS_ASSUME_YES=1` 继续。

项目 AI 工作空间初始化必须在项目 AI 工作空间目录内执行。研发工程师只需要指定项目配置项和 Jira 用户；Jira 空间、仓库映射、本地路径和工作流配置由 workflow profile 定义：

```sh
cd <project-ai-workspace>
agentic-cli workspace init --project tapstate --jira-user dev@example.com
```

初始化会在项目 AI 工作空间中写入 `.agentic-ops/agent.json` 和 `AGENTS.md`。AIAgent 在该目录中启动后，可以从本地配置推断当前项目，并按 `AGENTS.md` 中的规则调用 `agentic-cli`。

典型使用：

```sh
agentic-cli preflight
agentic-cli list-tasks
agentic-cli takeover-task TAP-123
agentic-cli write-evidence --run-id ...
```

以上命令代表核心 CLI 入口。对外演示必须使用真实 Jira 卡片；本地模拟流程只作为自动化回归验证。真实 Jira / GitHub 写操作仍必须经过门禁、策略和人工确认。
