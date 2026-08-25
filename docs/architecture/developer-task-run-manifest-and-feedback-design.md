# developer 任务执行包与 AO 问题上报闭环设计

## 1. 目标与事实基线

本设计对应 AO-95，修复 developer 工作面从任务设计审查进入实现时的两条阻断链：

1. 已完成 L1 方案、仓库关系和任务工作树确认后，仍需由研发工程师或 AI 手写完整 task-to-PR manifest、猜测验证命令约束并计算 Runtime 专用摘要；失败后又被要求逐项重新确认。
2. 用户触发「AO问题反馈」并确认完整缺陷报告后，`jira create plan` 的可选 `--run-id` 与必填受管 `--plan-file` 无法自动形成一致绑定，导致按错误提示重试仍返回 `jira_plan_path_not_bound`，无法完成 AO 建卡。

评估基线为刷新后的 `origin/develop` `cd59ec345bc9694b53b1d5768392d8688074fc25`；当前 `HEAD`、`origin/main` 与 `origin/develop` 一致。AO-95 Jira Description、来源任务 TAPSTATE-87 的脱敏会话记录和现役 Runtime 共同证明：

- 完整来源会话索引见 [AO-95 来源会话日志](ao-95-source-session-log.md)，包含 Codex 会话入口、用户消息原文、Runtime 错误时序和记录边界；
- `task solution classify` 能形成 L1 方案，但未保存可直接消费的结构化执行计划，也未记录设计确认后的工作项级连续执行授权；
- developer 只公开 `task-run open`，没有受管 manifest 生成、确认固化或 open 前预校验入口；
- `manifest_digest(...)` 与 Maven 验证白名单只存在于 Runtime 内部，用户无法可靠复现；
- AO-92 已把任务代码目录改为确认后的任务子工作树，但 `task-run open` 仍把 manifest 仓库绑定到 `agent.json` 的单一 `source_root` / 默认仓库，尚未消费 `confirmed_repository_branch_map` 中的实际工作树；
- `jira create plan` 在未显式传 `--run-id` 时自行生成 run，却要求调用者预先把同一 run 写进完整计划路径；`defect-feedback` Skill 示例没有传 `--run-id`，因此合同自相矛盾。

目标是把这两条链收敛为可验证的最小闭环：

```text
L1 完整设计与执行范围
-> Runtime 生成待确认执行包并预校验
-> 研发工程师一次确认设计和连续执行授权
-> Runtime 固化 canonical manifest
-> task-run open
-> 实现、验证、提交、任务分支推送和 PR

AO 问题报告
-> Runtime 自动分配 feedback run 与受管计划路径
-> 研发工程师确认建卡内容
-> plan -> apply -> readback
```

## 2. 设计原则与非目标

- 用户只处理会改变目标、范围、风险或外部授权的必要决策。仓库范围与完整设计属于用户决策；运行 ID、文件位置、命令安全参数规范化、批准计划摘要、manifest 摘要和确定性重试属于 Runtime 内部事实，不得升级为用户确认。
- AO 问题反馈先按时间顺序展示原始事实、错误码和来源会话日志，再给出归纳结论与候选修复；结论不得替代日志，也不得把重建记录冒充逐字原始记录。
- 设计审查与连续执行授权合并为一次面向业务语义的确认；不再把 manifest 字段、摘要、文件位置、提交、推送和 PR 分拆为多个用户问题。
- Runtime 只自动补全可由 Jira、Project Profile、安装身份、当前任务状态和 Git 工作树确定性回读的事实；验证意图和修改范围仍必须出现在设计审查正文中。
- `task-run open` 的现有 schema、摘要、验证白名单和 fail-closed 语义不放宽。生成器必须调用同一 developer Runtime 实现，不复制一套“近似算法”。
- maintainer 与 developer Runtime 继续硬隔离，不互相 import；`shared/` 继续只保存中立 schema 与协议说明，不新增跨工作面可执行公共代码。
- 本任务只闭合单一实际变更仓库。现役 manifest 是单仓合同；同时存在多个已准备变更工作树时返回结构化能力缺口，不把多仓任务伪装为单仓成功。
- 不自动合并 PR、不将业务 Jira 置为 Done、不发布、不创建 Tag、不推送保护分支，也不放宽仓库范围确认、代码审查或其它专业门禁。
- 不读取来源业务工作空间的真实凭证或隐藏状态来实施 AO-95；回归使用 maintainer 管理的 fixture 和 developer 黑盒入口。

