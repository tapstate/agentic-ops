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

## 必需插件的按需配置

当前项目的必需外部插件清单位于 `__AGENTIC_OPS_HOME__/adapters/tools/mcp-requirements.json`：
`atlassian` 提供 Jira 任务、准入和状态事实，是项目所需的 MCP 插件，但不是启动前置条件。
GitHub MCP、`gh` 和其它 GitHub 工具不由 AgenticOps 绑定；Agent 依据当前任务、可用工具和
用户授权自行选择。

- 首次需要 Jira 事实时检查 `atlassian` 是否可调用。
- `atlassian` 不可用、未安装、未启用或未登录时，停止 Jira 事实依赖的步骤，明确告诉研发工程师所需插件、用途和当前客户端的安装/登录入口；不得伪造工具结果、改用未受控 token/PAT，或自行修改全局 Agent 配置。
- 在用户完成安装和认证后，重新读取 Jira 事实并从当前停止点继续。与该插件无关的本地准备工作可以继续。

## 必须遵守的入口规则

- Jira 是任务事实源，Git 是代码事实源，GitHub PR/CI 是审查事实源；`.agenticops/`
  只保存工作空间配置及本地执行、恢复和门禁事件，不替代外部事实源。
- 收到接管、继续、恢复或 reset 任务请求时，必须先读取当前项目 `.agents/skills/` 或
  `.claude/skills/` 中匹配的 Project Skill，再读取历史 memory 或会话摘要。当前 Product
  Root、Project Profile 和 Project Skill 是现役规则源；memory 只能作为历史线索，不得
  用来推断现役命令。Skill 缺失、链接不可读或目标越界时停止任务副作用并提示从工作空间
  根执行 `./agenticops repair`；不得从历史信息推断或恢复已经退役的入口。
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
- Agent 从工作空间根使用 `./agenticops start <id>` 启动，并在同一会话完成任务。prepare 后必须执行 `python3 __AGENTIC_OPS_HOME__/workflow/task.py repository context --issue-key <JIRA-KEY> --json --dir <项目工作空间>`，只在返回的当前 issue/run worktree 中分析、修改、构建和测试。当前会话的 Git 副作用必须使用 `git -C <返回的 worktree> ...`；Gate 只接受当前任务已准备的精确路径。不得启动嵌套 Agent、切换工作空间或创建会话级“当前任务”状态。任务状态操作继续显式绑定 workspace、issue 和 run。
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
