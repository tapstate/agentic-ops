# PM-007 守护两类项目故事质量基线

作为公司员工指导员，
我希望项目维护故事和研发工程师故事成为可执行的质量合同，
以便任何影响已确认行为的代码变更都会停止连续自动化，并重新完成影响确认和故事验收。

### 触发方式

```sh
./maintainer/bin/ao-maint story impact --change-source worktree
./maintainer/bin/ao-maint story impact --change-source staged
```

### 前置条件

- 当前工作位于 `tapstate/agentic-ops` 源头仓库或独立 worktree，并通过根 AI 入口进入 maintainer 工作面。
- 两类故事已在机器注册表中声明文档、保护路径、验收检查和证据要求。
- Git diff 可被 Runtime 确定性读取。

### 主流程

1. Runtime 读取项目维护故事和研发工程师故事注册表。
2. Runtime 根据 Git diff 和显式路径映射生成稳定 `impact_id`。
3. 变更命中保护路径时停止连续自动化，输出受影响故事、类别、验收检查和人工动作。
4. 直接修改故事、注册表或验收条件时，将其标记为故事修订，原连续授权失效。
5. 公司员工指导员确认影响报告后，以 `user-confirmation:<KEY>:<impact-id>` 引用当前交互中的明确确认，并严格绑定同一个 `impact_id`；maintainer 没有 Jira 评论回读能力，不接受 `jira-comment` 引用或旧版审批记录。
6. Runtime 执行注册表声明的固定验收检查并写入本地证据。
7. Git 从 common directory 中不可入库修改的 trusted launcher 加载已接受 `HEAD` 的版本化 Hook；Hook 再用已接受 `HEAD` Runtime 检查隔离的 index 快照，而不是执行工作树或候选 Runtime。
8. 只有确认和验收均匹配当前 Git 内容指纹时，Git Hook 才允许继续提交。
9. `release` / `hotfix publish` 刷新 `origin/main`，从该提交创建可信基线快照，并用基线 Runtime 检查固定 candidate 快照；candidate `ao-maint` 不参与自证。

AO-11 首次安装新版门禁时，旧 `HEAD` trusted launcher 只执行旧 Hook，不能自动到达 staged candidate；这一笔提交不声称受新版 Hook 自动保护。公司员工指导员必须先显式确认 staged `impact_id`，候选 Runtime 对同一 index 完成 approve/verify/复检并锁定 tree，才可一次性绕过旧 Hook 创建基线提交；提交后对真实 commit range 复检同一 impact/tree 并立即安装新 trusted launcher。父提交已有 story Runtime 后，该迁移例外永久失效。

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
- 任意非空字符串、格式不合法的引用或没有绑定当前 `impact_id` 的对话确认均不能批准。

### 验收标准

- 注册表只允许 `maintainer` 和 `developer` 两类故事，稳定编号仍使用 `PM-*` 与 `DE-*`。
- 每个故事都有唯一编号、人读文档、保护路径、固定验收检查和证据要求。
- 受影响故事未经确认时，pre-commit 阻断提交。
- 只确认但未验收时，pre-commit 仍阻断提交。
- 确认和验收只对完全相同的 Git 内容指纹有效。
- 人工确认引用只接受与当前 `impact_id` 绑定的 `user-confirmation`；无回读能力时拒绝 Jira 评论引用。
- 治理路径缺失映射时以能力缺口阻断。
- 非 AgenticOps 仓库不因缺少故事注册表被项目 Hook 误伤。
- 未暂存篡改 Hook、launcher、Runtime 或固定验收入口时，pre-commit 在执行门禁前阻断。
- `maintainer/.local`、approval/evidence 目录或 JSON 叶子使用符号链接、特殊文件或逃出仓库 / candidate 真实路径时，Hook 与 release 必须在复制前失败，且仓库外 sentinel 不产生文件。
- staged Hook 变更由 Git common directory trusted launcher 加载的 `HEAD` Hook 和 `HEAD` Runtime 检查，不能把 candidate Hook 当信任根。
- `origin/main` 缺少新门禁时，发布以 `release_story_gate_baseline_upgrade_required` 失败关闭，不允许 AO-11 candidate 自证。
- 首次迁移测试使用真实 Git staged tree 与 commit range，证明显式人工 approval/acceptance 绑定的 impact 在提交后不变；文档不得声称旧 HEAD Hook 自动执行了 candidate 门禁。
- Hook、故事门禁 Runtime、注册表或发布脚本等信任根发生净变更时，自动 publish 以 `release_story_gate_trust_root_changed` 停止，改走受保护 `main` 的独立人工审查 PR。

### 保护行为

- 项目只维护项目维护故事和研发工程师故事，不建立第三类 AIAgent 故事。
- 故事是仓库内版本化质量合同，Jira 只管理实施计划、进度、确认和验收记录。
- 代码变更命中故事后必须停止连续自动化，原任务级授权不能隐式覆盖故事变化。
- AI、Skill、Shell、Git Hook 和发布脚本不得绕过 Python Runtime 的故事影响结论。
- 修改故事、保护路径或验收条件必须获得公司员工指导员确认。
- 验收检查必须来自 Runtime 固定白名单，注册表不得注入任意 Shell 命令。
- 版本化 `.githooks` 是受信 launcher 加载的策略源，不直接作为可自我证明的工作树可执行文件。
- 本地 Hook 是防误操作和快速反馈；拥有本机 Git 控制权的人仍可使用 `--no-verify` 或修改 Git 配置。硬门禁由无 bypass 的 `main` Ruleset 强制至少 1 个独立人工批准、最后推送者不能自批、dismiss stale approvals 和解决全部 review threads；即使 candidate 同时删除仓库内门禁调用，也不能自动合并。`origin/main` 发布基线提供确定性复检，不宣称单一本地 Hook 或仓库内脚本能抵抗恶意维护者。
- 首次安装或升级发布信任根必须分两阶段：先由受保护 `main` 的人工审查 PR 安装基线，再由新 `origin/main` 基线验证后续普通发布。

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
- `ao-maint story approve` 生成与 `impact_id` 绑定的本地确认记录。
- `ao-maint story verify` 生成固定检查结果和本地验收证据。
- Git common directory trusted launcher 从已接受 `HEAD` 加载 `.githooks/pre-commit`；攻击复现中 `HEAD ao-maint` 拒绝、工作树未暂存改为成功且 index 只含 README 时仍必须阻断。
- 发布 fixture 中 candidate `ao-maint` 即使无条件成功，`origin/main` 基线拒绝时 publish 仍返回 `release_story_gate_blocked`。

### 关联设计

- `docs/strategy/project-goals.md`
- `docs/user-stories/agenticops-user-stories.md`
- `maintainer/standards/stories/project-quality.yaml`
- `maintainer/rules/source-maintenance.md`
- `maintainer/skills/guard-story-quality/SKILL.md`
- `maintainer/runtime/src/ao_maint/story_gate/`
