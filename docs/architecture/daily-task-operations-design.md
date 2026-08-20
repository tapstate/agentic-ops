# 研发日常任务操作设计（Daily Task Operations）

> 状态：设计评审中（T1）
> 关联决策：D-050
> 关联 Jira：待建卡（T2-T7）

## 1. 目标与范围

### 1.1 目标

研发工程师的日常任务操作极简化，以「一句话开始工作」为体验准绳：

```text
/ao-list                查看自己名下待处理任务（按优先级排序）
/ao-inspect TAP-123     查看任务详情
/ao-takeover TAP-123    接管任务并开始工作
/ao-resume              恢复上次已接管的任务
/ao-takeover            未给编号时，按优先级自动选择一个任务接管
```

用户面向的是 AI 研发员（Hermes + Skill + ao-work），不是直接操作 ao-work CLI。本设计补齐 AI 编排所需的底层能力，并修复当前阻塞主链路的运行时缺陷。

### 1.2 范围

- T2：修复 `task_worktree._run_git` 缺 `git` 前缀（阻塞池模式 task start/takeover 的主链路 bug）。
- T3：修复 `-h/--help` 不透传子命令帮助（work_cli.py 全局拦截）。
- T4：实现 `list_tasks`（查看名下任务，contract 已有：list-tasks.yaml）。
- T5：实现 `resume_takeover`（恢复接管，contract 已有：resume-takeover.yaml）。
- T6：接管简化（agent-id 自动读取）与无编号自动接管（优先级排序 + 用户确认）。
- T7：AI 入口技能重构（工作目录优先识别工作空间、developer 日常操作流程、ao-work 定位规则）。
- T1：本文档 + D-050 决策登记。

### 1.3 非范围

- 不做跨仓库 PR 集合（沿用 D-048 单主仓库 PR 语义）。
- 不做 `task_transfer`（仍为 capability_gap，转派由人决定）。
- 不做旧工作空间迁移命令（沿用 D-048 阶段二明确约束）。
- 不引入新运行时形态（无 daemon / Web 控制台）。
- 不新增 Jira 状态、不改变 Jira 工作流。
- 不弱化接管/写入的人工门禁（授权引用机制保留）。

## 2. 现状与根因

### 2.1 主链路不可用：task_worktree._run_git 缺 git 前缀（T2 根因）

测试执行（2026-08-19，test-ws/takeover-error.log）证据：

```text
ao-work task start TAP-12289
→ failed: runtime_failed
  消息: Runtime 处理失败：[Errno 2] No such file or directory: '-C'
```

错误链：`execute_task_start` → 池模式 `prepare_task_worktrees`（task_start.py:139-164）→ `task_worktree.py:144-155` → `_run_git` → subprocess 崩溃。

根因：`task_worktree.py` 的 `_run_git`（259-270 行）裸透传命令列表：

```python
subprocess.run(command, capture_output=True, text=True, ...)
```

但全部 8 处调用点（worktree add / rev-parse / config / worktree list / remove）都以 `["-C", ...]` 开头，缺 `git` 可执行名前缀。`command[0] == "-C"` 被 subprocess 当成可执行文件 → `FileNotFoundError`。

对照正确模式：
- `workspace_init/service.py:1246`：`subprocess.run(["git", *arguments], ...)` ✓
- `task_gate.py:1282`：`["git", "-C", str(root), *arguments]` ✓
- `task_worktree.py:264`：裸透传 ✗

四项固定验证未拦截的原因：`test_task_worktree.py` 全部注入 mock runner（`run_git=self._run_git`），mock 只看命令内容返回、从不经过真实 subprocess，缺 `git` 前缀永不暴露。真实运行即炸。

影响范围：池模式下 task start / takeover 的全部工作树操作（创建、复用校验、身份写入、回滚）都会崩，developer 主链路实际不可用。

### 2.2 命令探索困难：-h/--help 不透传（T3 根因）

`work_cli.py:225-227`：

```python
if "--help" in arguments or "-h" in arguments:
    write_json(success("help", usage=parser.format_help()))
```

对参数列表任意位置的 `-h/--help` 直接输出顶层 usage 并 return，不透传给 argparse 子解析器。实测 `ao-work task -h`、`ao-work jira -h` 都返回顶层帮助。AI 探索命令形态只能读源码，是「使用不方便」的直接来源之一。

### 2.3 日常操作缺口（T4/T5/T6 根因）

能力目录现状（operations.yaml）：

