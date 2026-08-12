# 问题修复与同步路径

> **迁移说明：** 本文中的 Go 二进制、资产包和 `exact_pair` 内容记录现役问题处理基线。目标修复路径改为更新稳定 `main` 中的 Skill、Rule、标准资产或 Python Runtime，再由 Shell Bootstrap 更新并回滚；具体替换顺序以迁移计划为准。

## 1. 目的

本文定义 AgenticOps 正式使用前必须具备的问题修复路径。

研发日常使用的是安装后的 `agentic-cli`、AI 员工手册、操作契约、工作流配置、策略、运行手册和模板。项目出现问题时，AgenticOps 必须能快速判断问题类型，选择正确修复载体，完成验证、发布、同步和回滚。

当前仓库已实现本地资产清单校验与安装、构建资源校验、操作契约校验、`profile validate / update / rollback`、`policy validate / update / rollback`、工作流配置驱动 Jira `transition` 标识映射、`exact_pair` 更新兼容判断、远程产物 checksum 校验、版本化暂存与原子二进制切换、本地 checksum 回滚、必要更新统一阻断、`doctor` 显式真实外部检查、真实 Jira REST 客户端合同测试基线，以及真实 Jira 字段、评论和 `transition` 写入门禁与人工确认。源码发布已经由 `scripts/release.sh`、`scripts/hotfix.sh`、PR、验证、Tag 和审计流程覆盖。当前不存在受控 `agentic-cli release publish`，只表示可选 GitHub Release 制品页能力尚未实现；它不等同于源码发布，也不阻塞当前正式研发流程。本文保留问题修复、同步和本地回滚的稳定设计与验收基线。

## 2. 架构适配性评估

目标是让 AgenticOps 渐进式形成公司标准流程，并在发现问题后快速修复上线。按这个目标评估当前架构：

| 架构部分 | 当前状态 | 适配性判断 | 必须补齐的能力 |
| --- | --- | --- | --- |
| Go CLI 运行时 | 已有 `agentic-cli` 本地模拟流程、真实 Jira REST 客户端合同测试基线、工作流配置驱动 `transition` 映射、`exact_pair` 更新门禁、本地回滚和 `doctor` 显式外部检查 | 适合承载强制检查、结构化输出、诊断、更新和回滚 | 跨版本迁移框架 |
| 操作契约 | 已有机器可读 YAML、`contract validate`、更新兼容与回滚契约基线 | 适合沉淀标准操作输入输出和失败码 | 跨版本迁移规则 |
| 工作流配置 | 已有默认工作流配置、`profile validate / update / rollback` 基线 | 适合处理不同团队和 Jira 工作流差异 | 真实 Jira `status` / `transition` 门禁、资产包来源、工作流配置版本审计 |
| 策略 / 门禁 | 已有 `policy validate / update / rollback` 本地基线 | 适合控制关键步骤门禁 | 真实写操作门禁变更审计和人工确认 |
| 证据 / 反馈 | 已有本地证据、反馈诊断包和按需反馈报告 | 适合提交任务审计、发现重复问题并推动规范优化 | 任务级审计提交、问题分类统计、修复效果追踪 |
| 发布 / 安装 | 已有安装引导、本地构建、源码发布脚本、PR 与 Tag 审计、资产来源与 exact_pair 校验、远程产物 checksum 校验、原子切换和本地回滚 | 适合快速分发 | 可选 GitHub Release 制品页能力按实际需求另行决策，不是当前阻塞项 |
| 项目资料边界 | 已明确 `~/.agentic-ops` 和项目 AI 工作空间边界 | 适合隔离全局工具资产和任务运行产物 | 资产版本目录、工作空间覆盖配置、敏感信息检查 |

结论：

- 当前架构方向适配“渐进形成公司标准流程”和“快速修复上线”两个目标。
- 现有源码发布、远程清单、产物校验、原子切换、`exact_pair` 兼容门禁、本地回滚和显式外部诊断是迁移基线；目标目录和运行边界已调整，需要用真实任务逐项复验后替换。
- 修复能力应优先作为 Python Runtime 的受控操作、Skill、Rule 或标准资产实现，而不是分散在 Shell Bootstrap、临时人工说明或聊天上下文中。

## 3. 设计目标

持续快速优化能力必须做到：

- 发现问题时，研发能用一条命令生成脱敏诊断信息。
- 维护者能按问题类型判断应该修 Python Runtime、Skill、Rule、工作流配置、模板还是 Jira 卡片数据。
- 修复合入稳定 `main` 后，研发侧能快速更新 managed clone，无需等待项目二进制构建。
- 研发侧能快速同步更新，并在异常时回滚。
- 所有修复动作能进入任务级审计记录，并可被按需反馈报告分析，用于判断问题是否减少。
- 所有能力以 `agentic-ops` 当前项目为权威维护；历史 `rd-agentic` / `td-agentic` 后缀项目只作为参考来源，不作为当前设计、计划或目标的事实源。

