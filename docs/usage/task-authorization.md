# 任务授权指引

本指引是项目工作空间中的**脚本接管与授权**流程，不要求先在 Agent 对话中创建任务。它从本地任务列表为空开始，使用 `workflow/task.py` 加载一个 Jira 任务的本地执行状态，再在方案确认后用 `workflow/authorization.py` 签发 `task_execution` 授权。

这些脚本不读取或修改 Jira 内容：Jira 仍是任务事实源。执行前，应通过已配置的 Jira 客户端读取任务号、任务类型、负责人、状态、准入事实和验收要求；不要把本地 `init` 当作 Jira 接管或状态流转的替代品。

本地方案确认只在 Workflow 检查点核验，原生 Git/Jira/PR 操作不再由通用 Hook 拦截。所有状态写命令（record、仓库 add/update/prepare/cleanup/record-result、block、activate/deactivate、grant/revoke、Jira prepare/complete、CI watch/record-fix）必须携带 `--expected-run-id "$task_run"`；advance 另带 `--expected-stage <当前阶段>`。命令必须使用已核对的绑定；task_run 从 init/status 输出固定，reset 后重新读取，不能在失败重试时自动替换。

## 1. 前提与变量

在已初始化的项目工作空间中执行。`agenticops_root` 是中央产品根目录，`project_workspace` 是业务项目工作空间；两者不能混用。以下以 TapData 缺陷 `TAP-123` 为例：

```sh
agenticops_root=/absolute/path/to/agentic-ops
project_workspace=/absolute/path/to/agenticops-tapdata
task_key=TAP-123
task_class=defect_fix
```

`task_class` 必须按 Jira 任务和当前项目准入规则选择：`defect_fix`、`feature_change` 或 `technical_task`。不确定时先查询清单，不要猜测：

```sh
python3 "$agenticops_root/workflow/task.py" checklist \
  --task-class "$task_class" --dir "$project_workspace"
```

## 2. 从空任务列表接管

先确认本地没有已登记的任务，再初始化指定 Jira 任务：

```sh
python3 "$agenticops_root/workflow/task.py" list --dir "$project_workspace"

python3 "$agenticops_root/workflow/task.py" init \
  --issue-key "$task_key" --task-class "$task_class" \
  --dir "$project_workspace"

# 将 init 输出的 run_id 固定到变量；恢复已有任务时先通过 status 核对。
task_run='从 init 输出复制的 run_id'

# 用 Jira 原生工具读取当前任务并保存为 jira-before.json；该快照必须含 source_ref、
# issue.key、issue.fields.issuetype 和 AgenticOps Version 字段。
python3 "$agenticops_root/workflow/jira_watermark.py" prepare --expected-run-id "$task_run" \
  --issue-key "$task_key" --input jira-before.json --dir "$project_workspace"

# 仅当 prepare 输出 outcome=ready 时，按 native_request 用 Jira 原生编辑工具覆盖一个字段；
# 随后重新读取 Jira 保存为 jira-after.json。不得夹带其它字段，也不得重复发送。
python3 "$agenticops_root/workflow/jira_watermark.py" complete --expected-run-id "$task_run" \
  --issue-key "$task_key" --outcome unknown --input jira-after.json --dir "$project_workspace"

# 仅 complete 输出 outcome=verified 才可进入 task_intake。
python3 "$agenticops_root/workflow/task.py" advance --expected-run-id "$task_run" --expected-stage waiting_takeover \
  --issue-key "$task_key" --note "已核对 Jira 任务归属、负责人、状态和任务类型" \
  --dir "$project_workspace"
```

`init` 创建当前 run 和 waiting_takeover 状态。先用 task.py snapshot 保存已读的 Jira 初始快照，再尽力同步 AgenticOps Version；水印失败或结果不明只列警告，不阻止 advance。未知写入保留同一 run，先回读原操作再决定恢复，不盲目重发。进入 task_intake 后，按项目规则准备 In Progress 同步；失败继续本地主流程。具体记录方式见[质量检查与证据](quality-checkpoints.md)。

