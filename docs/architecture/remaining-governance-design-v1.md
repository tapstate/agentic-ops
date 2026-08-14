# AgenticOps 剩余治理设计草案

> **冻结历史 / 迁移基线，不是现役操作：** 本文保留旧统一 CLI 阶段的治理裁决与候选方案。涉及命令、安装、目录或工作面时，以现役《项目结构》和《Python Runtime》为准。

> **状态：** Task 4 和 Task 6 已决策并完成实现；已确认不引入 Web 控制台或后台常驻服务、任务审计以本地 Jira 编号目录为当前最终管理位置，并允许有效授权自动推进到任务分支提交和 `develop` PR；Task 7 仅保留为可选 GitHub Release 制品能力设计；transition 采用“ID 严格优先、唯一名称受控兜底、歧义即阻断”的裁决规则。
>
> **范围：** 本文保留已删除历史计划中的 Task 4、Task 6 设计基线，以及 Task 7 的可选候选设计；历史原文只通过 Git 历史查阅，实现状态以代码、测试和对应 Jira 工作项为准。

## 1. 设计约束

本文只做设计，不新增 CLI 入口、真实 Jira / GitHub adapter 或发布副作用。设计必须遵守以下边界：

- Jira、Git、GitHub / CI 仍是各自事实源；AgenticOps 只保存执行事件、任务证据和发布审计引用。
- 高风险写操作必须经过策略门禁、显式人工确认和结构化审计。
- 真实 Jira 或 GitHub 写操作必须具备本地 fake 合同测试，并经过策略、权限和显式人工确认门禁。
- 更新采用 latest-only；回滚只用于本地安装失败或新版本不可用，不恢复旧版本补丁线。
- stdout 只输出结构化 JSON，失败必须包含稳定错误码和可执行人工动作。

## 2. Task 4：拉取请求证据职责边界

### 2.1 推荐方案：保留两个契约，复用同一证据写入内核

保留 `write-evidence` 和 `write-pr-evidence` 两个操作契约，并实现两个清晰的 CLI 入口；底层可以共用证据构造、任务上下文读取、策略校验和事件写入代码，但不能把两个契约合并成一个含义模糊的“大证据操作”。

| 操作 | 事实来源 | 负责内容 | 允许阶段 | 不负责的内容 |
| --- | --- | --- | --- | --- |
| `write-evidence` | Jira 任务、运行事件、本地证据模板 | 任务阶段证据、阻塞说明、完成证据和任务级审计主体 | `takeover_started`、`development_completed`、`blocked` | 不创建或更新 PR，不替代 CI / Review 事实 |
| `write-pr-evidence` | GitHub PR、CI、Review 读取结果、运行事件 | PR URL、CI 结论、Review 结论、风险摘要与任务证据关联 | `pr_created`、`ci_passed`、`review_approved` | 不创建 PR、不修改 CI / Review，不裁决审查结论 |

`write-pr-evidence` 的最小输入为 `workspace`、`agentic_run_id` 和已验证的 `pr_url`；其输出应包含 PR 事实摘要、`audit_reference`、当前阶段和下一步动作。它可以通过现有受控 Jira 评论原子操作写入任务证据，也可以追加本地事件，但必须沿用真实 Jira 写入确认和策略门禁。PR 本身仍只通过 GitHub 读取接口获取事实，不把本地摘要当作 GitHub 事实源。

两个操作共用以下约束：

- run、workspace、任务所有权和操作阶段必须先校验。
- 写入失败必须保留失败事件，不能把本地报告标记为已提交审计。
- 重试只能重试证据写入，不重复创建 PR 或重复执行其它高风险动作。
- 事件中区分 `audit_target`、`audit_submitted`、`audit_reference` 和 PR 事实字段。

### 2.2 备选方案

如果研发工程师选择统一入口，则删除 `write-pr-evidence.yaml`，在 `write-evidence` 中增加可选 `pull_request` 输入块，并扩展允许阶段和输出字段。该方案减少命令数量，但会让任务证据和 PR 证据共享一套阶段规则，后续更容易误把本地 PR 摘要当作 GitHub 事实。除非明确希望减少操作入口，否则不推荐。

### 2.3 验收标准

