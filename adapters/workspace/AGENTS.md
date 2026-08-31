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
- 副作用操作由 Agent Adapter 转换为标准请求，再由 `gate/runner.py` 判定。收到
  `ask` 或 `deny`
  必须展示原因并停止该操作，不得改命令、改状态文件或换工具绕过。
- 进入实现前，先为每个目标仓库登记仓库、工作分支、基线分支、范围和验证方式，
  执行 `workflow/task.py repository prepare` 创建当前 run 的 linked worktree并固化
  `base_sha`，再由研发工程师签发一次任务授权。新增仓库、重新准备或修改范围会使原授权失效。
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
- 未迁移的辅助能力不阻塞整个流程：优先使用 Agent 原生能力；没有安全自动路径时，
  只暂停对应副作用步骤并给出结构化人工接力。事实不可信、权限不足、高风险人工
  门禁和外部写结果不明确仍必须停止。

- Agent 原生入口只负责加载中央规则；不得把产品根目录的 Policy、Project 或 Skill
  复制成工作目录事实源。
- Hook 负责强制副作用门禁。即使自然语言入口未被正确理解，也不得绕过 Hook。
- Hook 必须按 Jira 任务号或 `repository + work_branch` 唯一解析 active 任务；没有
  匹配或匹配多个任务时不得借用其它任务授权。