若 `list` 已显示同一任务，不要再次 `init`。先用 `status --issue-key "$task_key"` 回读现有 `run_id` 和阶段；继续现有现场或按该 `run_id` 清理后 reset 是两个不同决定。若列表有其它 active 任务，后续每条命令都必须保留 `--issue-key "$task_key"`，不能借用其授权。

## 3. 完成准入并准备本地基线

使用脚本读取当前任务的必填项，再把已从 Jira 确认的事实逐项写入本地状态：

```sh
python3 "$agenticops_root/workflow/task.py" checklist \
  --issue-key "$task_key" --json --dir "$project_workspace"

python3 "$agenticops_root/workflow/task.py" record --expected-run-id "$task_run" \
  --issue-key "$task_key" --key problem_branch --value develop \
  --dir "$project_workspace"
```

上例的 `problem_branch` 只适用于 `defect_fix`。其它任务类型和其它必填项以 `checklist --json` 的 `missing` 为准；对每个缺项使用同一条 `record` 命令替换 `--key` 和 `--value`。缺失事实应按项目准入规则补充到 Jira，不能用占位值推进。

然后从项目 Profile 查询仓库分支、登记本任务的仓库绑定，并受控准备 worktree：

```sh
python3 "$agenticops_root/workflow/task.py" branch \
  --repo tapdata/tapdata --dir "$project_workspace"

python3 "$agenticops_root/workflow/task.py" repository add --expected-run-id "$task_run" \
  --issue-key "$task_key" --repo tapdata/tapdata \
  --base-branch develop --work-branch fix/TAP-123 \
  --scope "仅修复批读 SQL，不改数据库迁移或发布配置" \
  --verification "Maven mysql-connector 模块测试" \
  --dir "$project_workspace"

python3 "$agenticops_root/workflow/task.py" repository prepare --expected-run-id "$task_run" \
  --issue-key "$task_key" --dir "$project_workspace"

python3 "$agenticops_root/workflow/task.py" repository context \
  --issue-key "$task_key" --json --dir "$project_workspace"
```

每个目标仓库都要单独执行 `repository add`，再一次执行 `repository prepare`。该受控命令负责下载或校验 Source Pool、创建任务 worktree，并固化 `base_sha`；不得以直接 `git clone`、Source Pool 主工作树或远端页面信息替代。`repository context` 返回的 worktree、分支和 `base_sha` 是后续分析与授权的唯一基线。

### 已有分支/PR 的两条处理路径

发现已有开发成果时，用户可选择“继续已有分支/PR，完成当前任务”或“从当前目标分支创建新分支，重新处理当前任务”。用户已明确说继续 PR 或新建分支时直接执行相应路径，不重复询问方向。先检查当前 CLI 能完成后续步骤，再清理或 reset。

**继续已有分支/PR：**本地原 run 存在时，从原阶段恢复，使用 `repository context` 核对原基线；目标分支前进、CI 失败不要求 reset 或重开 PR。若只有旧分支/PR 而本地记录缺失，在项目准入后登记旧工作分支，核验本地分支 Head 与远端 PR Head 一致。从旧记录（包括 reset 的 `archive_repository_baseline` 事件）读取并验证原 SHA；原记录不可得时，Agent 可以用原生 Git 检查唯一共同祖先并明确将其记录为“本次接管比较基线”，不能声称找回原始创建点。祖先关系不明、多共同祖先、对象缺失或历史分叉无法解释时停止相关接管步骤。

```sh
# continuation_sha 为已核验比较基线，pr_head 为刚回读的 PR 完整 Head；逐仓绑定。
python3 "$agenticops_root/workflow/task.py" repository prepare \
  --issue-key "$task_key" --expected-run-id "$task_run" \
  --reuse-existing-branch --continuation-base "tapdata/tapdata=$continuation_sha" \
  --continuation-head "tapdata/tapdata=$pr_head" \
  --dir "$project_workspace"
```

