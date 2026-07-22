# 问题修复与同步路径

## 1. 目的

本文定义 AgenticOps 正式使用前必须具备的问题修复路径。

研发日常使用的是安装后的 `agentic-cli`、AI 员工手册、operation contracts、workflow profiles、policies、runbooks 和 templates。项目出现问题时，AgenticOps 必须能快速判断问题类型，选择正确修复载体，完成验证、发布、同步和回滚。

当前仓库已实现本地资产安装、本地 release 打包、operation contract 校验、profile validate / update / rollback、policy validate / update / rollback、远程 release manifest / artifact 下载校验、真实二进制切换、doctor 显式真实外部检查、真实 Jira REST client 合同测试基线，以及真实 Jira 字段、comment 和显式 transition 写入 gate/confirmation。尚未实现完整真实发布和 profile 驱动 transition id/name 映射。本文是正式使用前的目标设计和验收基线。

## 2. 架构适配性评估

目标是让 AgenticOps 渐进式形成公司标准流程，并在发现问题后快速修复上线。按这个目标评估当前架构：

| 架构部分 | 当前状态 | 适配性判断 | 必须补齐的能力 |
| --- | --- | --- | --- |
| Go CLI Runtime | 已有 `agentic-cli` 本地 fake flow、真实 Jira REST client 合同测试基线、真实二进制切换和 doctor 显式外部检查 | 适合承载强制检查、结构化输出、诊断、更新和回滚 | profile 驱动 transition 映射 |
| Operation Contract | 已有机器可读 YAML 和 `contract validate` 基线 | 适合沉淀标准操作输入输出和失败码 | operation 兼容性版本、跨版本迁移规则 |
| Workflow Profile | 已有默认 profile、`profile validate / update / rollback` 基线 | 适合处理不同团队和 Jira workflow 差异 | 真实 Jira status / transition gate、资产包来源、profile 版本审计 |
| Policy / Gate | 已有 policy validate / update / rollback 本地基线 | 适合控制关键步骤门禁 | 真实写操作 gate 变更审计和 confirmation |
| Evidence / Feedback | 已有本地 evidence 和 feedback report | 适合发现重复问题并推动规范优化 | feedback bundle、问题分类统计、修复效果追踪 |
| Release / Install | 已有 bootstrap stub、本地资产安装、本地 build / release 脚本和远程 manifest / artifact 下载校验 | 适合快速分发，但当前不完整 | GitHub release、自更新、回滚、真实二进制切换 |
| 项目资料边界 | 已明确 `~/.agentic-ops` 和项目 AI 工作空间边界 | 适合隔离全局工具资产和任务运行产物 | assets 版本目录、workspace 覆盖配置、敏感信息检查 |

结论：

- 当前架构方向适配“渐进形成公司标准流程”和“快速修复上线”两个目标。
- 最大缺口不在目录结构，而在正式使用前缺少远程版本化资产、自更新、真实外部诊断检查、真实 Jira 写入 gate/confirmation 和更完整的 profile 版本审计。
- 修复能力应优先作为 `agentic-cli` 的一组受控 operation 实现，而不是分散在 shell 脚本、人工说明或提示词中。

## 3. 设计目标

持续快速优化能力必须做到：

- 发现问题时，研发能用一条命令生成脱敏诊断信息。
- 维护者能按问题类型判断应该修 Go 代码、profile、policy、template 还是 Jira 卡片数据。
- 修复后能快速发布二进制或资产包。
- 研发侧能快速同步更新，并在异常时回滚。
- 所有修复动作能进入 feedback report，用于判断问题是否减少。
- 所有能力以 `agentic-ops` 当前项目为权威维护；历史 `rd-agentic` / `td-agentic` 后缀项目只作为参考来源，不作为当前设计、计划或目标的事实源。

## 4. 总体流程

所有问题都先进入同一条处理路径：

```text
发现问题
-> 记录 operation / workspace / version / error_code
-> 生成脱敏诊断信息
-> 判断问题类型
-> 选择修复载体
-> 本地和合同验证
-> 发布二进制或资产包
-> 研发侧同步更新
-> 必要时回滚
-> feedback report 观察问题是否减少
```

修复路径必须遵守：

- 不把 secrets、tokens、private keys、原始 Jira 描述、敏感代码片段写入诊断包。
- 不让 AIAgent 猜 Jira 字段、状态或 workflow。
- 不让 AIAgent 未经人工确认自动修改全局规范、profile 或 policy。
- 不把标准资产不完善的问题误判为 `agentic-cli` 二进制问题。
- 不把所有问题都升级成二进制发布；能通过 profile / policy / template 修复的问题优先走资产包。
- 不维护旧版本补丁线；BUG 只在最新版本修复，有新版本时推荐自动更新应用。
- 任何放宽门禁、真实 Jira 写操作、Git push、PR、merge 和发布都必须可审计、可回滚。

## 5. 问题分类

