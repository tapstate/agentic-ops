# 项目故事质量门禁

## 1. 目的

AgenticOps 通过项目维护故事和研发工程师故事管理长期验收条件。故事是仓库内版本化质量合同，Jira 是变更计划、进度、人工确认和验收记录的事实源。

代码变更影响故事时，Python Runtime 生成与 Git 内容绑定的 `impact_id` 并停止连续自动化。任务级连续执行授权不能隐式覆盖故事保护行为、验收条件或映射变化。

## 2. 两类故事

| 类别 | 主角 | 保护范围 |
| --- | --- | --- |
| `maintainer` | 公司员工指导员 | AgenticOps 架构、标准资产、安装、更新、回滚、发布和项目演进质量 |
| `developer` | 业务项目工作空间所代表的研发工程师 | 安装、授权、任务接管、开发、验证、恢复、证据和任务审计质量 |

AIAgent、Skill 和 Python Runtime 是两类故事的实现组成，不建立第三类故事。故事门禁属于 maintainer 工作面，只能由 `ao-maint` 执行；`ao-work` 不读取维护故事确认状态。

## 3. 事实源

- 人读故事：`docs/user-stories/project-maintainer/`、`docs/user-stories/development-engineer/`。
- 机器注册表：`maintainer/standards/stories/project-quality.yaml`。
- 本地确认：`maintainer/.local/story-approvals/<impact_id>.json`。
- 本地验收：`maintainer/.local/story-evidence/<impact_id>.json`。
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
./maintainer/bin/ao-maint story impact --change-source worktree
./maintainer/bin/ao-maint story impact --change-source staged
./maintainer/bin/ao-maint story approve --change-source staged \
  --impact-id <impact_id> \
  --authorization-reference user-confirmation:AO-11:<impact_id>