## 4. 总体流程

所有问题都先进入同一条处理路径：

```text
发现问题
-> 记录操作 / 工作空间 / 版本 / 错误码
-> 生成脱敏诊断信息
-> 判断问题类型
-> 选择修复载体
-> 本地和合同验证
-> 通过受保护 main 发布源码和标准资产
-> 研发侧同步更新
-> 必要时回滚
-> 按需反馈报告观察问题是否减少
```

修复路径必须遵守：

- 不把 secrets、tokens、private keys、原始 Jira 描述、敏感代码片段写入诊断包。
- 不让 AIAgent 猜 Jira 字段、状态或工作流。
- 不让 AIAgent 未经人工确认自动修改全局规范、工作流配置或策略。
- 不把 Skill、Rule 或标准资产不完善的问题误判为 Python Runtime 问题。
- 不把所有问题都升级成 Runtime 修复；能通过 Skill、Rule、工作流配置或模板修复的问题优先修改对应资产。
- 不维护旧版本补丁线；BUG 只在最新版本修复，有新版本时推荐自动更新应用。
- 任何放宽门禁、真实 Jira 写操作、Git 推送、创建拉取请求、合并和发布都必须可审计、可回滚。

## 5. 问题分类

| 问题类型 | 典型表现 | 修复载体 | 同步方式 |
| --- | --- | --- | --- |
| `agentic-cli` Runtime 逻辑错误 | 命令输出错误、`agentic_run_id` 生成错误、事件写入错误、`adapter` 行为错误 | Python Runtime | 合入稳定 `main` + Bootstrap 更新 |
| Jira 流程状态没适配 | 未知 Jira `status` / `transition`、状态映射失败、项目工作流差异 | 工作流配置 / 适配器映射 | `asset update` + `profile update` |
| Jira 卡片属性丢失 | 缺少负责人、验收标准、目标仓库、验证方式、风险等级 | 门禁失败 + 补全模板 / 字段映射 | 阻断接管 + 人工补卡或工作流配置修复 |
| 关键步骤门禁调整 | 推送 / 创建拉取请求 / Jira 评论 / 范围变更的确认要求变化 | 策略包 | `policy update` + 审查 + 回滚 |
| 标准提示或处理步骤不完整 | AIAgent 不知道如何处理某类已知问题、说明不清、转人工条件不明确 | 手册 / 运行手册 / 模板 | `asset update` + 人工审查 |

## 6. 通用诊断数据

每次失败都必须能形成安全摘要：

```json
{
  "workspace": "tapstate",
  "agentic_cli_version": "RES-v0.1.3-a68372d",
  "version_state": "RES",
  "asset_version": "RES-v0.1.3-a68372d",
  "operation": "takeover_task",
  "task_type": "task_takeover",
  "current_stage": "takeover_gate",
  "agentic_next_action": "ask_owner",
  "ok": false,
  "code": "missing_target_repo",
  "required_human_action": "请补充 target_repo 或维护工作空间代码仓库映射"
}
```

当前提供的诊断命令：

```sh
agentic-cli doctor --workspace tapstate
agentic-cli feedback bundle --workspace tapstate --run-id <agentic_run_id> --redact
```

`doctor` 用于判断安装、版本、工作流配置、策略、Jira / GitHub 凭证和工作空间是否一致。
`feedback bundle --redact` 用于给维护者提供脱敏诊断包。

### 当前错误与事件基线

当前 CLI 基线已实现以下能力：

- 失败输出固定包含 `ok`、`operation`、`code`、`message`、`required_human_action`、`task_type`、`current_stage` 和 `agentic_next_action`。
- 事件日志固定支持 `agentic_cli_version`、`version_state`、`asset_version`、`operation`、`task_type`、`current_stage`、`agentic_next_action`、`code`、`gate` 和 `gate_status`。
- `gate_status` 当前取值为 `passed`、`blocked` 或 `failed`。
- 已实现命令中的校验失败会优先给出明确 `required_human_action`，例如缺少 `agentic_run_id` 时要求补充 `--run-id`。

四类正式问题的稳定错误码规划如下：

