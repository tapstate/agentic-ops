---
name: test-task-to-pr-e2e
description: Run one explicitly confirmed real Jira task through an isolated developer workspace and a separately launched developer AI to a real GitHub pull request review, then launch an independent reviewer AI and collect the complete friction retrospective. Use when an AgenticOps maintainer needs to validate the deployed developer experience with one Jira key. Stop before any merge, Jira Done transition, release, tag, protected-branch push, or unapproved scope change; fail closed before external access when a required ao-work atomic capability is missing.
metadata:
  workplane: maintainer
---

# 测试真实任务到 PR 全链路

只在 `maintainer` 工作面使用。完成一次性全链路配置后，任务运行入口只有 Jira key；测试身份、Project Profile 和预期确认人从该配置读取，Jira 卡片和 Runtime 能确定的字段不得逐项询问。用户只承担隐藏凭据输入、设计审查、风险决策以及最终 PR 审查，不确认准入摘要、通用方案摘要或内部标识。

## 停止线

- 未收到本次真实测试授权前，不读取 `~/.agentic-ops`、业务工作空间、凭据、Jira、GitHub 或其它本机身份。
- 先查询 `ao-work capability list|show`。`workspace_init`、正式 `takeover_task`、信息分析、方案分级、任务审计、Jira Comment/Worklog、Git 提交、任务分支推送和 GitHub PR 创建必须都有 `implemented` 原子能力；任一项为 `capability_gap` 时停止，不让 AI 直接运行等价命令绕过。内部 `task_start` 不是用户入口，也不能作为接管前置步骤。
- 每次 `ao-work` 调用只根据 JSON 的 `ok`、`status`、证据字段和结构化 `agentic_next_action` 推进。`executor` 只表示当前步骤执行者，不改变 `task_ownership.task_owner`；现役 `ownership_effect` 只能为 `none`。未知 executor/action、未列入 `allowed_operations` 的下一操作、缺失 required inputs 或要求 `stop_workflow=true` 时停止。只在 `retry_gate.allowed=true` 时允许按同一 `retry_key` 重试一次；必须先回读状态、改变输入并记录 retry 事件，耗尽后转人工，绝不自动循环。
- 测试终点固定为真实 PR 等待审查；禁止 merge、Jira Done、release、tag、保护分支直推、强推和历史改写。

## 执行

1. 首次使用时准备一次性非敏感全链路配置：

```sh
ao-maint integration prepare-task-to-pr-e2e-config \
  --agent-id <agent-id> \
  --project-profile <profile> \
  --expected-confirmer <name>
```

配置不包含 token；每次任务不得用命令参数覆盖身份。变更配置必须走独立修改和审查流程。隔离工作空间必须位于 AgenticOps 源仓库与 `~/.agentic-ops` 之外。

2. 只传 Jira key 执行无外部访问的能力预检：

```sh
ao-maint integration preflight-task-to-pr-e2e <ISSUE-KEY>
```

用稳定 main 安装的 developer `ao-work` 再次查询能力目录。若必要原子能力未实现，输出准确 capability id、当前状态、缺少的确定性门禁和建议实现位置，然后停止；不得创建半初始化业务工作空间或访问 Jira。
3. 能力齐备后，向用户展示本次读取与真实副作用边界，获得 Jira 读取和隔离工作空间初始化授权；随后创建隔离目录并运行 `ao-work workspace init`。让用户通过终端隐藏输入完成唯一 Jira 账户授权，并确认配置指定的身份、Project Profile、源码仓库和执行身份。不要把 token 放入参数、prompt、日志或结果包。
4. 运行 `ao-work workspace preflight`，随后在隔离业务工作空间启动独立 developer AI。developer AI 必须先执行 `ao-work takeover <ISSUE-KEY>`；Runtime 自动判断新接管、接纳存量或恢复，完成 Comment、必要 Status transition 和本地状态回读。不得先调用内部 `task start`，也不得要求用户提供授权参数。接管成功后再分析 Jira、Project Profile 和业务源码，把语义分析写入工作空间普通 JSON，并调用 `ao-work task intake assess --issue-key <KEY> --agentic-run-id <RUN> --input-file <相对JSON>`。Runtime 校验 Jira/Profile/Runtime 精确值、源码证据摘要和干净 HEAD，自动补齐确定性字段。必要信息仍缺失时只按同一 `retry_key` 改变输入后重试一次；事实完整时直接形成方案，不增加准入确认。
5. 调用 `ao-work task solution classify --issue-key <KEY> --agentic-run-id <RUN> --input-file <相对JSON>`。Runtime 按固定风险标志和证据分级：
   - L1：信息完整、范围明确且风险可控，展示完整设计并进入设计审查。
   - L2：方案可执行，但含用户选择、真实外部副作用或非平凡风险，逐项进入风险决策。
   - L3：触及架构、公共合同、安全边界、数据迁移或已确认设计，由 AI 先修改设计并重新分析，之后仍进入设计审查。
   - L4：事实冲突、必要信息无法补齐、权限或能力不足，停止并转人工。
   不调用已经删除的 intake/solution 通用确认命令。Jira/Profile 快照、源码 HEAD、证据、范围、风险或方案变化后旧结论失效，必须重新分析和分级。
