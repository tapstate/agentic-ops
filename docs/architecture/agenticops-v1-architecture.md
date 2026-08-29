# AgenticOps v1 工程架构

## 1. 架构决策

AgenticOps v1 直接采用 ao-gate-poc 的核心思想：Agent 保持执行主体，AgenticOps
只在关键副作用前实施统一策略控制，并用少量确定性工具保存流程状态。不同平台先
转换为版本化 AgenticOps 标准协议，Gate Core 不理解平台专有字段。

`v0.7` 是重构前基线。v1 不迁移旧目录结构、统一 CLI 和中间平台 Runtime；能力
只有在解决当前问题时，才按新架构重新实现。

## 2. 运行链路

```text
Agent 原生事件
    │
    ▼
Agent Adapter ──> Tool Adapter ──> Standard Request
    ▲                                      │
    │                                      ▼
    └────── Standard Decision <──── Gate Core <──── Policy
                                          ▲
                                   Workflow / Authorization
                                          ▲
                                      Project 配置

判定 allow 后，Agent 继续调用 Jira / Git / GitHub / CI 原生能力。
```

典型过程：Agent 读取 Jira 和 Git 事实，Workflow 在项目工作空间的统一注册表中建立
任务状态并绑定多个仓库；
人工确认方案后签发授权；Agent 原生执行代码、PR 和 CI 操作；每次副作用先经 Hook
判定；最终 Workflow 汇总验证和门禁事件，由 Agent 回填 Jira。

## 3. 稳定层与变化层

| 层 | 目录 | 责任 | 变化频率 |
|---|---|---|---|
| Contract | `contracts/` | 标准请求、判定和 Adapter Manifest | 低 |
| Gate | `gate/` | 标准操作、上下文提取、统一判定 | 低 |
| Policy | `policies/` | 公司级操作分级、授权和连续性原则 | 中 |
| Workflow | `workflow/` | 阶段、授权、CI、证据和恢复 | 中 |
| Project | `projects/<project>/` | Jira、仓库、准入、验证和 Runbook | 高且项目隔离 |
| Agent Adapter | `adapters/agents/` | Agent Hook 输入输出转换 | 随平台变化 |
| Tool Adapter | `adapters/tools/` | MCP、CLI 工具到标准操作的映射 | 随工具变化 |
| Bootstrap | `bootstrap/` | 安装、更新、回退、工作目录接线 | 低 |
| Internal | `internal/` | 本仓库故事审查和发布 | 不进入产品运行 |

源码仓库和安装目录都属于同一种 Product Root，使用相同目录结构和根入口
`agenticops`。二者只有版本稳定性不同，不是两套运行模式。项目工作空间不是产品
副本，只保存 `.agenticops.json`、归一化 `.gate/` 多任务状态和 Agent 平台要求的
可再生薄接线。

目录按“变化原因”划分，而不是按人员角色或旧命令划分。公共 Gate 不导入 Adapter
和项目配置；Adapter 不复制 Policy、Workflow 或 Project；Bootstrap 不实现业务
流程；Internal 不被产品安装。

## 4. 标准契约与适配边界

`contracts/` 是 Agent、Tool Adapter 与 Gate Core 的唯一协议事实源：

- `gate-request.schema.json`：标准副作用操作、来源、执行目录和目标上下文。
- `gate-decision.schema.json`：统一 `allow / ask / deny` 判定。
- `adapter-manifest.schema.json`：平台能力、降级方式和生成产物。
- `operation-catalog.json`：标准操作名称、类别、语义和请求边界。
- `workspace-binding.schema.json`：Product Root、项目、Agent、版本和可再生接线绑定。

Agent Adapter 只能编解码平台事件和判定；Tool Adapter 只能把 MCP、CLI 调用映射成
标准操作。Hook、MCP、Skill 和工作目录配置由 Manifest 与模板生成。平台不支持
`ask` 时通过 Manifest 声明 `deny_with_guidance`，不得在 Adapter 中增加状态机。

适配层重量由 `tests/test_adapter_boundary.py` 强制检查：每个 Agent 只有一个小型
无状态入口，限制代码行、函数和分支数量；禁止依赖 Workflow、Project、Policy，
禁止写状态；Gate 中禁止出现平台协议标识。

## 5. 项目工作空间与多任务模型

一个工作空间绑定一个 `projects/<project>/`，可以同时接管该 Jira 项目下多个任务。
任务状态统一组织为：

