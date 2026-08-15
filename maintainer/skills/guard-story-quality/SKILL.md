---
name: guard-story-quality
description: Detect and enforce AgenticOps project-maintenance and development-engineer story quality gates. Use before or after source-maintenance changes, before commit, or whenever code may alter protected behavior, acceptance conditions, story documents, tests, standards, skills, rules, Runtime, installation, or release flows.
metadata:
  workplane: maintainer
---

# 守护项目故事质量

只在 `maintainer` 工作面使用。不要自行判断或绕过故事影响，先调用维护 Runtime。

## 检查影响

修改前检查当前工作树：

```sh
ao-maint story impact --change-source worktree
```

提交前检查暂存内容：

```sh
ao-maint story impact --change-source staged
```

结果命中以下任一失败码时立即停止连续自动化：

- `maintenance_story_impacted`：代码命中已确认故事的保护路径。
- `maintenance_story_revision_required`：故事、注册表或验收条件被修改。
- `maintenance_story_acceptance_failed`：固定验收未运行或失败。
- `maintenance_story_mapping_missing`：治理范围内路径没有故事映射。

停止后只允许读取 diff、解释影响、运行已有验收和请求公司员工指导员确认。不得继续扩大修改、提交、推送或创建 PR。

## 记录确认并验收

公司员工指导员确认当前影响报告后，只能使用当前对话中的明确人工确认：`user-confirmation:<KEY>:<impact-id>`。末段必须逐字等于当前 `impact_id`，不能用任务级授权、旧影响编号或任意非空文本代替。maintainer 工作面没有 Jira 评论回读能力，因此 `jira-comment:<KEY>:<id>` 即使格式正确也必须拒绝，旧审批记录不能放行。

```sh
ao-maint story approve \
  --change-source staged \
  --impact-id <impact_id> \
  --authorization-reference user-confirmation:AO-11:<impact_id>

ao-maint story verify --change-source staged
```

再次执行 `story impact --change-source staged`。只有 `approved=true` 且 `acceptance_status=passed` 才允许继续提交。

任意非空字符串不构成人工确认。Git 内容变化会生成新的 `impact_id`，旧确认和验收立即失效。禁止使用 `--no-verify`、修改 Hook、伪造确认文件或让 AI 猜测缺失映射。

## 保持信任链

提交时必须确认 `core.hooksPath` 指向 Git common directory 中带有 `AGENTIC_OPS_TRUSTED_HOOK_LAUNCHER_V1` 标记的入口。该入口从已接受 `HEAD` 加载版本化 Hook；Hook 再用 `HEAD` Runtime 检查隔离 index 快照。发现门禁实现有未暂存差异时立即停止，不能执行工作树 `ao-maint`。

发布时只接受刷新后的 `origin/main` 基线 Runtime 检查固定 candidate 快照。遇到 `release_story_gate_baseline_upgrade_required` 时，先通过受保护 `main` 的独立人工审查 PR 安装基线；遇到 `release_story_gate_trust_root_changed` 时，信任根变更同样改走独立人工审查，不能重试自动 publish 或让 candidate 自证。

本地 Hook 只提供防误操作和快速反馈，不能抵抗拥有本机 Git 控制权的人。不要把 Hook 通过表述为服务器安全证明；硬门禁还必须依靠无 bypass 的 `main` Ruleset 强制至少 1 个独立人工批准、最后推送者不能自批、dismiss stale approvals 和解决全部 review threads。这样即使 candidate 删除仓库内门禁调用，也不能自动合并；发布基线继续做确定性复检。

## 首次安装门禁基线

父提交没有新版 story Runtime 时，Git common directory 的 trusted launcher 固定执行旧 `HEAD` Hook，无法自动执行 staged candidate。不要声称首次 AO-11 提交已受新版 Hook 保护，也不要把 launcher 改成信任候选代码。

首次迁移只能在公司员工指导员确认当前 `impact_id` 后执行一次显式人工流程：暂存完整候选，直接用候选 `ao-maint` 完成 staged `impact -> approve -> verify -> impact`；记录 index tree，确保其后不再变化；仅该笔提交用单次 `git -c core.hooksPath=/dev/null commit` 避开旧 Hook；随后以 `HEAD^...HEAD` 复检同一 `impact_id`、确认/验收状态和 tree，并立即运行 `workflow_install_trusted_hooks`。只有父提交缺少 story Runtime、候选正安装首个基线时可使用；新基线进入 HEAD 后不得再次使用此例外。首次进入远端 `main` 仍依靠独立人工审查 PR，不能自动 publish。