6. 设计或风险决策确认后，developer AI 加载工作空间 `AGENTS.md` 和 `$run-task-to-pr-test`，逐步消费 `agentic_next_action`。开放式代码理解、方案设计和实现由 AI 完成；Jira、Git、GitHub、验证、证据和门禁由 Runtime 判定。
7. 每个原子步骤完成后，根据实际结果执行返回的唯一下一动作，直到 `stop_workflow=true`、L4、重试耗尽或到达 PR 审查。方案、范围、外部事实或批准摘要变化后，重新执行信息分析和方案分级，不沿用旧结论。
   从正式接管到 PR 审查保持配置指定的同一 `task_owner`。reviewer、人工确认、Runtime 或项目工具参与步骤不构成转派。如需转派，记录 `task_transfer` 能力缺口并停止，由人决定；本 Skill 不预设转派方案。
8. developer AI 完成代码与验证后，启动独立 reviewer AI，以只读方式检查 Jira 验收条件、已确认准入摘要、分级方案、diff 和验证证据。reviewer 只能给出 `approve`、`request_changes` 或 `blocked`；不能修改代码、创建 PR 或代替用户批准高风险动作。`request_changes` 交回原 developer AI 修复、重新分析受影响信息并重新验证。
9. 仅在 reviewer 通过、最终 HEAD 验证通过且 `ao-work` 原子门禁允许时，执行提交、任务分支推送、真实 PR 创建、PR 回读、中文 Jira Comment、真实 Worklog 和写后回读。Worklog 必须结构化列出分析/实现/验证/复盘耗时并排除等待。
10. 在 PR 审查停止，生成 developer 结果包；用 `ao-maint integration accept-task-to-pr` 只读验收。保留隔离工作空间供人工审查，不自动清理。

## AI 启动边界

使用非交互 Codex 时，把 developer 与 reviewer 启动为两个独立进程和上下文。developer 的工作目录是隔离业务工作空间，允许 `workspace-write`；reviewer 使用同一 commit 的只读副本或 `read-only` sandbox。不要把 maintainer prompt、规则、状态、凭据或 Python import 注入任一子进程。

若当前 Codex 运行器无法证明两个上下文隔离、无法传回结构化结果、或需要用户再次登录，记录为 `automation_gap` 并停止。不得在 maintainer AI 当前会话中直接扮演 developer 或 reviewer 来伪造独立执行。

## 结果

最终必须给出真实状态 `ready_for_pr_review`、`blocked` 或 `failed`，以及 Jira/仓库/分支/commit/PR/CI/验证/授权回读。复盘逐项覆盖 `automation_gap`、`manual_friction`、`output_quality`、`unreasonable_process`；每个失败、重试、等待和人工干预都必须进入 finding、证据与优化候选，不能只写成功摘要。

如果因为 `ao-work` 原子能力不足而不能执行真实测试，结论必须列出无法实现的理由：缺少哪个操作、为何 AI 直接执行不具备门禁或回读、需要新增什么 Runtime 合同与测试。该结论是安全阻塞，不是全链路通过。