| 能力 | 状态 | 说明 |
| --- | --- | --- |
| `list_tasks` | capability_gap | 「Jira 待办列表目标契约，尚未迁移到 Python Runtime」，无现役命令 |
| `resume_takeover` | capability_gap | 「Jira 接管恢复目标契约，尚未迁移到 Python Runtime」 |
| `takeover_task` | implemented | `task takeover <key> --agent-id --authorization-reference`，要求手动传 agent-id 和授权引用 |
| `jira_inspect` | implemented | 单查 `jira inspect --issue-key <key>` |

契约与配置已就绪：
- `contracts/operations/list-tasks.yaml`：operation=list_tasks，task_type=task_listing，allowed_stages=[initialized]，无 human_gate，side_effects 必须不写 Jira。
- `contracts/operations/resume-takeover.yaml`：operation=resume_takeover，allowed_stages=[takeover_started, blocked]，失败码清单完整。
- tapdata profile 已配置 `task_query: project = TAP AND assignee = currentUser() AND statusCategory != Done ORDER BY updated DESC`（profile.yaml:14），list_tasks 的 JQL 来源已就绪。

差距只在 Runtime 实现与能力目录登记。

### 2.4 AI 入口技能缺位（T7 根因）

用户侧加载的 `agentic-ops` 技能（个人 Hermes 技能库）存在三个问题：

1. **内容以 maintainer 面为主**：故事门禁、发布流程、四项验证占大部分篇幅；developer 日常操作（看任务/查任务/接管/恢复）没有可执行的第一步指引。
2. **安装位置假设错误**：技能写死「`~/.agentic-ops` 是 stable main 的 developer-only sparse managed clone」，AI 找不到 ao-work 时按此约定去扫 `~/.agentic-ops`（实测 Path not found，见日志 419-435 行），而正确安装位置可能在工作空间绑定关系或验证安装目录。
3. **缺「从当前工作目录识别工作空间」的第一步**：用户明明在业务工作空间（有 `.agentic-ops/` + `AGENTS.md`），正确做法是从 cwd 识别 developer 工作空间、用该工作空间对应的 ao-work 入口，而不是扫全局安装目录。

随安装分发的版本化技能（developer/skills/ 下 5 个）不覆盖「日常操作编排」这一层——`run-task-to-pr-test` 面向已授权的完整任务链路（intake→solution→takeover→commit→push→PR），不是面向研发工程师的日常入口。

## 3. 方案

### 3.1 T2：修复 _run_git 缺 git 前缀

`task_worktree.py` 的 `_run_git` 改为与 `workspace_init/service.py` 同风格：

```python
def _run_git(command, *, timeout=None):
    return subprocess.run(["git", *command], capture_output=True, text=True, timeout=timeout, check=False)
```

一处修复覆盖全部 8 处调用点。同时补真实 subprocess 回归测试（PATH 假 git 或真实 git + 临时仓库），防止再次裸透传。测试模式参考 `source-pool-phase1-implementation.md` 的 PATH 假 git 手法。

### 3.2 T3：修复 -h/--help 透传

移除 `work_cli.py:225-227` 的全局 `-h/--help` 拦截，交给 argparse 子解析器：
- 顶层 `ao-work -h`：argparse 自然输出顶层帮助（JsonArgumentParser 需确认 add_help 输出形态）。
- `ao-work task -h` / `ao-work jira -h`：argparse 输出对应子解析器帮助。
- 需确认 `JsonArgumentParser` 的 help 输出是否仍保持 JSON 包装（现有 `success("help", usage=...)` 协议），必要时在 help 分支按 `args.group` 定位子解析器再 format_help。

### 3.3 T4：实现 list_tasks（查看名下任务）

**命令形态**：`ao-work jira list`（归属 jira 组，只读 Jira 事实；不建 task 组列表，避免与本地 TaskStore 语义混淆）。

**实现**：
- `JiraClient` 新增 `search_jql(jql, fields, max_results)`，走 `/rest/api/3/search/jql?jql=<urlencoded>&fields=...&maxResults=...`（12.1 节已实测：tapdata 站点 `/rest/api/3/search` 已移除，必须用 `/search/jql`）。
- JQL 来源：`profile.task_query`（缺省时用 `assignee = currentUser() AND resolution = Unresolved ORDER BY updated DESC`）。
- 输出：任务数组（key / summary / status / issue_type / priority / updated），含总数与截断提示。
- 能力目录 `list_tasks` → implemented，登记命令 `[jira, list]`。

**安全边界**：只读，side_effects 不写 Jira（契约已声明）；`total` 字段缺失时用 `len(issues)` 兜底（12.1 节实测坑）。

### 3.4 T5：实现 resume_takeover（恢复接管）

**命令形态**：`ao-work task resume`（归属 task 组，恢复的是本地执行上下文）。