- 命令注册、契约和帮助信息三者一致。
- 普通任务证据无需提供 `pr_url`；PR 证据缺少 `pr_url` 时稳定返回 `missing_pr_url`。
- 两个命令都能在 fake 流程中生成结构化事件，并能验证 Jira 写入门禁未被绕过。
- `write-pr-evidence` 不产生 create/update PR、merge、push 或 CI 写入副作用。

## 3. Task 6：更新、回滚和兼容治理

### 3.1 版本关系模型

release manifest 同时描述 CLI 和资产，不允许仅凭一个版本字符串推断兼容性。建议增加以下字段：

```json
{
  "version": "latest CLI version",
  "asset_version": "latest asset version",
  "min_cli_version": "minimum CLI version",
  "min_asset_version": "minimum asset version",
  "asset_source": {
    "kind": "repository",
    "repository": "tapstate/agentic-ops",
    "ref": "release ref",
    "path": "install-resources/basic"
  },
  "compatibility_policy": "exact_pair",
  "migration_required": false,
  "blocked_operations": []
}
```

`asset_source` 只描述来源和可信边界，不代表 CLI 自动信任任意 URL。远程产物仍必须通过 checksum；来源变化必须在 manifest 和审计事件中可见。

### 3.2 已确认的最低兼容承诺

研发工程师已确认选择 `exact_pair`：CLI 版本与资产版本必须与 manifest 声明的版本对完全一致；当前兼容策略不承诺跨多个 CLI / 资产版本组合的长期兼容。若 `migration_required=true` 或当前版本对不是 latest pair，诊断、`update check`、`update apply` 和 `update rollback` 仍可执行，受影响业务操作按 `blocked_operations` 阻断。

这与 latest-only 方向一致，也避免在尚未有迁移框架时承诺隐含兼容。若需要更宽松的 `same_major` 或向后兼容窗口，必须另行定义版本比较、迁移脚本、保留周期和测试矩阵。

### 3.3 更新应用和回滚流程

`update apply` 应按以下顺序执行：

```text
读取 manifest
-> 校验来源、版本关系、资产清单和 checksum
-> 下载到按版本隔离的 staging 目录
-> 校验二进制和资产完整性
-> 保存 previous metadata、previous binary path、previous asset path
-> 原子切换 current metadata 和激活路径
-> 输出 audit_reference 与下一步 doctor
```

`update rollback` 只读取本地保存的 previous metadata 和路径，不重新从远程下载。它必须验证回滚目标仍存在且 checksum / manifest 关系有效；成功后恢复 CLI、资产和 current metadata，失败时返回 `rollback_state_missing`、`rollback_target_invalid` 或 `rollback_failed`，并给出人工恢复动作。

实施前缺口（历史，已由 3.7 节实施结果取代）：当时实现会直接覆盖 `bin/agentic-cli`，因此设计要求改为版本隔离的激活路径或保留上一份二进制副本，不能只保存 previous version 字符串。

### 3.4 必要更新阻断

在统一 CLI dispatch 前增加只读 `required update guard`，但排除 `help`、`version`、`doctor`、`preflight`、`update check`、`update apply` 和 `update rollback`。当当前安装状态标记某操作在 `blocked_operations` 中时，返回：

```json
{
  "ok": false,
  "code": "required_update_blocked",
  "required_human_action": "请先执行 update check 和 update apply"
}
```

Guard 只阻断受影响操作，不把普通推荐更新扩大为全局阻断，也不在 guard 内执行网络请求。

### 3.5 资产安装校验

`assets install` 必须读取源目录中的资产 manifest，校验资产版本、最低 CLI 版本、来源声明和当前 CLI 版本；校验失败不得写入 `current.json`。安装成功后保留资产 manifest 和校验摘要，供 `doctor`、rollback 和审计读取。

### 3.6 验收标准

- `update check` 输出 `compatibility_state`、`migration_required` 和受影响操作。
- `update apply` 在切换失败时不改变当前激活版本，并保留可验证的 previous state。
- `update rollback` 能在 fake 安装目录恢复 CLI、资产和 current metadata。
- 不兼容版本只阻断 manifest 指定的操作；诊断和恢复入口仍可用。
- `assets install` 拒绝缺失或不匹配的资产 manifest。

