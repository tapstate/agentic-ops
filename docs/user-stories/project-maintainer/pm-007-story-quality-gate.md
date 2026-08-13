# PM-007 守护两类项目故事质量基线

作为公司员工指导员，
我希望项目维护故事和研发工程师故事成为可执行的质量合同，
以便任何影响已确认行为的代码变更都会停止连续自动化，并重新完成影响确认和故事验收。

### 触发方式

```sh
agentic-cli story impact --change-source worktree
agentic-cli story impact --change-source staged
```

### 前置条件

- 当前工作位于 `tapstate/agentic-ops` 源头仓库并使用 `source_maintenance` 模式。
- 两类故事已在机器注册表中声明文档、保护路径、验收检查和证据要求。
- Git diff 可被 Runtime 确定性读取。

### 主流程

1. Runtime 读取项目维护故事和研发工程师故事注册表。
2. Runtime 根据 Git diff 和显式路径映射生成稳定 `impact_id`。
3. 变更命中保护路径时停止连续自动化，输出受影响故事、类别、验收检查和人工动作。
4. 直接修改故事、注册表或验收条件时，将其标记为故事修订，原连续授权失效。
5. 公司员工指导员确认影响报告后，以稳定 Jira 评论或等价记录批准同一个 `impact_id`。
6. Runtime 执行注册表声明的固定验收检查并写入本地证据。
7. 只有确认和验收均匹配当前 Git 内容指纹时，Git Hook 才允许继续提交。

### 输出

```json
{
  "ok": false,
  "operation": "story_impact",
  "status": "blocked",
  "code": "maintenance_story_impacted",
  "impact_id": "<sha256>",
  "impacted_story_ids": ["PM-007", "DE-004"],
  "required_human_action": "请公司员工指导员确认影响报告"
}
```

### 失败处理

- 未确认故事影响时返回 `maintenance_story_impacted`。
- 修改故事、注册表或验收条件时返回 `maintenance_story_revision_required`。
- 固定验收未运行或失败时返回 `maintenance_story_acceptance_failed`。
- 治理范围内代码没有故事映射时返回 `maintenance_story_mapping_missing`，不得由 AI 默认放行。
- Git 内容变化后旧 `impact_id`、确认和验收证据自动失效。

### 验收标准

- 注册表只允许 `project_maintenance` 和 `development_engineer` 两类故事。
- 每个故事都有唯一编号、人读文档、保护路径、固定验收检查和证据要求。
- 受影响故事未经确认时，pre-commit 阻断提交。
- 只确认但未验收时，pre-commit 仍阻断提交。
- 确认和验收只对完全相同的 Git 内容指纹有效。
- 治理路径缺失映射时以能力缺口阻断。
- 非 AgenticOps 仓库不因缺少故事注册表被项目 Hook 误伤。

### 保护行为

- 项目只维护项目维护故事和研发工程师故事，不建立第三类 AIAgent 故事。
- 故事是仓库内版本化质量合同，Jira 只管理实施计划、进度、确认和验收记录。
- 代码变更命中故事后必须停止连续自动化，原任务级授权不能隐式覆盖故事变化。
- AI、Skill、Shell、Git Hook 和发布脚本不得绕过 Python Runtime 的故事影响结论。
- 修改故事、保护路径或验收条件必须获得公司员工指导员确认。
- 验收检查必须来自 Runtime 固定白名单，注册表不得注入任意 Shell 命令。

### 审核问题

- 当前变更影响哪些项目维护故事和研发工程师故事。
- 影响来自保护路径还是故事定义本身。
- 当前 `impact_id` 是否与公司员工指导员确认记录一致。
- 固定验收是否覆盖每个受影响故事的保护行为。
- 是否存在治理范围内但未映射到故事的代码路径。

### 验收证据

- 未确认保护路径变更返回 `maintenance_story_impacted`。
- 注册表或故事文档变更返回 `maintenance_story_revision_required`。
- 未运行或失败验收返回 `maintenance_story_acceptance_failed`。
- 未映射治理路径返回 `maintenance_story_mapping_missing`。
- `agentic-cli story approve` 生成与 `impact_id` 绑定的本地确认记录。
- `agentic-cli story verify` 生成固定检查结果和本地验收证据。
- `.githooks/pre-commit` 在确认和验收均通过后才允许提交。

### 关联设计

- `docs/strategy/project-goals.md`
- `docs/user-stories/agenticops-user-stories.md`
- `standards/stories/project-quality.yaml`
- `rules/source-maintenance.md`
- `skills/guard-story-quality/SKILL.md`
- `runtime/src/agentic_ops/story_gate/`