| 问题类型 | 典型表现 | 修复载体 | 同步方式 |
| --- | --- | --- | --- |
| `agentic-cli` 逻辑错误 | 命令输出错误、run_id 生成错误、事件写入错误、adapter 行为错误 | Go CLI 二进制 | 发布最新版本 + `update apply` |
| Jira 流程状态没适配 | 未知 Jira status / transition、状态映射失败、项目 workflow 差异 | workflow profile / adapter mapping | asset update + `profile update` |
| Jira 卡片属性丢失 | 缺少 owner、验收标准、目标仓库、验证方式、风险等级 | gate failure + 补全模板 / field mapping | 阻断接管 + 人工补卡或 profile 修复 |
| 关键步骤门禁调整 | push / PR / Jira comment / scope change 的确认要求变化 | policy package | policy update + review + rollback |
| 标准提示或处理步骤不完整 | AIAgent 不知道如何处理某类已知问题、说明不清、转人工条件不明确 | handbook / runbook / template | asset update + 人工 review |

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
  "next_action": "ask_owner",
  "ok": false,
  "code": "missing_target_repo",
  "required_human_action": "请补充 target_repo 或维护 workspace repo 映射"
}
```

正式使用前应实现目标命令：

```sh
agentic-cli doctor --workspace tapstate
agentic-cli feedback bundle --workspace tapstate --run-id <run_id> --redact
```

`doctor` 用于判断安装、版本、profile、policy、Jira / GitHub 凭证和 workspace 是否一致。  
`feedback bundle --redact` 用于给维护者提供脱敏诊断包。

### 当前错误与事件基线

当前本地 fake flow 已实现以下基线能力：

- 失败输出固定包含 `ok`、`operation`、`code`、`message`、`required_human_action`、`task_type`、`current_stage` 和 `next_action`。
- 事件日志固定支持 `agentic_cli_version`、`version_state`、`asset_version`、`operation`、`task_type`、`current_stage`、`next_action`、`code`、`gate` 和 `gate_status`。
- `gate_status` 当前取值为 `passed`、`blocked` 或 `failed`。
- 已实现命令中的校验失败会优先给出明确 `required_human_action`，例如缺少 `run_id` 时要求补充 `--run-id`。

四类正式问题的稳定错误码规划如下：

| 问题类型 | 稳定错误码 | 当前状态 |
| --- | --- | --- |
| `agentic-cli` 逻辑错误 | `agentic_cli_logic_error` | `doctor`、doctor 显式真实外部检查和 `feedback bundle --redact` 诊断基线已落地。 |
| Jira 流程状态没适配 | `unknown_jira_status` | `profile validate / update / rollback`、真实 Jira REST 读取映射基线和显式 `--jira-transition-id` transition gate 已落地；profile 驱动 transition id/name 映射仍需流程 owner 决策。 |
| Jira 卡片属性丢失 | `missing_jira_field` | fake Jira 接管 gate 已覆盖必填字段阻断；真实 Jira 字段读取映射基线已落地，补全模板后续实现。 |
| 关键步骤门禁调整 | `policy_gate_required` | `policy validate / update / rollback` 本地基线已落地；真实 Jira 字段写入、Jira comment 写入和显式 transition 写入已要求 `--confirm-real-jira-write`，并记录 `real_jira_write` gate 审计事件。 |

## 7. 修复路径一：CLI 逻辑错误

适用场景：

- `agentic-cli` 命令逻辑错误。
- JSON 输出字段错误。
- 事件日志写入错误。
- evidence / feedback 生成错误。
- fake / real adapter 行为与 contract 不一致。

处理流程：

```text
研发发现错误
-> 执行 doctor / feedback bundle
-> 维护者复现
-> 修复 Go 代码
-> go test ./...
-> contract test
-> local fake flow e2e
-> 构建多平台二进制
-> 发布新的 latest release
-> 更新 release manifest
-> 研发 update apply 或自动更新到最新版本
```

目标命令：

```sh
agentic-cli update check
agentic-cli update apply
```

AgenticOps 不支持为旧版本单独做 BUG 修复。修复完成后只发布新的 latest 版本，研发侧应优先自动更新到最新版本。后续如果实现 rollback，它只用于安装失败或新版本不可用时的本地恢复，不作为旧版本修复策略。

严重逻辑错误可以在 manifest 中标记：

```yaml
severity: required
reason: takeover_task may write invalid evidence
blocked_operations:
  - takeover_task
  - write_evidence
```

`required` 更新只允许用于安全、数据损坏、错误证据回写、严重流程越权等问题。

## 8. 修复路径二：Jira 流程状态没适配

适用场景：

- Jira status 名称变更。
- 项目新增或调整 transition。
- 不同 workspace 的 Jira workflow 不一致。
- AIAgent 看到未知状态，无法判断下一步。

这类问题优先修复 workflow profile，不优先发布二进制。

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
  "required_human_action": "请维护 workflow profile 的 status_mapping"
}
```

## 9. 修复路径三：Jira 卡片属性丢失