| 问题类型 | 稳定错误码 | 当前状态 |
| --- | --- | --- |
| `agentic-cli` 逻辑错误 | `agentic_cli_logic_error` | `doctor`、doctor 显式真实外部检查和 `feedback bundle --redact` 诊断基线已落地。 |
| Jira 流程状态没适配 | `unknown_jira_status` | `profile validate / update / rollback`、真实 Jira REST 读取映射基线、显式 `--jira-transition-id` 门禁和工作流配置驱动 `transition` 标识映射已落地。 |
| Jira 卡片属性丢失 | `missing_jira_field` | 模拟 Jira 接管门禁已覆盖必填字段阻断；真实 Jira 字段读取映射基线、补全模板输出和反馈报告缺失字段聚合已落地。 |
| 关键步骤门禁调整 | `policy_gate_required` | `policy validate / update / rollback` 本地基线已落地；真实 Jira 字段写入、Jira 评论写入和显式 `transition` 写入已要求 `--confirm-real-jira-write`，并记录 `real_jira_write` 门禁审计事件。 |

## 7. 修复路径一：Python Runtime 逻辑错误

适用场景：

- `agentic-cli` 命令逻辑错误。
- JSON 输出字段错误。
- 事件日志写入错误。
- 证据或反馈生成错误。
- 模拟或真实适配器行为与契约不一致。

处理流程：

```text
研发发现错误
-> 执行 doctor / feedback bundle
-> 维护者复现
-> 修复 Python Runtime
-> 执行单元测试与 contract test
-> 执行本地 fixture 和真实场景回归
-> 任务分支 PR 合入 develop
-> 通过受控发布 PR 合入 main
-> 研发执行 update apply 更新 managed clone
-> 回到原输入复验
```

目标命令：

```sh
agentic-cli update check
agentic-cli update apply
agentic-cli update rollback
```

AgenticOps 不支持为旧版本单独做 BUG 修复。修复完成后只更新稳定 `main`，研发侧应优先更新到最新提交。`update rollback` 只恢复本地保存的上一稳定 Git 引用和对应锁定 Python 环境，用于安装失败或新版本不可用时的恢复，不作为旧版本修复策略。

严重逻辑错误可以在版本化更新策略中标记：

```yaml
severity: required
reason: takeover_task 可能写入无效证据
blocked_operations:
  - takeover_task
  - write_evidence
```

`update check` 把目标 Git 引用、兼容状态和受影响操作写入安装目录 `.local/update-state.json`。统一命令门禁只读取该本地状态，并仅阻断 `blocked_operations` 中的操作；`help`、`version`、`doctor`、`preflight`、`update check`、`update apply` 和 `update rollback` 始终可用。

`update apply` 只能快进到受保护 `main` 中已经确认的提交，执行 `uv sync --locked` 和固定自检后才切换 `current-ref`。更新不得修改 `user/` 或任何项目 AI 工作空间；依赖锁变化和目标提交必须进入安装审计。

`required` 更新只允许用于安全、数据损坏、错误证据回写、严重流程越权等问题。

## 8. 修复路径二：Jira 流程状态没适配

适用场景：

- Jira status 名称变更。
- 项目新增或调整 transition。
- 不同工作空间的 Jira 工作流不一致。
- AIAgent 看到未知状态，无法判断下一步。

这类问题优先修复工作流配置或项目映射，不优先修改 Python Runtime。

示例：

```yaml
status_mapping:
  in_development:
    - In Progress
    - 开发中
  waiting_review:
    - Code Review
    - 等待 Review
```

目标命令：

```sh
agentic-cli profile validate --workspace tapstate
agentic-cli profile update --workspace tapstate
agentic-cli profile rollback --workspace tapstate
```

未知状态必须阻断，不允许 AIAgent 猜：

```json
{
  "ok": false,
  "operation": "takeover_task",
  "code": "unknown_jira_status",
  "message": "当前 Jira 状态未配置映射",
  "required_human_action": "请维护 工作流配置的 status_mapping"
}
```

## 9. 修复路径三：Jira 卡片事实不足

适用场景：

- 缺少验收标准。
- 缺少目标仓库。
- 缺少验证方式。
- 缺少负责人字段。
- 缺少风险等级或范围边界。

这类问题默认不是 CLI 自动修复，也不属于 `takeover-task` 的项目准入判断范围。AIAgent 必须先执行只读检查，结合项目资产判断卡片事实是否足够；如果不足，应输出分析结果和补卡建议，由研发工程师确认后再回写 Jira。

示例：

```json
{
  "ok": true,
  "operation": "inspect_task",
  "issue_key": "TAP-123",
  "form_values": {
    "target_repo": "",
    "problem_branch": "release-v4.0.0"
  },
  "asset_refs": {
    "admission_dir": "standards/projects/tapdata/admission",
    "templates_dir": "standards/projects/tapdata/templates"
  },
  "recommended_next_action": "inspect_by_agent"
}
```

处理流程：