```text
.gate/
├── tasks.json                         # 项目级任务注册表
└── tasks/<issue-key>/
    ├── task.json                      # 阶段、事实和仓库集合
    ├── authorization.json             # 任务级授权
    ├── events.jsonl                   # 任务级门禁事件
    └── ci-<pr>.json                   # 任务级 CI 状态
```

`tasks.json` 只保存任务身份和 `active / inactive / completed` 注册状态，不复制任务
阶段和事实。多个任务可以同时 active；Workflow 在有歧义时要求显式 `--issue-key`。
Hook 根据 Jira 任务号，或 Git `repository + work_branch` 唯一解析任务；零匹配或多
匹配都不能复用其它任务授权。

每个任务可以组织多个独立 Git 工作树。每个仓库绑定：

- `repository`
- `work_branch`
- `base_branch`
- `approved_scope`
- `verification_method`

Hook 从当前仓库读取 origin 和分支，再从 active 任务中唯一定位授权。新增仓库或改变稳定绑定会
使旧授权失效；PR、CI 和验证结果属于执行结果，不改变授权指纹。每个仓库可以独立
形成提交、PR 和 CI，最后汇总到同一个 Jira 任务证据。

同一项目的两个 active 任务可以使用同一仓库的不同工作分支，但禁止注册相同的
`repository + work_branch`，否则门禁上下文无法唯一解析。中央源码池不是正确性
依赖；每个任务的状态、授权、事件和 CI 证据必须隔离。

## 6. 流程连续性

架构把“能力是否已迁移”和“流程是否安全可继续”分开判断：

```text
能力可用？ ── 是 ──> 自动执行当前步骤
    │
    否
    ▼
Agent 原生能力可安全完成？ ── 是 ──> 原生执行
    │
    否
    ▼
结构化人工接力当前步骤；不阻塞无依赖工作
```

只有事实不可信、权限不足、高风险人工门禁或外部写结果不明才是强制停止原因。不能
为了连续性修改策略、伪造状态或绕过 Hook。

## 7. 项目与 Agent 扩展

新增产品项目只新增 `projects/<project>/`，其中包含 Profile、准入规则、Skill 和
Runbook；除非发现公司级共性，否则不修改 Gate 或 Policy。新增 Agent 平台只新增
Manifest、薄转换器、模板和契约测试，不复制任务状态和项目规则。

这样 TapData、TapTest、TapState 可以独立调整 Jira 流程和表单，Claude、Codex 也
可以共用同一套规则与任务状态。

## 8. 安装与发布边界

`~/.agentic-ops` 是稳定 `main` 的中央 Product Root 稀疏克隆，只包含根入口和
`adapters/`、`bootstrap/`、
`contracts/`、`gate/`、`policies/`、`projects/` 和 `workflow/`。业务项目工作空间只保存
产品项目绑定、生成的 Agent 原生入口、平台接线和归一化 `.gate/`；不会复制 Project Skill、
Policy、Runtime，也不会加载根仓库维护规则或 `internal/`。

Codex 从项目工作空间 `AGENTS.md` 加载公共协作入口；Claude 从 `CLAUDE.md` 导入同一份
`AGENTS.md`。两者的 Hook 都直接调用 Product Root Adapter。`agenticops doctor` 只读
检查绑定与派生接线，`agenticops repair` 根据中央资产幂等重建接线，不修改 `.gate/`；
修复时会删除带 `product: agenticops` 标记的旧版复制 Project Skill，遇到同名的非产品
文件则失败关闭。

AgenticOps 仓库自身通过 `internal/story_gate/` 和 `internal/release/` 治理。这些
工具是源代码供应链保护，不构成另一套面向研发的运行架构。

## 9. 架构验收标准

- 产品运行只依赖 Git、Shell 和 Python 3.9+，Python 产品代码无第三方依赖。
- Claude、Codex 共用 Gate、Policy、Workflow 和 Project。
- Gate 只接受标准协议，平台差异只存在于 Adapter。
- Adapter 重量门禁和跨 Agent 语义一致性测试通过。
- 一个项目工作空间可同时接管多个任务，每个任务可安全绑定并推进多个仓库。
- 多个 active 任务的状态、授权、事件和 CI 证据隔离，门禁上下文歧义时失败关闭。
- 新项目适配无需修改公共 Gate。
- 产品安装不包含 `internal/`。
- 未迁移辅助能力不会默认阻塞整体流程。
- 固定测试分别覆盖产品 Runtime、资源边界、安装边界和发布治理。