适用场景：

- 缺少验收标准。
- 缺少目标仓库。
- 缺少验证方式。
- 缺少 owner。
- 缺少风险等级或范围边界。

这类问题默认不是工具自动修复，而是任务数据质量问题。AgenticOps 必须停止接管，并输出明确补全动作。

示例：

```json
{
  "ok": false,
  "operation": "takeover_task",
  "code": "missing_target_repo",
  "message": "Jira issue 缺少目标仓库信息",
  "required_human_action": "请在 Jira 卡片补充目标仓库，或维护 workspace repo 映射"
}
```

处理流程：

```text
gate 发现必填属性缺失
-> 停止接管
-> 生成 required_human_action
-> 可生成 Jira comment 补全模板
-> 记录 missing_field 事件
-> feedback report 聚合缺失字段
-> 提出 Jira 创建模板或字段校验改进建议
```

如果字段实际存在但名称不同，应修复 `field_mapping`：

```yaml
field_mapping:
  target_repo:
    jira_field: customfield_12345
  acceptance_criteria:
    jira_field: customfield_23456
```

如果字段确实缺失，必须由研发 owner 或流程 owner 补卡，不允许 AIAgent 编造。

## 10. 修复路径四：关键步骤门禁调整

适用场景：

- 是否允许写 Jira comment。
- 是否允许推进 Jira 状态。
- 是否允许创建 commit。
- 是否允许 push。
- 是否允许创建或更新 PR。
- scope change、风险扩大、发布动作是否必须人工确认。

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

当前本地基线命令：

```sh
agentic-cli policy validate --workspace tapstate
agentic-cli policy update --workspace tapstate --source /path/to/default-policy.yaml
agentic-cli policy rollback --workspace tapstate
```

当前实现会读取 `assets/policies/default.yaml`，校验 policy 名称、版本和 `write_jira_comment`、`transition_jira_status`、`git_commit`、`git_push`、`create_pr`、`scope_change` 六个关键 gate。`policy update` 会先校验 source，再写入 `.bak` 备份；`policy rollback` 会先校验备份，再恢复默认 policy。

门禁调整规则：

- 不确定是否需要门禁时，按需要门禁处理。
- 放宽门禁必须有人工确认和决策记录。
- 收紧门禁可以快速发布，但仍必须可回滚。
- 所有 gate 变更必须写入事件日志和版本记录。

## 11. 发布与同步模型

AgenticOps 需要两类发布物：

```text
binary release
  agentic-cli 多平台二进制

asset release
  operation contracts
  workflow profiles
  policies
  templates
  handbooks
```

本机建议结构：

```text
~/.agentic-ops/
  bin/
    agentic-cli
  versions/
    agentic-cli/
      RES-v0.1.2-7f31a2b/
      RES-v0.1.3-a68372d/
  assets/
    RES-v0.1.2-7f31a2b/
    RES-v0.1.3-a68372d/
  current.json
  config.yaml
```

`current.json` 必须记录：

```json
{
  "agentic_cli_version": "RES-v0.1.3-a68372d",
  "asset_version": "RES-v0.1.3-a68372d",
  "previous_agentic_cli_version": "RES-v0.1.2-7f31a2b",
  "previous_asset_version": "RES-v0.1.2-7f31a2b"
}
```

## 12. 正式使用前验收标准

正式使用前必须满足：

- 每类问题都有稳定错误码、人工动作和事件日志。
- 研发可以一条命令生成脱敏诊断包。
- CLI 逻辑错误可以通过发布新的 latest 版本修复，并推荐研发侧自动更新。
- Jira status / transition 差异可以通过 profile 更新修复并回滚。
- Jira 卡片属性缺失会阻断接管，并给出补全模板。
- 关键 gate 可以通过 policy 更新调整，并保留审计记录。
- update check / apply 的输出全部是结构化 JSON；如实现 rollback，它只用于本地恢复，不用于旧版本修复线。
- required update 只阻断受影响 operation，不应无差别阻断所有命令。
- 所有修复进入 feedback report，用于后续观察问题是否减少。

## 13. 当前实现边界

当前第一阶段本地 fake flow 已支持：

- `assets install`
- `preflight`
- `workspace init`
- `agent init`
- `list-tasks`
- `takeover-task`
- `resume-takeover`
- `write-evidence`
- `feedback report`
- `scripts/version.sh`
- `scripts/build.sh`
- `scripts/release.sh`

当前 `update check/apply` 已完成本地 manifest 基线、远程 manifest 拉取、artifact 下载、checksum 校验和真实二进制切换。当前 `policy validate/update/rollback` 已完成本地文件基线，真实 Jira 字段写入、comment 写入和显式 transition 写入已记录 `real_jira_write` gate 审计事件；doctor 已支持显式 `--check-real-jira` 和 `--check-github` 外部检查。profile 驱动 transition id/name 映射和真实 release 发布仍属于正式使用前必须补齐的目标能力。