### 2.1 门禁增多的演进根因

来源会话 `codex://threads/01a03728-c948-70b3-b599-21349fbe9ec9` 的用户消息与 Runtime 状态表明，交互增多不是单个门禁重复触发，而是三个独立阶段先后引入、却没有形成统一授权生命周期：

1. AO-92 为避免 `repositories.default` 替代真实任务范围，引入完整仓库/分支关系确认；该确认解决“改哪个仓库、从哪个分支开始”的实质决策。
2. AO-11 的 task gate 引入 L1 设计审查；该确认解决“如何修改、范围和风险是什么”的实质决策。
3. 更早的 task-run 只提供 `open --manifest`，要求外部调用者准备批准计划、manifest、验证 argv 和摘要，却没有从前两项已确认事实生成执行包的 Runtime 入口，也没有把设计确认记录成可供 task-run 消费的工作项级连续执行授权。

因此，原本应由 Runtime 完成的 Maven batch/offline 规范化、文件路径选择、计划 SHA 与 manifest digest 计算，在失败时被 Skill 当作“包内容变化”重新向用户确认。`stop_workflow` 的 fail-closed 语义是正确的，但调用层没有区分“用户语义发生变化”和“内部载体校验失败”，把所有停止都转译成了人工决策。

AO 反馈链另有一处叠加：`defect-feedback` 先确认完整报告，又要求确认 plan；示例同时手拼未提供的随机 run 路径，造成 `jira_plan_path_not_bound` 后再次请求用户选择纠正路径。这里既有重复授权，也有 create plan 参数合同自相矛盾。

收敛后的用户门禁矩阵为：

| 场景 | 是否需要用户决策 | 处理方式 |
| --- | --- | --- |
| 仓库/分支范围不确定或用户要修改建议 | 是 | 展示完整关系后确认一次 |
| L1 方案、范围、验证、外部动作与残留风险 | 是 | 合并为一次设计与连续执行授权 |
| Maven batch/offline、受管路径、run、SHA、digest | 否 | Runtime 确定性生成、校验和审计 |
| 已确认事实发生漂移或范围/风险扩大 | 是 | 展示新旧语义差异后重新决策 |
| AO 反馈建卡 | 是 | 先展示事实与会话日志，再展示总结；对最终建卡内容确认一次 |
| 同一内容的 plan/apply/readback | 否 | 复用前述确认，连续闭环并回读 |

## 3. L1 设计与执行计划合同

### 3.1 结构化执行计划

`task solution classify` 在保持现有 v1 输入兼容的前提下，接受可选 `execution_plan`：

- `verification`：验证 id、argv、工作目录和超时；
- `change_repository`：本次实际变更仓库 slug；
- `review_summary`：面向研发工程师的实现、验证和外部动作摘要。

新版本 Skill 形成方案时必须提供 `execution_plan`。Runtime 将它与当前 `solution_digest`、L1 级别、`scope.included/excluded`、当前 `agentic_run_id` 和来源 HEAD 一起保存。旧运行缺少该字段时仍可读取和恢复，但不能直接生成执行包；`task-run prepare` 返回由 AI 补齐并重新分级的明确下一步，不要求研发工程师手写 manifest。

Maven `mvn` / `./mvnw` 验证意图在分级时做确定性规范化：缺少 batch/offline 标志时补为 `--batch-mode --offline`（已有等价短参数时不重复），然后调用 `task-run open` 使用的同一验证命令校验函数。其它禁止命令、修改模式、路径越界或未知工具继续失败关闭。设计审查展示规范化前后差异，用户确认的是最终 argv。