工具核对显式 SHA 是本地 commit，且属于工作分支和当前目标分支的共同历史，再固化 base_sha 和来源事件；不会自动 rebase、移动分支或复制旧 CI。已有冻结基线只能复用，不能用此参数替换。没有历史参数时仍按已有冻结基线或当前目标分支准备，关系不符在创建前拒绝。旧 PR Head 的 CI 可作为该提交的事实回读，但新 run 的质量确认和最终验证仍按当前 run/完整 SHA 建立。

本地没有同名分支时，`--continuation-head` 是受控获取的必要输入：prepare 只从项目配置的 origin 获取已登记工作分支，先核对下载结果与预期 Head 完全一致，再从该提交创建本地分支/worktree。分支不存在、网络失败、Head 已变化均停止，不按同名猜测或覆盖；fork PR 不自动更换 origin，应先核对项目授权范围。已有本地分支时只核对其 Head，不覆盖其修改或提交。本地分支存在且继续原 run 时可省略该参数。

版本规划仍核验当前目标分支的完整 SHA，开发基线则独立校验当前 run 的 worktree、目录摘要和 base_sha→Head 祖先关系。两者不要求 SHA 相等；因此可以在历史基线接管后导入或修正版本规划，而不重新准备基线。目标分支改变、当前版本证据过期或开发基线漂移仍拒绝。

**新分支处理：**先保存未提交修改；脏 worktree 的 cleanup 会拒绝，不应强删。执行 `repository cleanup`（不带 `--delete-branches`）后，按旧 run 执行 `reset --stage task_intake`，回读新 run 并补齐其版本规划、快照和准入事实。若本来就没有基线或 worktree 且处于 task_intake，不必为更新登记额外 reset。

```sh
python3 "$agenticops_root/workflow/task.py" repository update \
  --issue-key "$task_key" --expected-run-id "$task_run" \
  --repo tapdata/tapdata --work-branch "fix/$task_key-retry" \
  --scope "当前目标分支上的修复范围" --verification "新分支的验证方式" \
  --reason "用户选择新分支处理，旧 PR 保留参考" --dir "$project_workspace"
```

update 只允许 active/task_intake、无冻结基线/worktree 记录和相关租约，检查新名称与本地、远端及任务绑定无冲突；远端不可达不能视作不存在。它保留仓库身份、endpoint 和目标分支，先撤销授权，再保存新登记和旧引用的审计记录；写入失败后回读，旧授权保持失效。旧分支与 PR 不变，是否关闭旧 PR 是另外的外部操作。随后正常 prepare/context、源码核验、方案确认与授权。通常不需要 purge；新分支上的修改必须重新验证。

准入事实、仓库登记和本地基线全部齐备后，进入方案审查：

```sh
python3 "$agenticops_root/workflow/task.py" advance --expected-run-id "$task_run" --expected-stage task_intake \
  --issue-key "$task_key" --note "准入事实、授权仓库和受控本地基线均已核验" \
  --dir "$project_workspace"
```

此时阶段应为 `design_review`。在这里根据已准备的 worktree 形成并人工确认方案；未确认前不得签发授权或修改代码。

TapData 缺陷使用 `recorded_decision` 质量模式：普通缺项先披露并继续无依赖的分析，进入实施前必须使用 [质量检查与证据](quality-checkpoints.md)的工具记录 Q1、Q2 用户处置，包括 Jira「已链接工作项」中的 Test 用例、修复前后用例及用户选定的验证方式。下文的授权命令不代替这些质量确认；基线不可靠或权限不足仍停止对应步骤。

## 4. 签发实施授权

研发工程师确认方案后，明确记录方案版本、实际 Agent 身份和授权有效期：