**实现**（按 resume-takeover.yaml 契约）：
- 输入：`--issue-key`（优先）或 `--agentic-run-id`（二选一，缺省用本地最近 takeover_started 记录）。
- 校验链：本地 run 存在且 workspace 匹配 → Jira 回读（issue 存在、Assignee == currentUser、状态映射）→ 本地状态允许恢复（takeover_started/blocked）→ 输出执行上下文。该只读诊断不依赖 Agentic Jira Custom Field。
- 输出：workspace / issue_key / agentic_run_id / agent_id / task_class / previous_stage / current_stage / agentic_next_action。
- 复用 TaskStore（task_state/）+ JiraClient 现有能力，不新增大块状态机。
- 能力目录 `resume_takeover` → implemented，登记命令 `[task, resume]`。

### 3.5 T6：接管简化 + 无编号自动接管

**3.5.1 agent-id 自动读取**：`task takeover` 的 `--agent-id` 改为可选。缺省从安装目录身份（`~/.agentic-ops/user/identity.yaml`，D-048 阶段二）读取 `agent_id`；安装身份缺失时阻断并提示配置路径（`install identity set`）。授权引用（`--authorization-reference`）保持必填，人工门禁不弱化。

**3.5.2 无编号自动接管**：`ao-work task takeover` 不带 issue_key 时：
1. 调 list_tasks（T4）取名下未完成列表。
2. 按优先级排序：`profile.task_priority` 映射（priority 名 → 权重，配置缺省可用、渐进补充；未配置时按 Jira priority 默认序 + updated 倒序）。
3. 输出候选列表（key/summary/status/priority）并生成计划，**由研发工程师确认目标后**（授权引用 `user-confirmation:<KEY>:<plan_id>`）再执行接管，AI 不擅自选择任务。

**3.5.3 编排层（Skill）**：`/ao-takeover TAP-123` 由 AI 按既定顺序编排：workspace 识别 → jira list/inspect → task start → task takeover（授权确认）→ 输出下一步。该编排写入版本化 Skill（T7）。

### 3.6 T7：AI 入口技能重构

两个层面：

**3.6.1 仓库版本化技能（developer/skills/）**：新增 `daily-task-operations` Skill（workplane: developer），定义日常操作流程：
- 第一步：从当前工作目录识别工作空间（`.agentic-ops/AGENTS.md` 存在 = developer 工作空间）。
- 第二步：定位 ao-work 入口（工作空间绑定/安装身份/验证安装目录，不默认扫 `~/.agentic-ops`）。
- 日常操作：list / inspect / takeover / resume / auto-takeover 的编排顺序与确认点。
- 与 `run-task-to-pr-test` 的分工：本 Skill 负责「接管并开始工作」，完整链路 Skill 负责「已授权任务到 PR」。

**3.6.2 个人技能修正**（`~/.hermes/.../agentic-ops`，不属仓库）：内容瘦身为「工作面识别 + 规则指针」，developer 日常流程指向仓库版本化 Skill；删除「安装位置写死 ~/.agentic-ops」的表述，改为「从 cwd 识别 + 按工作空间绑定定位」。

## 4. 安全边界（不弱化项）

- **接管/写入人工门禁不弱化**：`--authorization-reference` 保持必填；自动接管必须先展示候选、由研发工程师确认目标，AI 不擅自选择任务。
- **不新增 Jira 状态、不改工作流**：transition 仍走 D-049 共享匹配器。
- **能力目录强耦合三条硬约束不破坏**（test_capability_catalog.py）：契约与目录条目一一对应；项目资产引用同步；旧命令串不出现。
- **list_tasks 只读**：side_effects 必须不写 Jira、不改代码。
- **resume_takeover 不写 Jira**：契约 side_effects 允许的只是本地事件/反馈物，外部状态一律回读核对。
- **技能归属**：新增 Skill 必须 `metadata.workplane: developer` 唯一声明；个人技能修正不改变工作面隔离。
- **CLI 参数变更同步文档**：`ai-employee-handbook.md` 第 5 节与 `initialize-project-workspace/SKILL.md` 命令示例必须同批修正（用户明确要求，2026-08-18）。

## 5. 失败码（新增/复用）

| 能力 | 失败码 | 来源 |
| --- | --- | --- |
| list_tasks | `task_query_failed` / `jira_search_failed` / `jira_adapter_config_failed` | list-tasks.yaml 已有 |
| resume_takeover | `run_not_found` / `local_state_mismatch` / `assignee_changed` / `agent_binding_lost` / `agent_ownership_conflict` / `resume_stage_not_allowed` / `terminal_run` 等 | resume-takeover.yaml 已有 |
| takeover 简化 | `agent_identity_missing`（安装身份缺失时阻断） | 新增 |
| takeover 自动选择 | `task_selection_cancelled`（用户未确认候选） | 新增 |