### 3.2 一次设计审查内容

L1 设计审查必须一次展示：

- 根因与实现方案；
- 唯一实际变更仓库、固定工作树、来源/任务/目标分支及当前 HEAD；
- 包含与排除范围；
- 规范化后的完整验证 argv、工作目录和超时；
- 允许的 Jira read/comment/worklog、Git commit/任务分支 push、GitHub PR/read 及按 Profile 需要的 CI read 权限；
- 明确禁止的 merge、Jira Done、release、Tag、保护分支 push、强推和历史改写；
- 残留风险与会使授权失效的事实变化。

这份正文同时是设计确认对象和工作项级连续执行授权对象。内部 `solution_digest`、draft id、manifest digest 仅用于 Runtime 绑定，不能作为用户确认主题。

## 4. 受管 manifest 生命周期

### 4.1 公开入口

新增两个 developer Runtime 原子操作：

```sh
ao-work task-run prepare --issue-key <KEY>

ao-work task-run authorize \
  --issue-key <KEY> \
  --confirmed-by "<当前会话声明的确认人>" \
  --confirm
```

`prepare` 无外部副作用，不接受完整 manifest 或摘要参数。它读取当前任务的 L1 solution、仓库确认状态、已准备工作树、安装身份和 Project Profile，生成受管 draft 和完整 `confirmation_package`，并返回人工设计审查节点。

`authorize` 只在研发工程师明确确认当前 `confirmation_package` 后调用。用户不复制 draft id、plan SHA 或 manifest SHA；Runtime 从当前任务运行读取唯一待确认 draft，重新验证绑定事实，记录设计决策与连续执行授权，再原子输出：

- `inputs/agentic-ops/<issue>/<run>/approved-plan.md`；
- `inputs/agentic-ops/<issue>/<run>/task-to-pr.manifest.json`。

文件名和目录由 Runtime 决定，调用者不能越界或覆盖现有文件。成功输出 `manifest_path`、`authorization_reference`、`manifest_sha256` 和 `agentic_next_action=task_run_open`；Skill 随后自动调用现役 `task-run open --manifest <manifest_path>`，不再请求第二次确认。

### 4.2 确定性事实来源

生成器按以下来源填充 manifest：

| manifest 区域 | 唯一来源 |
| --- | --- |
| workspace / agent / execution_identity | 当前工作空间绑定与安装级身份回读 |
| issue / jira / issue_content_sha256 | 当前任务来源快照与必要 Jira 实时回读 |
| task_binding | Runtime 生成的批准计划文件与原始 UTF-8 SHA-256 |
| repository | `confirmed_repository_branch_map` 中唯一 `worktree_status=prepared` 的实际变更仓库及 Git 回读 |
| scope | 当前 L1 solution |
| verification | 当前 solution 的已规范化 `execution_plan.verification` |
| pr_endpoint / CI | 当前 Project Profile 与确认仓库 |
| permitted_external_actions | 版本化流程固定最小权限集合，并在确认包中逐项展示 |
| authorization | 本次确认声明、批准计划摘要和 developer `manifest_digest(...)` |

AO-92 的仓库确认表成为 task-run 的仓库事实源。`task-run open` 不再要求任务仓库等于 `agent.json` 默认仓库或 `source_root`；它必须精确验证准备工作树路径、仓库 slug、任务分支、基线 SHA、当前 HEAD 和工作树状态仍与当前任务绑定。默认仓库只能是分析候选，不能覆盖已确认任务仓库。

### 4.3 摘要和预校验

`authorize` 按以下顺序执行：

1. 重读当前任务、L1 solution、仓库映射、工作树 Git 事实、Profile 和安装身份；
2. 验证 draft 绑定摘要与上述稳定事实一致；
3. 生成批准计划并计算 `approved_plan_sha256`；
4. 填入授权引用、确认声明和确认时间；
5. 使用 `task-run open` 同一 `manifest_digest(...)` 计算 `confirmed_manifest_sha256`；
6. 调用同一 `validate_manifest(...)`、验证命令校验和工作空间/任务绑定预校验；
7. 全部通过后才原子发布批准计划和最终 manifest。

