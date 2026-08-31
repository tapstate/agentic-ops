# AgenticOps v1 工程架构

## 1. 核心决策

Agent 是执行主体；AgenticOps 只提供标准、规则门禁和少量确定性状态。不同 Agent 和工具先转换成版本化标准操作，Gate 不理解平台字段。旧版实现固定在 `v0.7`，能力只在解决当前问题时按新分层重建。

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
| Maintenance Skill | `skills/` | 仅维护面使用的流程验证与协作指引；不属于任何业务项目，也不安装或接线到业务工作空间 |
| Internal | `internal/` | AgenticOps 自身的审查和发布 |

规则按变化原因归属：平台差异只能进入 Adapter，项目差异只能进入 Project，公司共性进入 Policy，只有必须确定执行的状态逻辑进入 Workflow。

## 3. 通用 Agent 适配

公共入口不维护 Agent 枚举。`bootstrap/agent_registry.py` 从 `adapters/agents/*/manifest.json` 发现 Agent。每个 Manifest 声明：

- Agent ID、入口和协议能力；
- `ask` 不可用时的保守降级；
- 要生成的工作空间接线；
- 本地启动方式。

新增 Agent 只增加一个目录、Manifest、薄 Hook、模板和测试。Adapter 不得保存状态、依赖 Policy/Project/Workflow 或定义新操作语义。`tests/test_adapter_boundary.py` 对每个 Agent 约束文件数、代码量、依赖和状态写入。

## 4. 产品根目录（Product Root）的两个工作面

源码产品根目录和稳定安装产品根目录使用同一个 `agenticops` 入口，但生命周期操作必须区分工作面：

- 源码目录是维护工作面，首次由 `agenticops setup` 跟踪 `develop`，后续由 `update` 原地 fast-forward 并同步维护依赖和受信 Hook；
- 安装产品根目录是使用工作面，`update` 只跟随安装时记录的分支；
- 维护工作面允许本地领先并明确提示，但不自动推送；分支不符、工作区有修改或 Git 历史分叉时停止；使用工作面还要求 HEAD 与本地安装记录严格一致；
- 两者所有非 Git 本地状态都进入 `.local/`；
- `.local/product.json` 记录 `mode`、仓库、跟踪分支及生命周期同步提交；`.local/repository-pool.json` 记录默认 Source Pool 根目录和仓库供给模式；`.local/gate/events.jsonl` 只记录直接在 Product Root 执行且无法归属任务的门禁事件，避免把维护状态误写成项目工作空间状态；维护工作面的实际运行版本始终以 Git HEAD 为准；
- 安装产品根目录不包含 `internal/` 或维护面 `skills/`。

`.local/` 是本机可删除、不可提交的产品运行区，不是规则或业务事实源。除生命周期配置外，它可保存由本 Product Root 成功初始化过的工作空间提示索引；该索引只用于更新后提示接线待刷新，不发现、不扫描、更不自动修改业务目录。生命周期操作使用 `.local/lifecycle.lock/` 防止同一产品根目录并发更新或回退。更新源码后，当前源码内核立即生效；已启动 Agent 需要重启，生成接线由下一次 `start` 自动刷新，也可通过 `doctor` 和 `repair` 显式检查、修复。`rollback` 只属于使用工作面；维护工作面保留正常 Git 历史和发布治理，不由产品入口自动移动源码分支。

## 5. 薄项目工作空间

一个工作空间绑定一个 `projects/<project>/`，可同时激活该项目下多个 Jira 任务。

```text
.agenticops/
├── init.json                 # Product 版本和生成产物哈希
├── workspace.json            # 产品根目录、workspace ID、Project、Agent 与 Source Pool 绑定
├── events.jsonl              # 无法唯一归属到任务的 Gate 审计事件
└── tasks/
    ├── index.json            # active/inactive/completed 注册状态
    └── <issue-key>/
        ├── state.json        # 阶段、事实和多仓库集合
        ├── authorization.json
        ├── events.jsonl
        └── ci-<pr>.json
```

工作空间不复制 Policy、Project Skill 或 Runtime。根 `AGENTS.md`、Agent 配置和 MCP 配置是可再生接线，文件归属及哈希记录在 `init.json`；`doctor` 检测漂移，`repair` 安全重建。Gate 能唯一解析任务时将事件写入对应任务目录；无法唯一解析任务时才写入根 `events.jsonl`，它是受控工作空间状态，随 `purge` 删除。旧 `.agenticops.json` 和 `.gate/` 只作为一次性迁移输入，不再是事实源。工作空间维护命令先列出精确目标再确认：`repair` 和 `clean --generated-only` 只收敛可再生接线；`detach` 删除已校验归属的接线和绑定但保留任务状态；`purge` 才会删除任务状态，且必须逐个工作空间明确确认。无法访问的登记只报告，不能被更新自动注销。

多个 active 任务存在歧义时，Workflow 要求显式 issue key。Gate 按 issue key 或 `repository + work_branch` 唯一解析任务；零匹配、多匹配都不能借用其它任务授权。

## 6. Source Pool 与任务工作树

大型业务仓库由多个工作空间共享同一个 Source Pool，但不把实体仓库嵌入 Product Root。默认池根目录是 `${product_root}-repos`；Product Root 的 `.local/repository-pool.json` 保存默认池配置，`.local/repository-worktrees.json` 与 `repository-pool.lock` 保存跨工作空间 worktree 租约及互斥。工作空间可在初始化时覆盖池根目录，最终绑定固化在 `workspace.json`，产品默认值变化不会静默重绑已有工作空间。