### 3.7 实施结果

- release manifest 已支持 `min_cli_version`、`min_asset_version`、`asset_source`、`compatibility_policy` 和 `migration_required`；显式 `exact_pair` 清单必须完整声明版本对与来源。
- release manifest 不允许省略 `compatibility_policy`，版本字段不得包含路径分隔符；本地 manifest 只能更新与当前运行 CLI 同版本的资产，不能伪装成已替换 CLI。
- `update check` 会把兼容状态保存到安装目录 `.local/update-state.json`，统一 CLI dispatch 只读取该本地状态，不在门禁内发起网络请求。
- 远程 `update apply` 先把二进制和资产包解包到版本目录并校验资产 manifest，再保存上一二进制路径与 SHA-256，最后原子替换激活二进制和资产 metadata。
- `update rollback` 只使用本地上一状态并校验 SHA-256；不会重新下载远程产物。
- `assets install` 已读取源目录 `manifest.json`，并在复制和写入 `current.json` 前校验资产版本、CLI 版本、来源和 `exact_pair` 策略。

## 4. Task 7：可选 GitHub Release 制品发布与审计

本节只讨论创建或更新 GitHub Release 资产，不等同于源码通过 PR 合入 `main` 的正式发布；源码发布以 `docs/architecture/source-release-workflow-design.md` 为准。

### 4.1 推荐治理模型

- **责任人：** AgenticOps 项目维护者或明确指定的 release owner；AIAgent 只能准备发布计划。
- **授权：** 每次发布都必须有显式人工确认，CLI 同时校验策略门禁、目标仓库、release version、资产清单和 checksum；凭证由受控 GitHub 客户端提供，CLI 不保存 token。
- **事实源：** GitHub Release 记录发布对象和产物；本地结构化事件记录操作输入、版本、资产 checksum、确认人和 `audit_reference`。
- **回滚：** CLI 只负责本地安装回滚和阻断后续更新；删除或改写已发布 GitHub Release 不作为自动回滚动作，由 release owner 人工处理。
- **实现边界：** 只有项目后续明确选择实现该可选能力时，才补充契约、权限判断、资产集合校验、fake GitHub client 和审计事件；真实 GitHub 写入仍只能在显式 opt-in、策略允许且取得人工确认后执行。

### 4.2 `release_publish` 契约

最小输入：

- `release_version`
- `target_repository`
- `asset_manifest`
- `checksums`
- `confirmation_reference`

最小输出：

- `release_version`
- `target_repository`
- `asset_count`
- `checksum_digest`
- `audit_reference`
- `agentic_next_action`

稳定失败码至少包括：`release_confirmation_required`、`release_owner_mismatch`、`release_repository_mismatch`、`release_asset_missing`、`release_checksum_mismatch`、`release_policy_gate_required`、`release_publish_failed` 和 `release_audit_write_failed`。

该操作只能创建或更新受控 GitHub Release 资产，不负责构建、不负责推送代码、不负责合并、不负责修改 Jira。真实 GitHub 写入必须显式 opt-in，并通过策略、权限和人工确认门禁；fake client 覆盖 create、upload、权限失败、资产缺失和审计失败。

### 4.3 验收标准

- release 版本、目标仓库、资产清单和 checksum 任一不匹配时，在外部写入前失败。
- 没有人工确认和策略允许时不调用 GitHub 写接口。
- 成功 fake 发布输出可关联的 `audit_reference`；失败输出人工补救动作。
- 发布操作不引入常驻服务或恢复发布 shell 业务逻辑。

## 5. 当前状态与后续决策门

