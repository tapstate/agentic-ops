# developer PR CI 持续监控与失败修复

`development_change_v2` 是显式选择的 developer 流程。旧 Profile 默认仍为 `development_change_v1`，升级安装不会静默改变既有业务工作空间。

## Profile 绑定

CI 配置可以放在业务工作空间的 `tapdata.local.yaml` overlay 中，使同一 `tapdata` 基础 Profile 能按当前绑定仓库声明精确检查名。配置必须来自项目 GitHub Actions 事实，不能照抄示例中的占位值：

```yaml
process_id: development_change_v2
ci:
  provider: github-actions
  start_timeout_seconds: 300
  completion_timeout_seconds: 600
  poll_interval_seconds: 15
  max_remediation_attempts: 3
  required_checks: [<GitHub 中精确且唯一的必需检查名>]
  workflows: [<精确 Workflow 名>]
  artifact_name_patterns: [<能够唯一匹配的 Artifact 模式>]
  report_parser: maven-failsafe-v1
  limits:
    max_archive_bytes: 52428800
    max_extracted_bytes: 209715200
    max_file_bytes: 20971520
    max_files: 2000
    max_depth: 20
  completion:
    finish_agent_run_on_pass: true
    transition_jira_done: false
```

manifest 必须携带相同 `process_id` 和 CI 配置，并显式允许 `github_ci_read`、`github_artifact_read`。Runtime 在任何 GitHub 读取前把 manifest 与当前有效 Profile 精确比较；不一致时要求重新确认，不从历史会话或其它仓库猜测映射。

`development_change_v2` 的 `pr_endpoint.ci_policy` 固定为 `detect_from_github_pr`。Runtime 先回读 PR 的 Base/Head，再从 GitHub 读取 Base 提交的 `.github/workflows/*.yml|yaml` Blob 并与已确认的 Workflow 名精确匹配：Base 没有任何 GitHub Actions Workflow 时输出 `ci_status=not_required` 并结束；匹配 Workflow 会由无条件 `pull_request` 或任务分支 `push` 触发时进入 CI 监控；Workflow 树截断、读取失败、名称缺失/重复、触发语义混合，或包含尚不能等价执行的 `paths`/`branches` 条件时转人工。项目名和聊天声明都不能替代这份 GitHub 证据。

## 原子命令

```sh
ao-work task-run probe-ci --manifest <相对路径>
ao-work task-run fetch-ci-artifact --manifest <相对路径>
ao-work task-run parse-ci-report --manifest <相对路径>
ao-work task-run record-ci-remediation \
  --manifest <相对路径> \
  --failure-event-id <ID> \
  --commit-sha <SHA> \
  --new-head-sha <SHA> \
  --authorization-reference <当前manifest授权>
```

Skill 只消费结构化返回值并按 `agentic_next_action` 编排。它不直接调用 `gh`、不自行解压、不解析 Runtime 状态文件，也不重置截止时间或修复预算。

只有 GitHub PR 判定为 `required` 才建立两个不可重置的独立门限：首次观察起 5 分钟内必须看到 CI 测试执行；观察到执行后 10 分钟内必须结束。任一门限超时都返回人工介入。测试成功即结束本次运行；测试失败必须先分类，只有唯一记录为 `ci_code_defect` 且 `retry_safe=true` 的代码缺陷事件可以进入受限自动修复。依赖、环境、Runner、Workflow、配置、报告不可信及未知失败均由人工介入，不能消耗修复预算。

## 状态与安全

CI 状态位于 `.agentic-ops/tasks/<ISSUE>/runs/<agentic_run_id>/ci/`，绑定 Issue、run、Profile 配置摘要和当前 Head。相同 Head 只追加观察；Head 变化必须先有授权内修复记录和可信远端分支回读，否则返回 `ci_head_changed_externally`。

Artifact 按内容识别 ZIP、TAR 或 TAR.GZ，逐项拒绝绝对路径、`..`、链接和特殊文件，并限制压缩包、展开总量、单文件、文件数和深度。解析器只读取普通单链接的 `failsafe-summary.xml`、`TEST-*.xml` 和 `.txt`，拒绝 DTD、实体、损坏 XML与统计冲突；`.txt` 只记录相对路径和摘要，不把原始日志写入 Jira。

CI 通过或 GitHub PR 明确判定无需 CI，只结束本次 AIAgent 开发闭环：`current_stage=completed`、`ci_status=passed|not_required`、`agentic_next_action=none`。它不自动合并 PR、不把 Jira 置为 Done、不发布、不打 Tag，也不取消项目另行要求的专业门禁。

当前版本化 Failsafe Fixture 是脱敏合成样例，不冒充 AO-76 Jira 附件。附件正文受控读取能力补齐后，仍需增加真实格式一致性 E2E；在此之前不得声称 Jira 附件样例已完成验收。
