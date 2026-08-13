# 项目故事质量门禁

## 1. 目的

AgenticOps 通过项目维护故事和研发工程师故事管理长期验收条件。故事是仓库内版本化质量合同，Jira 是变更计划、进度、人工确认和验收记录的事实源。

代码变更影响故事时，Python Runtime 生成与 Git 内容绑定的 `impact_id` 并停止连续自动化。任务级连续执行授权不能隐式覆盖故事保护行为、验收条件或映射变化。

## 2. 两类故事

| 类别 | 主角 | 保护范围 |
| --- | --- | --- |
| `project_maintenance` | 公司员工指导员 | AgenticOps 架构、标准资产、安装、更新、回滚、发布和项目演进质量 |
| `development_engineer` | 业务项目工作空间所代表的研发工程师 | 安装、授权、任务接管、开发、验证、恢复、证据和任务审计质量 |

AIAgent、Skill、Python Runtime 和 `agentic-cli` 是研发工程师能力组成，不建立第三类故事。

## 3. 事实源

- 人读故事：`docs/user-stories/project-maintainer/`、`docs/user-stories/development-engineer/`。
- 机器注册表：`standards/stories/project-quality.yaml`。
- 本地确认：`.agentic-ops/story-approvals/<impact_id>.json`。
- 本地验收：`.agentic-ops/story-evidence/<impact_id>.json`。
- 团队确认与验收轨迹：对应 Jira 工作项 Comment 和 Worklog。

本地确认和验收文件由 Git 忽略，只用于恢复当前维护会话，不能替代 Jira 人工确认。

## 4. 门禁流程

```text
读取 Git diff
-> 校验故事注册表
-> 映射受影响故事
-> 生成 impact_id
-> 停止连续自动化
-> 公司员工指导员确认
-> 执行固定白名单验收
-> 同一 impact_id 回读通过
-> 允许提交
```

常用命令：

```sh
agentic-cli story impact --change-source worktree
agentic-cli story impact --change-source staged
agentic-cli story approve --change-source staged \
  --impact-id <impact_id> \
  --authorization-reference <jira-comment-reference>
agentic-cli story verify --change-source staged
```

Git 内容、注册表或受影响故事集合变化后会产生新的 `impact_id`，旧确认和验收自动失效。

## 5. 安全边界

- 注册表只引用 Runtime 固定验收检查 ID，不接受任意 Shell 命令。
- 直接修改故事文档或注册表时按故事修订处理。
- 治理路径没有故事映射时以能力缺口阻断，不允许 AI 默认放行。
- pre-commit 只在 AgenticOps 源头仓库存在故事注册表时启用该门禁，不影响使用同一 Hook fixture 的其它仓库。
- 禁止 `--no-verify`、临时修改 Hook、删除注册表或伪造本地确认记录。

## 6. 稳定失败码

- `maintenance_story_impacted`：代码命中保护路径，等待人工确认。
- `maintenance_story_revision_required`：故事或注册表发生修订。
- `maintenance_story_acceptance_failed`：验收未运行或失败。
- `maintenance_story_mapping_missing`：治理范围内路径缺少故事映射或注册表无效。
