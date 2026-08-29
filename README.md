# AgenticOps

AgenticOps 是公司级 Agentic 研发基础设施，为 Claude、Codex 和后续 Agent 提供
统一流程规则、操作门禁、任务恢复和证据边界。

v1 以 ao-gate-poc 的 Hook + 声明式 Policy 思想为基线。旧版 AgenticOps 固定在
`v0.7`，现役代码不保留 `ao-work`、双工作面或旧 Runtime 兼容入口。

## 架构

```text
Agent 原生事件
    │
Agent Adapter → Tool Adapter → Standard Request
    ↑                              │
    └── Standard Decision ← Gate Core ← Policy
                                  ↑
                         Workflow / Project

allow 后由 Agent 调用 Jira / Git / GitHub / CI 原生能力。
```

- `contracts/`：AgenticOps 标准请求、判定和 Adapter Manifest。
- `gate/`：稳定门禁内核。
- `policies/`：公司级操作策略与流程连续性原则。
- `workflow/`：阶段、授权、CI、证据等小型确定性工具。
- `projects/`：TapData 及后续产品的独立适配。
- `adapters/agents/`：Agent Hook 薄转换；`adapters/tools/`：MCP、CLI 操作映射。
- `bootstrap/`：安装、更新、回退和工作目录初始化。
- `internal/`：仅供本仓库发布治理使用，不进入研发安装。
- `agenticops`：中央 Product Root 的薄入口，只负责安装生命周期、工作目录接线、
  诊断、修复和启动 Agent。

一个项目工作空间绑定一个 Product Project，可同时接管该项目下多个 Jira 任务；
每个任务可以推进多个 Git 仓库。未迁移的
辅助能力优先由 Agent 原生能力完成，不能成为整个主流程的默认阻塞点；事实、权限、
风险和外部写入不确定性仍严格失败关闭。

Hook、MCP、Skill 和工作目录入口由 Adapter Manifest 与模板生成。适配层必须无状态，
不能依赖 Workflow、Project 或 Policy，并由固定重量门禁限制文件数、代码预算和依赖。

## 验证

```bash
bash internal/tests/test_runtime.sh
bash internal/tests/test_resources.sh
bash tests/test_install.sh
bash internal/tests/test_release.sh
```

## 安装

```bash
./agenticops install
~/.agentic-ops/agenticops init --workspace <项目工作空间> --project tapdata --agent both
~/.agentic-ops/agenticops doctor --workspace <项目工作空间>
~/.agentic-ops/agenticops codex --workspace <项目工作空间>
```

源码维护时无需另装一份，直接用当前源码根作为中央 Product Root：

```bash
./agenticops init --workspace <临时项目工作空间> --project tapdata --agent both
./agenticops codex --workspace <临时项目工作空间>
```

项目工作空间只保存 `.agenticops.json`、`.gate/`、`AGENTS.md`、`CLAUDE.md` 和平台
Hook/MCP 薄接线；Project Skill、Policy 和 Runtime 始终从中央 Product Root 读取。

```bash
python3 ~/.agentic-ops/workflow/task.py init --issue-key TAP-123 --task-class defect_fix --dir <项目工作空间>
python3 ~/.agentic-ops/workflow/task.py init --issue-key TAP-456 --task-class technical_task --dir <项目工作空间>
python3 ~/.agentic-ops/workflow/task.py list --dir <项目工作空间>
```

多个任务可以同时为 `active`。存在多个 active 任务时，Workflow 命令必须显式传
`--issue-key`；Hook 按仓库、工作分支或 Jira 任务号唯一解析任务，歧义时停止。

当前首先适配 TapData。新产品在 `projects/<project>/` 独立增加 Profile、准入规则和
Runbook，不修改公共 Gate。

源码版本使用 `<分支>-<标签>-<提交数>-<提交编号>`：

```bash
python3 internal/version.py
```

日常开发在 `develop`，通过 `internal/release/release.sh` 发布至 `main`。
