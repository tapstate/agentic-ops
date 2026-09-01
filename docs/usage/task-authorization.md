# 任务授权指引

本指引是项目工作空间中的**脚本接管与授权**流程，不要求先在 Agent 对话中创建任务。它从本地任务列表为空开始，使用 `workflow/task.py` 加载一个 Jira 任务的本地执行状态，再在方案确认后用 `workflow/authorization.py` 签发 `task_execution` 授权。

这些脚本不读取或修改 Jira 内容：Jira 仍是任务事实源。执行前，应通过已配置的 Jira 客户端读取任务号、任务类型、负责人、状态、准入事实和验收要求；不要把本地 `init` 当作 Jira 接管或状态流转的替代品。

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

python3 "$agenticops_root/workflow/task.py" advance \
  --issue-key "$task_key" --note "已核对 Jira 任务归属、负责人、状态和任务类型" \
  --dir "$project_workspace"
```

`init` 创建该 Jira 任务的本地 `run_id` 和 `waiting_takeover` 状态；第一条 `advance` 才进入 `task_intake`。它们只加载本地执行上下文，不授权修改代码、提交、推送、合并或 Jira 状态流转。

若 `list` 已显示同一任务，不要再次 `init`。先用 `status --issue-key "$task_key"` 回读现有 `run_id` 和阶段；继续现有现场或按该 `run_id` 清理后 reset 是两个不同决定。若列表有其它 active 任务，后续每条命令都必须保留 `--issue-key "$task_key"`，不能借用其授权。

## 3. 完成准入并准备本地基线

使用脚本读取当前任务的必填项，再把已从 Jira 确认的事实逐项写入本地状态：

```sh
python3 "$agenticops_root/workflow/task.py" checklist \
  --issue-key "$task_key" --json --dir "$project_workspace"

python3 "$agenticops_root/workflow/task.py" record \
  --issue-key "$task_key" --key problem_branch --value develop \
  --dir "$project_workspace"
```

上例的 `problem_branch` 只适用于 `defect_fix`。其它任务类型和其它必填项以 `checklist --json` 的 `missing` 为准；对每个缺项使用同一条 `record` 命令替换 `--key` 和 `--value`。缺失事实应按项目准入规则补充到 Jira，不能用占位值推进。

然后从项目 Profile 查询仓库分支、登记本任务的仓库绑定，并受控准备 worktree：

```sh
python3 "$agenticops_root/workflow/task.py" branch \
  --repo tapdata/tapdata --dir "$project_workspace"

python3 "$agenticops_root/workflow/task.py" repository add \
  --issue-key "$task_key" --repo tapdata/tapdata \
  --base-branch develop --work-branch fix/TAP-123 \
  --scope "仅修复批读 SQL，不改数据库迁移或发布配置" \
  --verification "Maven mysql-connector 模块测试" \
  --dir "$project_workspace"

python3 "$agenticops_root/workflow/task.py" repository prepare \
  --issue-key "$task_key" --dir "$project_workspace"

python3 "$agenticops_root/workflow/task.py" repository context \
  --issue-key "$task_key" --json --dir "$project_workspace"
```

每个目标仓库都要单独执行 `repository add`，再一次执行 `repository prepare`。该受控命令负责下载或校验 Source Pool、创建任务 worktree，并固化 `base_sha`；不得以直接 `git clone`、Source Pool 主工作树或远端页面信息替代。`repository context` 返回的 worktree、分支和 `base_sha` 是后续分析与授权的唯一基线。

准入事实、仓库登记和本地基线全部齐备后，进入方案审查：

```sh
python3 "$agenticops_root/workflow/task.py" advance \
  --issue-key "$task_key" --note "准入事实、授权仓库和受控本地基线均已核验" \
  --dir "$project_workspace"
```

此时阶段应为 `design_review`。在这里根据已准备的 worktree 形成并人工确认方案；未确认前不得签发授权或修改代码。

## 4. 签发实施授权

研发工程师确认方案后，明确记录方案版本、实际 Agent 身份和授权有效期：

```sh
agent_id=codex
plan_version=v1

python3 "$agenticops_root/workflow/authorization.py" grant \
  --issue-key "$task_key" --agent-id "$agent_id" --plan-version "$plan_version" \
  --ttl-hours 8 --dir "$project_workspace"

python3 "$agenticops_root/workflow/authorization.py" show \
  --issue-key "$task_key" --dir "$project_workspace"

python3 "$agenticops_root/workflow/task.py" advance \
  --issue-key "$task_key" --note "研发工程师确认方案 v1、仓库、分支、范围、验证和风险边界" \
  --dir "$project_workspace"
```

`grant` 只允许在 active 任务的 `design_review` 阶段执行。它将任务、当前 `run_id`、Agent、完整仓库集合、工作分支、`base_sha`、改动范围和验证方式写入 `authorization.json`；最后一条 `advance` 进入 `implementation`。`show` 的输出应与刚确认的方案逐项一致后，才允许继续。

## 5. 授权边界与失效

有效的 `task_execution` 可覆盖当前授权 worktree 的 `git commit`、向同名授权工作分支的 `git push`、授权仓库的 PR 创建或更新、PR 评论处理和 Jira 评论。它不覆盖合并、发布、Tag、保护分支写入、强推、历史改写、删除 worktree/任务状态、Jira 状态流转、工时记录或任务字段编辑。

新增仓库、切换分支、改变 `base_sha`、修改范围或验证方式后，旧授权失效。使用带当前 `run_id` 的 `task.py reset --stage design_review` 回到审查阶段，重新准备必要基线并再次执行 `grant`；需要立即停止时执行 `authorization.py revoke`。不要删除 `.agenticops/` 目录来代替撤销或重置。

完成实现后仍须记录实际验证命令和退出结果，并通过 PR 审查与 CI 验证。任务授权不是完成、合并或发布的证明。

## 相关文档

- [首次使用指引](../usage-guide.md)：安装与初始化项目工作空间。
- [权限与安全边界](../security/permissions.md)：凭证、服务器保护与 Hook 的边界。
- [v1 工程架构](../architecture/agenticops-v1-architecture.md)：多任务、多仓库和任务 worktree 的模型。