```sh
agent_id=codex
plan_version=v1

python3 "$agenticops_root/workflow/authorization.py" grant --expected-run-id "$task_run" \
  --issue-key "$task_key" --agent-id "$agent_id" --plan-version "$plan_version" \
  --ttl-hours 8 --dir "$project_workspace"

python3 "$agenticops_root/workflow/authorization.py" show \
  --issue-key "$task_key" --dir "$project_workspace"

python3 "$agenticops_root/workflow/task.py" advance --expected-run-id "$task_run" --expected-stage design_review \
  --issue-key "$task_key" --note "研发工程师确认方案 v1、仓库、分支、范围、验证和风险边界" \
  --dir "$project_workspace"
```

`grant` 只允许在 active 任务的 `design_review` 阶段执行。它将任务、当前 `run_id`、Agent、完整仓库集合、工作分支、`base_sha`、改动范围和验证方式写入 `authorization.json`；最后一条 `advance` 进入 `implementation`。`show` 的输出应与刚确认的方案逐项一致后，才允许继续。

## 5. 授权边界与失效

`task_execution` 记录方案确认并在进入实现、PR 审查、CI 验收和完成时核验 task/run、仓库集合、范围、基线、验证方式和有效期。Git/Jira/PR 操作依照用户授权由 Agent 原生工具执行，不宣称每次调用均经过 AgenticOps。合并、发布、Tag、保护分支写入、强推和历史改写仍需要独立明确授权。Jira 同步信息只用于准备与回读，不提供强制单次调用保证。

新增仓库、切换分支、改变 `base_sha`、修改范围或验证方式后，旧确认失效。新确认另绑定 fix_plan 摘要，方案正文改变也阻止后续推进；迁移前缺少摘要的旧确认保留原有绑定，不伪造其历史确认内容。使用带当前 `run_id` 的 `task.py reset --stage design_review` 回到审查阶段，重新准备必要基线并再次执行 `grant`；需要立即停止时执行 `authorization.py revoke`。不要删除 `.agenticops/` 目录来代替撤销或重置。

完成实现后仍须记录实际验证命令和退出结果，并通过 PR 审查与 CI 验证。任务授权不是完成、合并或发布的证明。

### 方案未变时显式续签

审查或 CI 等待导致授权到期，不必仅因此 reset。先用 `authorization.py show` 查看原确认，并用 `show --digest` 取得原授权摘要；向用户展示当前 run、方案、仓库范围和新的有效期，取得明确决定及可回查来源后，使用此前固定的摘要执行：

```sh
python3 "$agenticops_root/workflow/authorization.py" renew \
  --issue-key "$task_key" --expected-run-id "$task_run" \
  --expected-authorization-digest "$confirmed_authorization_digest" \
  --confirmed-by "$decision_maker" --confirmation-ref "$confirmation_source" \
  --ttl-hours 8 --dir "$project_workspace"
```

`renew` 仅适用于 active 任务的 `design_review`、`implementation`、`pr_review` 和 `ci_validation`。它核对原授权的任务、run、方案摘要、完整仓库绑定及当前 Project endpoint，只延长有效期并追加确认记录，不修改阶段、基线、方案、Agent 或质量证据。原授权摘要已变化、授权已撤销、方案或绑定改变、缺少旧方案摘要时拒绝；这些情况不能通过自动换参数重试解决。方案改变仍须按原流程重新设计确认。成功后重复提交同一摘要也拒绝，不能利用重放延长授权。确认来源是可回查记录，不提供用户身份认证。

## 相关文档

- [首次使用指引](../usage-guide.md)：安装与初始化项目工作空间。
- [权限与安全边界](../security/permissions.md)：凭证、服务器保护与 Workflow 检查点的边界。
- [v1 工程架构](../architecture/agenticops-v1-architecture.md)：多任务、多仓库和任务 worktree 的模型。