./maintainer/bin/ao-maint story verify --change-source staged
```

`--authorization-reference` 只接受 `user-confirmation:<KEY>:<impact-id>`：引用当前交互中的明确人工确认，末段必须与命令中的当前 `impact_id` 完全一致。maintainer 当前没有 Jira 评论回读能力，所以 `jira-comment:<KEY>:<id>` 不能证明评论存在或内容绑定，必须拒绝。

任意非空文本、任务级连续执行授权、旧 `impact_id` 或旧版 `jira-comment` 审批记录都不能打开故事门禁。本地批准记录会额外保存确认类型、Jira 工作项和记录标识；记录版本、格式或内容被篡改后按未确认处理。

Git 内容、注册表或受影响故事集合变化后会产生新的 `impact_id`，旧确认和验收自动失效。

### 首次门禁迁移

AO-11 之前的 `HEAD` 没有新版 maintainer Runtime，已安装的 trusted launcher 只会执行旧 `HEAD` Hook；它不会也不应自动改为执行 staged candidate。因此旧基线到新版门禁的第一笔提交**不受新版 Hook 自动保护**，不得声称 pre-commit 已完成硬门禁。

这一笔提交只允许使用一次性的显式人工迁移流程：先暂存完整候选，直接用候选 `ao-maint` 对 `--change-source staged` 依次执行 `story impact`、当前 `impact_id` 的人工 `story approve`、`story verify` 和最终 `story impact` 复检；记录 `git write-tree`，确认 index 此后没有变化，再以禁用旧 Hook 的单次 Git 配置创建提交。提交后必须对 `HEAD^...HEAD` 重新执行 `story impact`，确认仍是同一个 `impact_id`、`approved=true`、`acceptance_status=passed`，且提交 tree 等于此前记录的 index tree；随后立即运行 `workflow_install_trusted_hooks`，让后续提交固定从新 `HEAD` 加载 Hook。

该例外只在父提交确实不存在 `maintainer/runtime/src/ao_maint/story_gate/service.py`、当前变更正是安装首个基线且公司员工指导员已确认同一 impact 时成立。新基线进入 `HEAD` 后，禁止再次禁用 Hook、使用 `--no-verify` 或借“迁移”绕过门禁。远端 `origin/main` 的首次发布仍必须走受保护 main 的独立人工审查，不能由本地迁移结论替代。

## 5. 安全边界

- 注册表只引用 Runtime 固定验收检查 ID，不接受任意 Shell 命令。
- 直接修改故事文档或注册表时按故事修订处理。
- 治理路径没有故事映射时以能力缺口阻断，不允许 AI 默认放行。
- pre-commit 以 `.agentic-ops-source=maintainer` 识别 AgenticOps 源头；源头缺少 maintainer 故事注册表或 `ao-maint` 时必须阻断，不影响业务项目工作空间。
- `development-workflow.sh` 在 Git common directory 安装 trusted launcher，并把 `core.hooksPath` 指向该目录。launcher 从当前已接受 `HEAD` 读取版本化 `.githooks/pre-commit` / `pre-push`，因此 staged candidate 不能直接替换本次实际入口。
- pre-commit 拒绝门禁实现的未暂存差异，把 index 写成无引用临时提交，并在隔离 worktree 中检查。`HEAD` 已包含新门禁时执行 `HEAD` Runtime；AO-11 首次迁移只能按上一节显式人工流程完成，旧 trusted launcher 不会自动执行 staged Runtime，也不能因此取得 Hook 或发布信任。
- `maintainer/.local/story-approvals` 与 `story-evidence` 是快照外输入。Hook 和 release 在复制前逐级拒绝源目录与 candidate 目标中的祖先或叶子符号链接、非目录祖先、非普通 JSON 叶子，并校验物理路径仍在各自仓库 / 快照根内；发现异常分别以 `story_gate_local_state_unsafe` 或 `release_story_gate_local_state_unsafe` 失败，不能静默跳过或向仓库外写入。
- release / hotfix publish 必须先刷新官方 `origin/main`，分别创建 baseline 与固定 candidate 快照，用 baseline 的锁文件、launcher 和 Runtime 检查 candidate 范围。工作树或 candidate `ao-maint` 即使返回成功也不构成发布证据。
- `origin/main` 缺少新门禁时以 `release_story_gate_baseline_upgrade_required` 失败关闭。首次升级通过受保护 `main` 的独立人工审查 PR 安装基线，不能调用 candidate 自动发布；基线进入 main 后才恢复普通 publish。
- Hook、门禁 Runtime、锁文件、注册表或 release / hotfix 脚本等信任根发生净变更时，以 `release_story_gate_trust_root_changed` 停止自动 publish，必须独立人工审查。
- 本地 Hook 是快速反馈和防误操作层，不是对本机控制者的安全沙箱；`--no-verify` 或手工改 Git 配置仍能绕过本地执行。硬门禁最终依赖无 bypass 的 `main` Ruleset 强制至少 1 个独立人工批准、最后推送者不能自批、dismiss stale approvals 和解决全部 review threads。这样即使 candidate 同时删除 release 基线调用，也不能自动合并。`origin/main` 发布基线继续提供确定性复检；文档不得把单一本地 Hook 或仓库内脚本描述为不可绕过的信任根。
- 根 AI 入口只加载 maintainer 门禁；developer 安装和业务项目 AI 入口不得包含确认、验收或放行能力。
- 禁止 `--no-verify`、临时修改 Hook、删除注册表或伪造本地确认记录。

## 6. 稳定失败码

- `maintenance_story_impacted`：代码命中保护路径，等待人工确认。
- `maintenance_story_revision_required`：故事或注册表发生修订。
- `maintenance_story_acceptance_failed`：验收未运行或失败。
- `maintenance_story_mapping_missing`：治理范围内路径缺少故事映射或注册表无效。
- `story_authorization_reference_invalid`：确认引用不是受支持的可审计格式。
- `story_authorization_impact_mismatch`：对话人工确认引用没有绑定当前 `impact_id`。
- `release_story_gate_baseline_upgrade_required`：`origin/main` 尚无可独立执行的新门禁，必须先人工升级基线。
- `release_story_gate_trust_root_changed`：候选修改发布信任根，不允许自动 publish。
- `story_gate_local_state_unsafe`：Hook 发现故事确认或验收的源 / 目标路径含链接、特殊文件或路径逃逸。
- `release_story_gate_local_state_unsafe`：release 发现故事确认或验收的源 / candidate 路径含链接、特殊文件或路径逃逸。
