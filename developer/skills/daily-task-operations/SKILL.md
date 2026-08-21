---
name: daily-task-operations
description: Use when a development engineer asks an AIAgent to handle daily Jira task operations in a developer business-project workspace — list own tasks, inspect a task, take over a task (with or without an issue key), or resume an interrupted takeover. Covers workspace detection from the current working directory, locating the installed ao-work entry, and the orchestration order and confirmation points for list/inspect/takeover/resume.
metadata:
  workplane: developer
---

# 研发日常任务操作

本 Skill 只在业务项目 AI 工作空间的 `developer` 工作面使用。它负责研发工程师的日常任务操作：查看名下任务、查阅任务信息、接管任务（含无编号自动候选）、恢复接管。完整任务链路（接管 → 信息分析 → 设计审查 → 实现验证 → 代码审查）由 `run-task-to-pr-test` 编排；本 Skill 负责统一接管入口，不替代完整链路 Skill。

## 1. 第一步：从当前工作目录识别工作空间

**从当前工作目录（cwd）开始，不要扫描全局安装目录猜测位置。**

- 当前目录（或其父目录）存在 `.agentic-ops/AGENTS.md` 且 `AGENTS.md` 指向 developer → 当前就是业务项目 AI 工作空间，直接使用。
- 当前目录是 `tapstate/agentic-ops` 源头仓库（存在 `.agentic-ops-source` 标记）→ 这是 maintainer 工作面，日常任务操作不适用；停止并说明。
- 两者都不是 → 停止，询问研发工程师目标业务项目 AI 工作空间路径；不要凭 `~/.agentic-ops` 或历史会话猜测。

## 2. 第二步：定位 ao-work 入口

- 优先使用工作空间绑定或安装身份提供的 `ao-work`；验证安装目录（`install-verify-branch.sh` 产物）同样可运行。
- `ao-work` 不在 PATH 时，按以下顺序定位：业务工作空间初始化记录 → 安装目录 `~/.agentic-ops/bin/ao-work` → 验证安装目录。**不要默认假设 `~/.agentic-ops` 存在**（测试/验证环境可能安装在其他位置）。
- 执行任何命令前，先 `ao-work capability list` 确认目标能力为 `implemented`；`capability_gap` 按中文 `next_action` 处理，不得调用旧命令。

## 3. 日常操作编排

所有命令在目标业务工作空间内执行（必要时带 `--workspace-root <path>`）。

### 3.1 查看名下任务

```sh
ao-work jira list
```

- 契约：只列分配给当前用户的任务、按 `ORDER BY priority DESC, updated ASC` 排序、一页 10 个（`--max-results` 可调）。
- 结果 `ok=true` 时展示 `tasks`（key / summary / status / priority / updated）与 `total`；`total > returned` 说明还有更多，提示可调大 `--max-results`。
- 只读操作，无授权要求。

### 3.2 查阅任务详情

```sh
ao-work jira inspect --issue-key <KEY>
```

- 展示 issue 的 key / summary / status / issue_type / assignee。
- 只读操作，无授权要求。

### 3.3 接管任务（有编号）

```sh
ao-work takeover <KEY>
```

- Runtime 从安装身份读取 `agent_id`；缺失时按 `install_identity_missing` 处理，提示 `ao-work auth`。
- 研发工程师明确说“接管 <KEY>”即授权本次事实明确的常规接管。Runtime 在 run 确定后绑定稳定内部授权摘要；不得要求研发工程师查看、复制或确认 plan ID、摘要 ID 或授权参数。
- Runtime 自动返回 `takeover_kind=new_takeover|accept_existing_task|resume_takeover`。后两种在 Jira 评论中明文提示“不是新接管”，无需 AI 先选择另一个接管命令。
- 成功后必须已回读 `takeover_comment_id`，进入本地 `takeover_started` 阶段。随后连续执行信息分析和方案分级，只在设计审查或真实风险决策处暂停。
- 新工作空间和用户指引只使用本节顶层入口，不展示任何兼容调用方式。

### 3.4 接管任务（无编号，自动候选）

```sh
ao-work takeover
```

- 不带 `issue_key` 时，Runtime 只读返回候选列表（`selection_required: true`、`candidates` 按优先级+更新时间排序），**不写 Jira**。
- 把候选列表（key / summary / priority）展示给研发工程师，**由研发工程师选择目标任务**后，再用 3.3 的完整命令接管。AI 不得擅自选择任务执行接管。
- 候选为空时提示当前名下无待处理任务。

### 3.5 只读恢复诊断

```sh
ao-work task resume [--issue-key <KEY> | --agentic-run-id <RUN>]
```

- 都不传时取本地最近可恢复记录，包括 `takeover_started` / `blocked` 业务阶段和 Comment、Status、本地收口尚未全部完成的接管操作。
- 这是只读恢复诊断入口。研发工程师直接说“接管 <KEY>”时仍使用 3.3，由 takeover 自动判断是否恢复并留下明文 Jira 轨迹。
- 该命令不写 Jira，只校验 Jira Assignee、状态映射与本地状态后输出执行上下文。`takeover_recovery` 必须来自 Runtime 统一读取器，不能由 Skill 根据零散文件重新推断。
- 负责人不一致（`assignee_changed`）或阶段不允许（`resume_stage_not_allowed`）时，按失败码提示人工核对，不自动放行。

## 4. 硬边界

- 所有 Jira 可见内容使用中文；命令、字段名、issue key、状态名保留英文。
- 真实 Jira 写（接管评论和状态流转）必须绑定研发工程师明确的“接管 <KEY>”指令；事实明确时不再增加通用二次确认。所有权、状态、范围或外部结果不明确时进入风险决策。
- 信息分析、带来源补全和方案分级正常连续推进；固定暂停点是设计审查、代码审查和风险决策。
- developer 不读取、写入或清理 Agentic Jira Custom Field；运行信息由 Jira Comment 和本地 task state 承载。
- 无编号接管只列候选，不擅自选择任务；正式接管必须带 key，并由 AIAgent 在内部绑定用户接管指令。
- 能力目录是「能否调用」的唯一事实源：先 `capability list`，`capability_gap` 停止并按 `next_action` 处理。
- 本 Skill 不替代 `run-task-to-pr-test`（完整任务链路）与 `jira-task-collaboration`（评论/工作日志/描述回写）；需要时按对应 Skill 编排。
- 不读取 `.env`、凭证或隐藏文件；不在业务工作空间修改 AgenticOps 源头。
