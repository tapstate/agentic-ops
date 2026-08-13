---
name: guard-story-quality
description: Detect and enforce AgenticOps project-maintenance and development-engineer story quality gates. Use before or after source-maintenance changes, before commit, or whenever code may alter protected behavior, acceptance conditions, story documents, tests, standards, skills, rules, Runtime, installation, or release flows.
---

# 守护项目故事质量

只在 `source_maintenance` 模式使用。不要自行判断或绕过故事影响，先调用 Python Runtime。

## 检查影响

修改前检查当前工作树：

```sh
agentic-cli story impact --change-source worktree
```

提交前检查暂存内容：

```sh
agentic-cli story impact --change-source staged
```

结果命中以下任一失败码时立即停止连续自动化：

- `maintenance_story_impacted`：代码命中已确认故事的保护路径。
- `maintenance_story_revision_required`：故事、注册表或验收条件被修改。
- `maintenance_story_acceptance_failed`：固定验收未运行或失败。
- `maintenance_story_mapping_missing`：治理范围内路径没有故事映射。

停止后只允许读取 diff、解释影响、运行已有验收和请求公司员工指导员确认。不得继续扩大修改、提交、推送或创建 PR。

## 记录确认并验收

公司员工指导员确认当前影响报告后，使用 Jira 评论或等价记录的稳定引用：

```sh
agentic-cli story approve \
  --change-source staged \
  --impact-id <impact_id> \
  --authorization-reference <confirmation-reference>

agentic-cli story verify --change-source staged
```

再次执行 `story impact --change-source staged`。只有 `approved=true` 且 `acceptance_status=passed` 才允许继续提交。

Git 内容变化会生成新的 `impact_id`，旧确认和验收立即失效。禁止使用 `--no-verify`、修改 Hook、伪造确认文件或让 AI 猜测缺失映射。