```text
<repository-pool-root>/<owner>/<repo>/     # Git 主工作树，统一维护任务基线

<workspace>/.agenticops/worktrees/
└── <issue-key>/<run-id>/<owner>/<repo>/   # 任务实际修改目录
```

Project Package 的 `repositories.json` 是仓库、origin、基线分支和域标签的唯一目录。域只作为每个 `repositories[owner/repo].domains` 的数组标签维护，不再另建“域到仓库列表”；一个仓库可属于多个域，操作和验证可按标签动态筛选，避免双向目录漂移。用户可自行下载仓库，但必须满足 `owner/repo` 布局，且 origin、基线分支和 Git 根目录通过校验。准备任务时 Workflow 要求主工作树洁净、执行 `fetch --prune` 并 fast-forward 到远端基线，再固化 `base_sha`、仓库目录摘要和 worktree 路径；目录、分支或目录摘要漂移时失败关闭。

任务完成或显式清理会先检查 worktree 洁净度，再执行 `git worktree remove` 与 `prune`。本地任务分支默认保留；只有显式要求时才尝试 `git branch -d`，未合并分支不会被强删。同一 run 的恢复复用已有 worktree；reset 生成新 `run_id`。残留分支不会被静默复用，需要新分支或显式 `--reuse-existing-branch`。

`run_id` 是 Workflow 创建并持久化的任务执行身份，不是 Agent 会话身份。主 Agent、subagent 和恢复会话都必须读取同一个任务状态，不能各自生成 `run_id`。再次 init 已存在任务时保持状态不变并失败关闭，提示用户选择继续现有 run，或先清理 worktree 后显式 reset；只有后者创建新 run，并撤销旧授权。init/reset 按任务互斥；reset 必须携带 `--expected-run-id <当前值>`，并发或过期调用因 compare-and-swap 校验失败关闭。

## 7. 多仓库、授权与 Agent 执行目录

一个任务可登记多个仓库，每仓绑定 repository、work branch、base branch、修改范围和验证方式。准备 worktree 后，授权还绑定 `run_id` 与 `base_sha`。授权绑定任务、Agent、方案和完整仓库集合；新增仓库或修改稳定绑定后旧授权失效。每仓独立记录提交、PR、CI 和验证，最后汇总成任务证据。

Agent 仍由薄项目工作空间入口启动。`agenticops start --issue-key ...` 校验当前任务已准备的 worktree，以 `<workspace>/.agenticops/worktrees/<issue-key>/<run-id>` 作为任务模式 cwd，并逐个转换成 Agent Manifest 声明的动态目录参数；Codex 与 Claude 当前均使用 `--add-dir`。不得把 Source Pool 根目录、主工作树或其它任务执行目录加入可写范围。无法声明动态任务目录的平台失败关闭，并输出人工接力；不得降级为无沙箱启动。

文件修改、构建和测试发生在任务 worktree。linked worktree 的 Git 元数据仍位于主仓库 `.git/worktrees/`，因此 `git add/commit/push` 同时受 Agent 平台审批和 Gate 控制；Source Pool 的 clone、fetch、worktree add/remove 由确定性 Workflow 执行。

## 8. 连续性与安全

未迁移的辅助能力优先由 Agent 原生能力完成；没有安全自动路径时，只暂停当前副作用并输出人工接力。事实不可信、权限不足、高风险操作或外部写入结果不明必须停止。

Tool Adapter 采用正向命中：MCP 以服务标识和工具名的完整身份映射，Shell 只识别直接命令、已支持包装及同一调用内可证明指向受控命令的动态别名；只有明确映射到标准操作的工具调用才生成 Gate 请求。明确属于受控操作族但因包装、目标或参数歧义而无法可靠生成标准请求时失败关闭；任意解释器、未登记脚本和未映射工具不等于 AgenticOps 授权，Hook 不输出判定并交还 Agent 平台原生权限流程。Agent Adapter、Tool Adapter 或 Gate 自身异常仍失败关闭。Claude 与 Codex 共用同一 Tool Adapter 分类语义，只保留各自原生 Hook 判定协议的薄转换差异。

Hook 是流程控制点，不是安全沙箱。不得关闭 Agent 平台原生沙箱或把未命中透传配置成无条件外部写权限；凭证最小权限、服务端保护、CI 和人工审查仍是最终边界。合并、发布、Tag、保护分支写入、强推和历史改写不被普通任务授权覆盖。Agent Hook、共享 Adapter Runtime 和 Tool Adapter 分类策略属于发布信任根，修改后禁止自动发布，必须通过受保护 `main` 的独立人工审查 PR 完成升级。

## 9. 架构验收

- 公共入口可发现任意合规 Agent Manifest，不存在固定平台枚举。
- Gate 只接受标准协议，Adapter 重量门禁通过。
- MCP 只按完整工具身份映射；已映射标准操作进入统一 Gate，受控操作歧义失败关闭，未命中操作交还 Agent 原生权限；Claude 与 Codex 分类结果一致。
- 源码目录和安装产品根目录共用结构、入口和 `.local/` 约定。
- 工作空间明确区分初始化、配置和按任务隔离的数据。
- 多任务、多仓库上下文唯一，授权变化失败关闭。
- 多个工作空间共享 `owner/repo` 主工作树，任务只写工作空间内当前 run 的 worktree；启动权限不扩展到整个池。
- 主工作树、origin、基线、目录摘要、`base_sha`、清理和重做行为均由可执行测试约束。
- 新项目适配不修改公共 Gate；产品安装不包含 `internal/`。
- 四项固定测试覆盖 Runtime、资源、安装接线和发布治理。