## 6. 组件变更

| 组件 | 变更 |
| --- | --- |
| `developer/runtime/src/ao_work/task_worktree.py` | T2：`_run_git` 补 `["git", *command]` |
| `developer/runtime/src/ao_work/work_cli.py` | T3：移除全局 -h 拦截，子命令帮助可达 |
| `developer/runtime/src/ao_work/jira/client.py` | T4：新增 `search_jql` |
| `developer/runtime/src/ao_work/jira/cli.py` | T4：新增 `jira list` |
| `developer/runtime/src/ao_work/task_takeover.py` | T6：agent-id 可选 + 无编号候选选择 |
| `developer/runtime/src/ao_work/` | T5：新增 resume 能力（或并入 task_state） |
| `developer/standards/capabilities/operations.yaml` | T4/T5：list_tasks / resume_takeover → implemented |
| `developer/standards/contracts/operations/` | 必要时微调（新失败码） |
| `developer/standards/projects/tapdata/profile.yaml` | 可选：`task_priority` 映射渐进补充 |
| `developer/skills/daily-task-operations/SKILL.md` | T7：新增版本化日常操作 Skill |
| `developer/standards/handbooks/ai-employee-handbook.md` | 第 5 节命令示例同步 |
| `docs/architecture/daily-task-operations-design.md` | 本文档 |
| `docs/decision-log.md` | D-050 登记 |
| 个人技能 `agentic-ops`（~/.hermes，非仓库） | T7：瘦身 + 定位规则修正 |

## 7. 测试与验证

| 任务 | 测试 |
| --- | --- |
| T2 | `test_task_worktree.py` 补真实 subprocess 回归（PATH 假 git / 临时仓库），断言命令带 git 前缀 |
| T3 | work_cli 测试：`task -h` / `jira -h` 返回子命令帮助 |
| T4 | `test_jira_list.py`：fake transport 断言 search/jql 端点、JQL 来源、输出字段、total 缺失兜底 |
| T5 | `test_task_resume.py`：本地状态 + Jira 回读校验链、失败码覆盖 |
| T6 | `test_task_takeover.py` 扩展：agent-id 缺省读取、无编号候选选择 + 用户确认、`agent_identity_missing` |
| 全量 | 四项固定验证：test-python-runtime.sh / test-resources.sh / test_install_boundary.sh / test-release-workflow.sh |

## 8. 文档与故事

- 本设计命中故事：DE-002/DE-003/DE-005/DE-006、PM-001~005 等（实现时逐个 `story impact` 确认，不预先断言）。
- 文档同步：ai-employee-handbook.md 第 5 节、developer/AGENTS.md（如能力清单变化）、decision-log D-050。
- 实施计划、进度、验收写 Jira；仓库不建第二份计划文件。

## 9. 决策点结论表

| # | 决策点 | 选项 | 结论 |
| --- | --- | --- | --- |
| D1 | 推进档次 | A 全量拆分 / B 只修阻塞 / C 零改动 | A（用户已确认 2026-08-19） |
| D2 | list 命令归属 | jira 组 / task 组 | jira 组（只读 Jira 事实，待确认） |
| D3 | resume 命令形态 | `task resume --issue-key` / `--agentic-run-id` | 双输入二选一，缺省本地最近记录（待确认） |
| D4 | agent-id 自动读取 | 安装身份为唯一来源 / 工作空间配置兜底 | 安装身份优先、工作空间兜底（待确认） |
| D5 | 自动接管选择 | AI 自主选 / 候选列表 + 用户确认 | 候选列表 + 用户确认（推荐，不弱化门禁） |
| D6 | 优先级排序来源 | profile.task_priority 映射 / Jira 默认序 | 配置化 + 缺省 Jira 默认序（D-048 渐进式偏好） |
| D7 | 日常编排载体 | 新增版本化 Skill / 并入 run-task-to-pr-test | 新增 `daily-task-operations` Skill（独立生命周期） |
| D8 | 个人技能 agentic-ops | 瘦身保留 / 删除 | 瘦身保留（工作面识别 + 规则指针），待确认 |

## 10. 实施顺序

```text
T1 设计文档 + D-050（本文档，提交需 Jira key）
→ T2 阻塞修复（独立小提交，先行验证主链路可用）
→ T3 -h 修复（独立小提交）
→ T4 list_tasks（T6 前置）
→ T5 resume_takeover（独立）
→ T6 接管简化 + 自动接管（依赖 T4）
→ T7 技能重构（可并行，含个人技能修正）
```

每项独立建 Jira 卡、独立 story gate 提交；按用户「大批量变更逐项确认」偏好逐项确认后再统一 approve。