```text
inspect-task 输出 Jira 事实、表单值和项目资产路径
-> AIAgent 读取项目准入资产和模板
-> AIAgent 结合卡片与代码做先行分析
-> 一次性列出全部缺失或冲突项
-> 研发工程师确认真实写入
-> add-task-comment 写入准入分析与补卡建议
-> 结束本次接管
-> 研发工程师确认补卡内容
-> update-task-description-sections 更新稳定任务契约
-> add-task-comment 写入补卡确认结果
-> 再次结束本次接管
-> 用户再次要求接管时，AIAgent 重新 inspect-task
-> 准入判断通过后再调用 takeover-task
-> 接管后 add-task-comment 写入版本化修复计划
-> 研发工程师确认且决策评论写入 Jira 后才允许修改代码
```

Description 用于保存确认后的稳定任务契约，Comment 用于保存分析、计划、决策、阻塞和证据轨迹，Custom field 用于保存 profile 已映射的结构化结论。Worklog 只记录真实耗时，不承载门禁或决策。

如果字段实际存在但名称不同，应修复 `jira_form_mapping`：

```yaml
jira_form_mapping:
  fields:
    target_repo:
      source: jira_field
      jira_field: customfield_12345
    acceptance_criteria:
      source: jira_field
      jira_field: customfield_23456
      writable: true
```

只有明确声明 `writable: true` 的 `jira_field` 映射可以由 `update-task-form` 写入。负责人、assignee、代理所有权、Description 和 Comment 必须由对应专用操作维护。如果字段确实缺失，必须由研发工程师或流程负责人补卡，不允许 AIAgent 编造。

## 10. 修复路径四：关键步骤门禁调整

适用场景：

- 是否允许写 Jira `comment`。
- 是否允许推进 Jira 状态。
- 是否允许创建 `commit`。
- 是否允许推送。
- 是否允许创建或更新 PR。
- `scope change`、风险扩大、发布动作是否必须人工确认。

门禁必须配置化、版本化，不应写死在提示词里。

示例：

```yaml
gates:
  write_jira_comment:
    required: false
  transition_jira_status:
    required: true
  git_commit:
    required: true
  git_push:
    required: true
  create_pr:
    required: true
  scope_change:
    required: true
```

策略操作命令：

```sh
agentic-cli policy validate --workspace tapstate
agentic-cli policy update --workspace tapstate --source /path/to/default-policy.yaml
agentic-cli policy rollback --workspace tapstate
```

策略校验必须覆盖策略名称、版本和 `write_jira_comment`、`transition_jira_status`、`git_commit`、`git_push`、`create_pr`、`scope_change` 六个关键门禁。`policy update` 必须先校验来源文件，再写入可回滚备份；`policy rollback` 必须先校验备份，再恢复默认策略。

门禁调整规则：

- 不确定是否需要门禁时，按需要门禁处理。
- 放宽门禁必须有人工确认和决策记录。
- 收紧门禁可以快速发布，但仍必须可回滚。
- 所有门禁变更必须写入事件日志和版本记录。

## 11. 安装与同步模型

AgenticOps 只维护 latest 安装路径。GitHub 仓库和本机 `~/.agentic-ops` managed clone 的 Git 跟踪结构一致；本机只增加隔离环境、用户配置和安装状态：

```text
~/.agentic-ops/
  bootstrap/
  runtime/
  skills/
  rules/
  standards/
  .venv/
  user/
  bin/
    agentic-cli
  .local/
    current-ref
    previous-ref
    install-log.json
    update-stash/
```

`runtime/`、`skills/`、`rules/` 和 `standards/` 是 Git 跟踪的运行资产源头；`.venv/`、`user/`、`bin/agentic-cli` 和 `.local/*` 是本地产生内容，必须被 `.gitignore` 忽略。

`.local/install-log.json` 必须记录：

```json
{
  "operation": "update",
  "current_ref": "<git-commit>",
  "previous_ref": "<git-commit>",
  "lock_digest": "<sha256>",
  "bin": "~/.agentic-ops/bin/agentic-cli"
}
```

## 12. 正式使用前验收标准

正式使用前必须满足：

- 每类问题都有稳定错误码、人工动作和事件日志。
- 研发可以一条命令生成脱敏诊断包。
- Python Runtime、Skill、Rule 或标准资产问题可以通过受保护 `main` 的 latest 提交修复，并由研发侧快速更新。
- Jira `status` / `transition` 差异可以通过工作流配置更新修复并回滚。
- Jira 卡片属性缺失会阻断接管，并给出补全模板。
- 关键门禁可以通过策略更新调整，并保留审计记录。
- 安装或更新失败时可回退到 `.local/previous-ref`；回退只用于本地恢复，不用于旧版本修复线。
- 必要更新只阻断受影响操作，不应无差别阻断所有命令。
- 所有修复进入任务级审计记录，并可通过按需反馈报告观察问题是否减少。

## 13. 阶段计划入口

阶段性实现状态、当前实现边界、剩余工作和验收命令只维护在对应 Jira 工作项中。本文只保留问题修复与同步路径的稳定设计、门禁和运行边界。