任何一步失败都不留下可被 `open` 接受的半成品。`manifest_digest_mismatch` 继续保护外部或修改后的 manifest；Runtime 自己生成的 manifest 必须在单测和安装黑盒测试中证明可直接 open。

### 4.4 漂移与恢复

以下变化使 draft 和既有连续执行授权失效，并要求重新 prepare 后展示一份新的完整确认包：

- Jira 内容、经办人或允许状态变化；
- `agentic_run_id`、L1 solution、范围、验证、Profile、执行身份变化；
- 确认仓库、工作树、任务/目标分支或当前 HEAD 变化；
- 权限集合、CI 策略或残留风险变化。

相同稳定事实下重复 `prepare` 返回同一 draft；重复 `authorize` 在最终文件和决策记录完全一致时幂等回读，不重复生成授权。失败输出区分“AI 可修正的执行计划缺项”“需要重新设计审查的事实漂移”和“Runtime 能力缺口”，不得把所有问题都转成不可安全重试的人工死锁。

## 5. AO 问题反馈建卡路径

### 5.1 根因修复

`jira create plan` 的计划文件参数改为同时支持：

- 推荐：安全单层文件名，例如 `--plan-file defect-create.json`；Runtime 生成或采用显式 `--run-id`，并解析到 `.agentic-ops/tasks/<PROJECT>/runs/<run>/jira-plans/<name>.json`；
- 兼容：现有完整受管路径；若调用者同时给出 `--run-id`，两者必须严格一致。

当 `--run-id` 省略时，Runtime 先生成 run，再构造受管目录；不再要求调用者预知随机 run。plan 输出继续返回真实 `agentic_run_id`、`plan_file`、`plan_id` 和授权引用，apply/readback 只接受这些原始回读值。

完整路径与 run 不一致时返回新的精确错误，说明冲突值和唯一纠正方式，并标记为可在输入改变后重试；不得再次给出与实际解析规则矛盾的占位路径。

### 5.2 Skill 闭环

`defect-feedback` Skill 改用安全文件名，不手工拼 `.agentic-ops/tasks/AO/runs/<run-id>`。报告固定按以下顺序组织：来源会话标识与可访问日志、按时间排序的用户消息和工具/Runtime 错误事实、相关本地证据文件、事实缺口与脱敏说明，最后才是原因总结、影响和候选修复。无法导出逐字日志时必须标注“可核验重建记录”，不能省略来源会话或只给摘要。

用户只在以下节点确认一次真实 Jira 写入：上述事实与会话日志、完整中文 summary、description、`repair_readiness`、缺失事实和建卡副作用。确认后 Skill 连续执行 plan、复用该确认形成 plan 授权、apply，并立即 readback；不得再要求用户确认 `plan_id`。结果不明确时只持同一 plan/attempt 回读。

AO 建卡只是 developer 工作面允许的 AgenticOps 问题反馈出口，不授予 developer 读取 maintainer 状态、接管 AO 任务或修改 AgenticOps 源码的权限。AO-95 的维护和实现仍只在独立 maintainer 工作面完成。

## 6. 实施范围

预计修改以下现役资产：

- developer Runtime：`task_gate.py`、`task_run/cli.py`、`task_run/protocol.py`、`task_run/service.py`，并新增独立 manifest 生成服务；
- developer Jira CLI：create plan 的 filename/run 解析与错误语义；
- developer TaskStore：draft、设计决策和连续授权的受管状态与幂等回读；
- developer Skill：`run-task-to-pr-test`、`defect-feedback`；
- 能力目录、操作合同、developer README 与必要的人读文档；
- Runtime、资源、安装边界和跨工作面协议回归测试。

不修改 shared manifest schema 的安全字段，不修改 maintainer Jira 任务边界，不修改业务项目源码，不从历史路径恢复兼容入口。

## 7. 验证与验收

目标测试至少覆盖：

