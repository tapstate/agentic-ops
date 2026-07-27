# 反馈闭环

## 1. 目的

反馈闭环是 AgenticOps 的持续改进机制，用于在 AIAgent 完成一个任务、阻塞交接或到达标准流程终态时提交任务级审计记录，并在后续按需分析失败、阻塞、重复人工确认、有效经验和规则缺口。

第一阶段反馈通道只做分析和建议，不允许 AIAgent 自动修改 AgenticOps 源头规则。

## 2. 流程

```text
Go CLI 执行操作
-> 产生结构化事件日志
-> 到达完成、阻塞或交接节点
-> AIAgent 提交任务级审计记录到 Jira 卡片、审计服务或目标仓库证据链
-> 研发工程师或流程负责人审查任务审计记录
-> 维护者按需按 `run_id`、任务类型、失败码、时间范围或 `workspace` 聚合分析
-> AIAgent 生成 AgenticOps 改进建议
-> 人确认后更新 AgenticOps 规则 / 手册 / contracts / Go CLI
```

反馈闭环不只记录失败，也负责发现可固化经验。AIAgent 在具体环节中形成的有效处理方式，必须先以安全摘要进入事件、任务审计记录或反馈建议；只有重复出现、边界清晰、输入输出稳定后，才能建议升级为原子操作、运行手册、工作流配置、策略或模板。

`workspace` 聚合不是必经上报动作，也不要求生成日报。它只是维护者做趋势分析时可选的查询维度。单个任务的审计记录必须在任务完成、阻塞或交接时及时提交，不能等待后续批量报告。

## 3. 事件位置

本地事件日志必须写入具体项目 AI 工作空间；任务级审计记录还必须提交到至少一个外部或仓库内事实源。

```text
<project-ai-workspace>/
  .agentic-ops/
    runs/
      2026-07-21/
        TAP-123-takeover-20260721103012-a8f3/
          events.ndjson
          summary.json
          evidence.md
    feedback/
      bundles/
        TAP-123-takeover-20260721103012-a8f3.md
      reports/
        2026-07-21.md
        2026-07-21.json
```

`~/.agentic-ops` 不保存具体任务运行日志。

任务级审计记录的提交目标按优先级选择：

1. Jira 卡片：任务接管、阻塞、完成、证据和清理结果应优先写回任务卡片。
2. 审计服务：当团队部署独立审计服务时，AIAgent 应提交同一份脱敏审计摘要。
3. 目标仓库：如果流程要求仓库内保留证据，应写入受控证据位置或关联拉取请求证据链。
4. 项目 AI 工作空间：本地 `runs/`、`feedback/bundles/` 和 `feedback/reports/` 只作为运行记录与诊断材料，不能替代应回写的任务事实源。

## 4. 事件结构

事件日志使用 NDJSON，每条事件只记录安全摘要。

```json
{
  "timestamp": "2026-07-21T10:30:12+08:00",
  "workspace": "tapstate",
  "agent_id": "agent-local-7f31a2b",
  "run_id": "TAP-123-takeover-20260721103012-a8f3",
  "issue_key": "TAP-123",
  "assignee": "dev@example.com",
  "current_agent_id": "agent-local-7f31a2b",
  "task_type": "task_takeover",
  "task_class": "bug_fix",
  "process_id": "development_change_v1",
  "operation": "takeover_task",
  "current_stage": "takeover_gate",
  "next_action": "ask_owner",
  "ok": false,
  "code": "missing_target_repo",
  "duration_ms": 842,
  "human_gate": false,
  "requires_human_action": true,
  "review_gate": null,
  "review_decision": null,
  "retryable": false,
  "redo_from_stage": "takeover_gate",
  "current_agent_id_cleared": false,
  "audit_target": "jira_issue",
  "audit_submitted": false,
  "audit_reference": null,
  "safe_message": "Jira 卡片缺少目标仓库信息"
}
```

不得记录：

- secrets
- tokens
- private keys
- 原始敏感日志
- 完整 Jira 描述
- 敏感代码片段

## 5. 任务审计内容

任务级审计记录必须覆盖：

- `workspace`
- `agent_id`
- `run_id`
- `issue_key`
- `task_type`
- `task_class`
- `process_id`
- 当前阶段和下一步动作
- 任务接管、恢复、阻塞、完成或交接结论
- 标准表单字段输出或缺失字段
- 代码变更摘要
- 验证命令和结果
- 专业审查结论、重试依据或重做起点
- 完成证据引用
- `current_agent_id` 是否已清理
- 残留风险和需要人工处理的动作

任务审计记录不得包含原始敏感日志。需要诊断时，应生成脱敏 `feedback bundle`，再由维护者判断是否提交到审计服务。

## 6. 反馈命令

第一阶段建议操作：

```sh
agentic-cli write-evidence --workspace tapstate --run-id <run_id>
agentic-cli release-agent --workspace tapstate --run-id <run_id> --issue-key TAP-123 --completion-evidence evidence.md
agentic-cli feedback bundle --workspace tapstate --run-id <run_id> --redact
agentic-cli feedback report --workspace tapstate --date 2026-07-21
agentic-cli feedback analyze --workspace tapstate --date 2026-07-21
agentic-cli feedback propose --workspace tapstate --date 2026-07-21
```

`feedback report` 是按需分析报告，不是每天必须生成的工作日志。第一阶段仍保留 `--date` 作为兼容过滤和报告命名参数；后续可扩展为 `--from`、`--to`、`--run-id`、`--issue-key`、`--task-type` 或 `--code`。

## 7. 分析报告内容

按需分析报告应包含：

- runs 总数。
- 成功数。
- 失败数。
- 阻塞数。
- 最常见失败码。
- 人工确认点。
- 专业审查退回。
- 重试次数和失败后仍未解决的节点。
- 重做来源阶段。
- 所有权冲突、`assignee` 变更和代理绑定丢失。
- 任务完成后未清理 `current_agent_id` 的记录。
- 重复问题。
- 可复用经验。
- 候选原子操作。
- 改进建议。

## 8. 失败处理

- 任务审计记录无法写入 Jira 卡片时，必须记录稳定失败码，并提示研发工程师检查权限、字段映射或人工补卡。
- 审计服务不可用时，不能阻断本地证据落盘，但必须记录服务不可用事件和后续补交动作。
- 目标仓库证据写入失败时，不得继续执行推送、拉取请求或完成清理。
- 发现敏感内容时，必须停止外部提交，生成脱敏版本或请求人工判断。
- 完成或交接后如果 `current_agent_id` 清理失败，任务不能视为已完成审计。

## 9. 变更门禁

反馈进入 AgenticOps 源头规则前必须经过：

```text
Observation -> Proposal -> Accepted Change
```

AIAgent 可以生成 proposal，但不得未经人工确认直接修改项目规则、AI 员工手册、操作契约、工作流配置或 CLI 运行时。