1. `write-pr-evidence` 已确认保留独立契约和独立 CLI 入口，并完成实现。
2. 兼容承诺已确认采用 `exact_pair`，更新、回滚、资产来源和必要更新阻断已完成实现。
3. Task 7 的 GitHub Release 制品发布不是当前源码发布链路，也不是正式使用阻塞项；只有出现明确制品页需求时，才重新确认 release owner、授权、审计、回滚和是否需要 shell 包装。
4. 不引入 Web 控制台或后台常驻服务；标准操作通过隔离接口和项目 AI 工作空间本地文件协作，资料按 Jira 编号分类。
5. 研发工程师确认版本化设计或修复计划后，可以通过 `task_execution` 策略范围自动推进提交、推送任务分支和创建目标为 `develop` 的 PR，并统一停在拉取请求审查。`master`、`main`、`develop`、`release/*` 及同类保护分支禁止自动推送；合并、发布、Git Tag、范围变化或授权失效仍需独立人工确认。
6. 本地 Jira 编号目录承载完整任务审计、运行轨迹、证据、反馈和交接材料；Jira 卡片只回写关键结论和稳定引用，后续再评估独立审计服务。
7. GitHub Release 制品页不是当前源码发布链路；它只是可选的二进制和 checksum 展示 / 下载入口。当前不实现，只有明确需要面向用户提供制品下载页时再决策。
8. Jira transition 采用“适当容错+严格标准”：profile 声明稳定 ID 时必须优先使用并校验来源、目标状态；仅在没有 ID 且名称唯一、来源状态和目标状态均匹配时允许受控兜底。候选重复、目标状态不符、当前不可用或事实回读不一致时阻断。

当前直接推进项是使用已发布版本完成真实 AO 试运行，并把已证实问题转化为下一版本改进设计和实施计划。

## 6. 两个待决策事项的说明

### 6.1 GitHub Release 制品页是什么

它不是源码发布，也不是 PR 合入 `main`。它指的是在 GitHub 上额外创建一个 Release 页面，把已经构建好的 `agentic-cli` 四平台二进制、checksum 和版本说明作为可下载附件展示。

当前源码发布已经通过 PR、Merge commit、Tag、安装资源和本地审计完成，不需要 GitHub Release 页面才能成立。制品页只有在以下需求出现时才有价值：

- 希望研发工程师从 GitHub 页面直接下载某个版本的二进制。
- 希望外部安装脚本从 Release 附件获取固定版本，而不是从仓库路径获取。
- 希望把版本说明、资产清单和下载入口集中展示。

引入它会增加 Release 创建 / 更新权限、资产上传、checksum 校验、发布审计和人工回滚责任。当前建议：**暂不实现，继续使用仓库安装资源和现有源码发布链路；出现明确下载页需求时再单独立项。**

### 6.2 Jira `transition` 语义冲突是什么

Jira `transition` 是从当前状态执行的一次业务动作。例如标准流程中的“开始执行”“请求决策”“完成任务”，在不同 Jira 项目里可能分别叫 `Start Progress`、`开始处理` 或其它名称，并且 transition ID 也可能不同。

常见冲突有四类：

1. **同名不同义**：两个 transition 都叫“完成”，但一个进入 `Done`，另一个进入 `待审查`。
2. **同义不同名**：标准动作都是“开始执行”，不同项目使用不同名称和 ID。
3. **名称重复**：当前状态下存在两个同名候选，Agent 无法靠名称唯一定位。
4. **状态可见性变化**：同一个 ID 在当前状态不可用，或目标状态已被管理员改动。

已确认的裁决顺序：

```text
读取当前 Jira 状态和可用 transition
→ 使用 profile 中声明的 transition ID
→ 校验来源状态、目标状态和标准动作语义
→ 三者一致才允许执行
→ ID 缺失、候选重复、目标状态不符或当前不可用时阻断
→ 请求流程负责人更新 profile 或确认映射
```

方案比较如下：

| 方案 | 做法 | 风险 |
| --- | --- | --- |
| A. ID 严格模式 | profile 必须配置 ID；ID 不存在就停止 | 最安全，需要维护每个项目的映射 |
| B. ID 优先、唯一名称兜底 | 有 ID 用 ID；没有 ID 时仅允许唯一名称且目标状态匹配 | 兼容性较好，但名称变更会触发阻断 |
| C. 名称或目标状态模糊匹配 | 根据相似名称、顺序或状态猜测 | 可能把任务推进到错误流程，不建议 |

已确认采用 **A 为默认、B 作为受控兼容模式、禁止 C**。实际执行前必须把使用的 ID 或名称、来源状态、目标状态、解析模式和 profile 版本写入本地 Jira 编号审计目录。
