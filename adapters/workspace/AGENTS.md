<!-- 由 AgenticOps 生成；不要在项目工作空间直接维护。 -->
# AgenticOps 项目工作空间入口

本项目工作空间由中央 AgenticOps 产品根目录（Product Root）管理：

- 产品根目录：`__AGENTIC_OPS_HOME__/`
- Product Project：`__AGENTIC_OPS_PROJECT__`
- Repository Catalog：`__AGENTIC_OPS_HOME__/projects/__AGENTIC_OPS_PROJECT__/repositories.json`
- Initialization：`.agenticops/init.json`
- Workspace Configuration：`.agenticops/workspace.json`
- Task Registry：`.agenticops/tasks/index.json`
- Task State：`.agenticops/tasks/<issue-key>/`

一个工作空间只绑定一个产品项目，可以同时接管该项目下多个 Jira 任务；每个任务可
组织多个 Git 仓库。开始或恢复任务前，
必须读取中央产品根目录中当前项目的 Profile、准入规则和适用 Skill：

- `__AGENTIC_OPS_HOME__/projects/__AGENTIC_OPS_PROJECT__/profile.json`
- `__AGENTIC_OPS_HOME__/projects/__AGENTIC_OPS_PROJECT__/admission.json`
- `__AGENTIC_OPS_HOME__/projects/__AGENTIC_OPS_PROJECT__/skills/`

已接入 Agent 的原生 Skill 目录会以符号链接接线到同一份中央项目 Skill：Codex 使用
`.agents/skills/`，Claude Code 使用 `.claude/skills/`。不要修改这些工作空间链接或复制
Skill；更新、检查和修复均由 Product Root 统一处理。

多个任务同时 active 时，所有状态命令必须显式绑定任务号和项目工作空间：

```bash
python3 __AGENTIC_OPS_HOME__/workflow/task.py status --issue-key <JIRA-KEY> --dir <项目工作空间>
```

`AGENTS.md`、各 Agent 入口、Hook 和 MCP 配置都是可重新生成的薄接线，不是规则事实
源；项目规则和运行资产只在产品根目录维护。

## 必须遵守的入口规则

- Jira 是任务事实源，Git 是代码事实源，GitHub PR/CI 是审查事实源；`.agenticops/`
  只保存工作空间配置及本地执行、恢复和门禁事件，不替代外部事实源。
- `.agenticops/tasks/index.json` 只统一注册任务及其 active/inactive/completed
  状态；每个任务的事实、授权、事件和 CI 证据只能写入自己的
  `.agenticops/tasks/<issue-key>/`。
- 副作用操作由 Agent Adapter 转换为标准请求，再由 `gate/runner.py` 判定。首次收到
  `ask` 或 `deny` 时必须立即向研发工程师完整展示原因、处理动作和当前停止点，并停止
  当前操作及所有依赖它的后续步骤；不得把阻断当作正常结果继续，不得改命令、改状态
  文件或换工具绕过。
- 接管、继续或 reset 成功只是流程恢复点，不是默认停点。选择现有 run 或 reset 是人工
  决策；选择完成后应继续核验 Jira、补齐准入、登记仓库并准备本地基线，直到遇到方案
  确认、风险授权、事实不可信或其它真实人工决策点。
- `task_intake` 中先为每个目标仓库登记仓库、工作分支、基线分支、范围和验证方式，再
  执行受控 `workflow/task.py repository prepare`。该操作按已登记的 active 任务自动
  准备 Source Pool（`auto-clone` 模式会自动下载）和当前 run 的 linked worktree，固化
  `base_sha`，不要求预先签发 `task_execution` 授权；直接 Git clone、复用已有分支和
  非受控 worktree 操作仍由 Gate 单独判定。
- 只有 `repository prepare` 产出的本地任务 worktree、`base_sha` 和目录摘要才是任务的
  Git 基线。GitHub API、网页或其它远程只读源码只能标记为“远程候选参考”，不能写成
  “已核实基线”，也不能据此推进 `design_review`。本地基线完成后才能分析代码、形成
  方案；研发工程师确认方案后再签发一次任务授权。新增仓库、重新准备或修改范围会使
  原授权失效。
- 同一任务可以修改多个仓库；多个 active 任务使用同一仓库时必须使用不同工作分支。
  每个仓库分别保存提交、PR、CI 与验证事实，任务级
  证据汇总这些结果。
- `run_id` 只由 Workflow 创建；主 Agent、subagent 和恢复会话读取并共享同一任务状态。
  再次发现已接管任务时必须停止并让用户选择继续，或清理 worktree 后显式 reset；不得按
  Agent 会话自行生成新 run。reset 必须绑定当前 `--expected-run-id`，过期或并发请求停止。
- Jira 人可见内容使用中文；不得提交 token、密钥、客户数据或原始敏感日志。
- 合并、发布、Tag、强推、历史改写、保护分支写入始终不在任务授权范围内。
- 业务代码修改、构建和测试只能在当前任务 worktree 中进行；Product Root、Source Pool
  根目录、仓库主工作树和其它任务 worktree 不能作为任务写入目标。
- 执行实现任务时使用 `agenticops start --agent <id> --issue-key <JIRA-KEY>`；入口只把
  当前 issue/run 的执行目录作为 cwd，并只把当前任务已准备的 worktree 加入 Agent 动态
  目录。不得用关闭沙箱替代目录接线。
- 任务或工作空间清理必须先清理 linked worktree；脏 worktree 必须停止并保留现场。
- 临时结束处理用 `deactivate`，恢复同一 run 用 `activate`；清理重做使用 cleanup 后
  精确绑定当前 `run_id` 的 `reset`。只有任务已 inactive、run 精确匹配且研发工程师明确
  确认时，才可执行任务级 `purge` 删除该任务的本地状态；它不修改 Jira，脏 worktree
  必须停止，未合并分支必须保留并报告。
- 未迁移的辅助能力不阻塞整个流程：优先使用 Agent 原生能力；没有安全自动路径时，
  只暂停对应副作用步骤并给出结构化人工接力。事实不可信、权限不足、高风险人工
  门禁和外部写结果不明确仍必须停止。

- Agent 原生入口只负责加载中央规则；不得把产品根目录的 Policy、Project 或 Skill
  复制成工作目录事实源。
- Hook 负责强制副作用门禁。即使自然语言入口未被正确理解，也不得绕过 Hook。
- Hook 必须按 Jira 任务号或 `repository + work_branch` 唯一解析 active 任务；没有
  匹配或匹配多个任务时不得借用其它任务授权。