1. TAPSTATE-87 等价 fixture 从 L1 solution、唯一 prepared worktree 和 Profile 生成完整确认包；默认仓库与实际 connector 仓库不同仍绑定后者。
2. 普通 Maven argv 在确认前确定性规范化为 batch/offline，禁止命令仍在任何子进程前阻断。
3. 用户一次确认后，Runtime 自动生成批准计划、授权引用和 canonical digest，最终 manifest 可直接 `task-run open`。
4. 用户不手写 schema、文件路径、批准计划 SHA 或 manifest SHA；Skill 不再逐项追问 commit、push、PR。
5. Jira、solution、身份、Profile、工作树、分支、HEAD、验证或权限任一变化都会使 draft/授权失效；相同事实重复执行幂等。
6. 多个 prepared 变更工作树明确返回单仓协议能力缺口，不选择默认仓库降级。
7. `jira create plan --project-key AO ... --plan-file defect-create.json` 在没有 `--run-id` 时成功生成受管 plan；apply/readback 闭合。
8. 来源会话中的完整路径、遗漏 `--run-id` 复现得到可执行纠正信息；跨任务、跨 run、symlink、越界和覆盖仍失败关闭。
9. `defect-feedback` 完整黑盒回归证明报告确认后能创建并回读 Fake AO Agentic 缺陷，且不加载 maintainer 资产。
10. 现有 `manifest_digest_mismatch`、`verification_command_forbidden`、计划路径隔离、摘要篡改和禁止动作测试继续通过。
11. 来源会话等价 fixture 的反馈正文先包含会话标识、按时序事实和原始错误码，再出现归纳结论；缺失逐字日志时明确标记记录边界。

最后执行固定完整验证：

```sh
bash maintainer/scripts/test-python-runtime.sh
bash maintainer/scripts/test-resources.sh
bash developer/tests/bootstrap/test_install_boundary.sh
bash maintainer/scripts/test-release-workflow.sh
```

代码变更形成后还必须运行 `ao-maint story impact` 的固定故事验收；当前 `develop` 通道最终形成未推送本地 commit，并在推送前进入代码审查。

## 8. 设计审查确认项与风险

本次需要确认：

1. 将 L1 设计审查与 task-to-PR 连续执行授权合并为一次完整确认，确认后自动生成 manifest 并 open；仓库范围确认等既有实质门禁不取消。
2. task-run 仓库绑定改用 AO-92 的唯一 prepared 任务工作树，不再绑定 `agent.json` 默认仓库；多仓仍明确阻断。
3. Maven 只做 batch/offline 的确定性规范化，并在确认包中展示；其它验证白名单不放宽。
4. AO 问题反馈的 create plan 改为由 Runtime 生成 run 与受管路径，用户不处理内部路径或 plan/run 绑定。
5. 设计确认后的连续授权覆盖本设计内实现、测试、文档/Skill/能力资产更新、固定完整验证、必要 AO-95 中文进度回写和 `develop` 本地 commit；保持未推送并停在代码审查。

主要风险：

- task-run 仓库绑定从工作空间默认值切换到任务确认状态，若旧任务没有 AO-92 仓库状态，必须明确要求恢复/补齐，不能静默回退。
- 自动 Maven 规范化改变最终测试 argv；因此差异必须在确认前展示，且不得自动添加依赖下载、安装、更新或网络参数。
- `confirmed_by` 仍是当前会话/协议包声明，不是 maintainer 独立验证的外部身份；输出和验收不得扩大为独立人工批准证明。
- 单仓 manifest 暂不覆盖真实多仓提交/PR；AO-95 只给出准确能力缺口，不在本任务扩展 shared schema。
- AO 建卡允许跨出业务 Project 创建专用反馈卡，但权限只限版本化 `Agentic 缺陷` 创建协议，不能扩大为 developer 接管或维护 AO 任务。

若实施需要修改 shared manifest schema、支持多仓 task-to-PR、放宽验证/摘要/外部动作门禁、改变保护分支规则、读取真实业务凭证或新增其它 AO 操作，视为范围扩大并重新进入设计审查或风险决策。
