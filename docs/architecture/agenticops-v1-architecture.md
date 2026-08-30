# AgenticOps v1 工程架构

## 1. 核心决策

Agent 是执行主体；AgenticOps 只提供标准、规则门禁和少量确定性状态。不同 Agent 和
工具先转换成版本化标准操作，Gate 不理解平台字段。旧版实现固定在 `v0.7`，能力只在
解决当前问题时按新分层重建。

```text
Agent 原生事件
    │
Agent Adapter → Tool Adapter → Standard Request
    ↑                              │
    └── Standard Decision ← Gate Core ← Policy
                                  ↑
                         Workflow / Project
```

## 2. 分层

| 层 | 目录 | 责任 |
|---|---|---|
| Contract | `contracts/` | 标准请求、判定、操作词表和 Manifest |
| Gate | `gate/` | 上下文解析与统一判定 |
| Policy | `policies/` | 公司级操作和连续性规则 |
| Workflow | `workflow/` | 阶段、授权、CI、证据、恢复 |
| Project | `projects/<project>/` | Jira、分支、准入、验证和 Runbook |
| Adapter | `adapters/` | Agent/工具协议的无状态转换 |
| Bootstrap | `bootstrap/` | 源码目录、产品根目录（Product Root）与工作空间生命周期 |
| Internal | `internal/` | AgenticOps 自身的审查和发布 |

规则按变化原因归属：平台差异只能进入 Adapter，项目差异只能进入 Project，公司共性
进入 Policy，只有必须确定执行的状态逻辑进入 Workflow。

## 3. 通用 Agent 适配

公共入口不维护 Agent 枚举。`bootstrap/agent_registry.py` 从
`adapters/agents/*/manifest.json` 发现 Agent。每个 Manifest 声明：

- Agent ID、入口和协议能力；
- `ask` 不可用时的保守降级；
- 要生成的工作空间接线；
- 本地启动方式。

新增 Agent 只增加一个目录、Manifest、薄 Hook、模板和测试。Adapter 不得保存状态、
依赖 Policy/Project/Workflow 或定义新操作语义。`tests/test_adapter_boundary.py` 对每个
Agent 约束文件数、代码量、依赖和状态写入。

## 4. 产品根目录（Product Root）的两个工作面

源码产品根目录和稳定安装产品根目录使用同一个 `agenticops` 入口，但生命周期操作必须区分工作面：

- 源码目录是维护工作面，首次由 `agenticops setup` 跟踪 `develop`，后续由 `update`
  原地 fast-forward 并同步维护依赖和受信 Hook；
- 安装产品根目录是使用工作面，`update` 只跟随安装时记录的分支；
- 维护工作面允许本地领先并明确提示，但不自动推送；分支不符、工作区有修改或 Git
  历史分叉时停止；使用工作面还要求 HEAD 与本地安装记录严格一致；
- 两者所有非 Git 本地状态都进入 `.local/`；
- `.local/product.json` 记录 `mode`、仓库、跟踪分支及生命周期同步提交；维护工作面的
  实际运行版本始终以 Git HEAD 为准；
- 安装产品根目录不包含 `internal/`。

`.local/` 是本机可删除、不可提交的产品运行区，不是规则或业务事实源。除生命周期
配置外，它可保存由本 Product Root 成功初始化过的工作空间提示索引；该索引只用于更新后
提示接线待刷新，不发现、不扫描、更不自动修改业务目录。
生命周期操作使用 `.local/lifecycle.lock/` 防止同一产品根目录并发更新或回退。
更新源码后，当前源码内核立即生效；已启动 Agent 需要重启，生成接线由下一次 `start`
自动刷新，也可通过 `doctor` 和 `repair` 显式检查、修复。
`rollback` 只属于使用工作面；维护工作面保留正常 Git 历史和发布治理，不由产品入口
自动移动源码分支。

## 5. 薄项目工作空间

一个工作空间绑定一个 `projects/<project>/`，可同时激活该项目下多个 Jira 任务。

```text
.agenticops/
├── init.json                 # Product 版本和生成产物哈希
├── workspace.json            # 产品根目录、Project、Agent 集合
└── tasks/
    ├── index.json            # active/inactive/completed 注册状态
    └── <issue-key>/
        ├── state.json        # 阶段、事实和多仓库集合
        ├── authorization.json
        ├── events.jsonl
        └── ci-<pr>.json
```

工作空间不复制 Policy、Project Skill 或 Runtime。根 `AGENTS.md`、Agent 配置和 MCP
配置是可再生接线，文件归属及哈希记录在 `init.json`；`doctor` 检测漂移，`repair`
安全重建。旧 `.agenticops.json` 和 `.gate/` 只作为一次性迁移输入，不再是事实源。
工作空间维护命令先列出精确目标再确认：`repair` 和 `clean --generated-only` 只收敛可再生
接线；`detach` 删除已校验归属的接线和绑定但保留任务状态；`purge` 才会删除任务状态，且
必须逐个工作空间明确确认。无法访问的登记只报告，不能被更新自动注销。

多个 active 任务存在歧义时，Workflow 要求显式 issue key。Gate 按 issue key 或
`repository + work_branch` 唯一解析任务；零匹配、多匹配都不能借用其它任务授权。

## 6. 多仓库与授权

一个任务可登记多个仓库，每仓绑定 repository、work branch、base branch、修改范围
和验证方式。授权绑定任务、Agent、方案和完整仓库集合；新增仓库或修改稳定绑定后旧
授权失效。每仓独立记录提交、PR、CI 和验证，最后汇总成任务证据。

## 7. 连续性与安全

未迁移的辅助能力优先由 Agent 原生能力完成；没有安全自动路径时，只暂停当前副作用
并输出人工接力。事实不可信、权限不足、高风险操作或外部写入结果不明必须停止。

Hook 是流程控制点，不是安全沙箱。凭证最小权限、服务端保护、CI 和人工审查仍是最终
边界；合并、发布、Tag、保护分支写入、强推和历史改写不被普通任务授权覆盖。

## 8. 架构验收

- 公共入口可发现任意合规 Agent Manifest，不存在固定平台枚举。
- Gate 只接受标准协议，Adapter 重量门禁通过。
- 源码目录和安装产品根目录共用结构、入口和 `.local/` 约定。
- 工作空间明确区分初始化、配置和按任务隔离的数据。
- 多任务、多仓库上下文唯一，授权变化失败关闭。
- 新项目适配不修改公共 Gate；产品安装不包含 `internal/`。
- 四项固定测试覆盖 Runtime、资源、安装接线和发布治理。
