# AgenticOps v1 工程架构

## 1. 核心决策

Agent 是执行主体；AgenticOps 只提供标准、规则门禁和少量确定性状态。确定性入口复用版本化标准和判定，Gate 不理解平台字段。旧版实现固定在 `v0.7`，能力只在解决当前问题时按新分层重建。

```text
Agent 原生工具 → Git / Jira / GitHub / 构建与测试
Agent → Workflow 状态变更入口 → 持锁校验 → 状态与证据
                   ↑
              Project / Policy / Gate 的标准判定
```

使用者工作空间只保证流程检查点有效，不自动拦截原生工具。Gate 保留标准协议与可复用判定，旧 Agent Hook/Tool Adapter 协议资产未自动接线；它们的存在不代表产品承诺外部调用被拦截。

## 2. 分层

| 层 | 目录 | 责任 |
|---|---|---|
| Contract | `contracts/` | 标准请求、判定、操作词表和 Manifest |
| Gate | `gate/` | 上下文解析与统一判定 |
| Policy | `policies/` | 公司级操作、连续性规则及非阻断方案调优策略 |
| Workflow | `workflow/` | 阶段、授权、CI、证据、恢复 |
| Project | `projects/<project>/` | Jira、分支、准入、验证和 Runbook |
| Adapter | `adapters/` | Agent/工具协议的无状态转换 |
| Bootstrap | `bootstrap/` | 源码目录、产品根目录（Product Root）与工作空间生命周期 |
| Maintenance Skill | `skills/` | 仅维护面使用的流程验证与协作指引；不属于任何业务项目，也不安装或接线到业务工作空间 |
| Internal | `internal/` | AgenticOps 自身的审查和发布 |

规则按变化原因归属：平台差异只能进入 Adapter，项目差异只能进入 Project，公司共性进入 Policy，只有必须确定执行的状态逻辑进入 Workflow。

缺陷修复策略属于通用方案调优，不是 Gate 或质量门禁。通用目录位于 `policies/defect-repair-strategies.json`，项目可在 `projects/<project>/planning.json` 只覆盖默认选择；Workflow 容错解析后由 `task.py checklist/next` 向 Agent 提供 Q2 方案指导。它仅适用于规范化 `defect_fix`，配置不可用、Agent 未应用或方案偏离均只产生警告，不改变阶段推进、质量问题或授权判定。健康配置默认使用“最小充分修复”：完整消除已确认根因并保持架构稳定性、兼容性和容错性，同时限制无关扩散；它不等于追求最少代码行。

## 3. 通用 Agent 适配

公共入口不维护 Agent 枚举。`bootstrap/agent_registry.py` 从 `adapters/agents/*/manifest.json` 发现 Agent。每个 Manifest 声明生成接线与 `retired_artifacts`。退役列表只用于核验并迁移以前托管的文件，不按平台名称在 Bootstrap 写特例。当前内置 Agent 不生成通用 Hook，旧协议能力字段供显式标准判定与协议测试使用。每个 Manifest 声明：

- Agent ID、入口和协议能力；
- `ask` 不可用时的保守降级；
- 要生成的工作空间接线；
- 本地启动方式。

新增 Agent 只增加一个目录、Manifest、必要的薄适配、模板和测试。Adapter 不得保存状态、依赖 Policy/Project/Workflow 或定义新操作语义。`tests/test_adapter_boundary.py` 对每个 Agent 约束文件数、代码量、依赖和状态写入。

## 4. 产品根目录（Product Root）的两个工作面

源码产品根目录和稳定安装产品根目录使用同一个 `agenticops` 入口，但生命周期操作必须区分工作面：

- 源码目录是维护工作面，首次由 `agenticops setup` 跟踪 `develop`，后续由 `update` 原地 fast-forward 并同步维护依赖和受信 Hook；
- 安装产品根目录是使用工作面，`update` 只跟随安装时记录的分支；
- 维护工作面允许本地领先并明确提示，但不自动推送；分支不符、工作区有修改或 Git 历史分叉时停止；使用工作面还要求 HEAD 与本地安装记录严格一致；
- 两者所有非 Git 本地状态都进入 `.local/`；
- `.local/product.json` 记录 `mode`、仓库、跟踪分支及生命周期同步提交；`.local/repository-pool.json` 记录默认 Source Pool 根目录和仓库供给模式；`.local/gate/events.jsonl` 只记录直接在 Product Root 执行且无法归属任务的门禁事件，避免把维护状态误写成项目工作空间状态；维护工作面的实际运行版本始终以 Git HEAD 为准；
- 安装产品根目录不包含 `internal/` 或维护面 `skills/`。

`.local/` 是本机可删除、不可提交的产品运行区，不是规则或业务事实源。除生命周期配置外，它可保存由本 Product Root 成功初始化过的工作空间提示索引；该索引只用于更新后提示接线待刷新，不发现、不扫描、更不自动修改业务目录。生命周期操作使用 `.local/lifecycle.lock/` 防止同一产品根目录并发更新或回退。更新源码后，当前源码内核立即生效；已启动 Agent 需要重启，普通生成接线由下一次 `start` 刷新，也可通过 `doctor` 和 `repair` 检查、修复。检测到托管 Hook 退役时，start 和普通 repair 保留现场并报告迁移范围；用户明确选择 `repair --accept-checkpoint-migration` 后，先校验全部旧 Hook 的归属哈希，再移除并刷新。此选择不是持久门禁开关，不会改写 task/run 或授权记录。`rollback` 只属于使用工作面；维护工作面保留正常 Git 历史和发布治理，不由产品入口自动移动源码分支。

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
        ├── ci-<pr>.json
        └── <run-id>/             # Agent 原生工具调用的输入、草稿、回执与回读
```

工作空间不复制 Policy、Project Skill 或 Runtime。根 `agenticops`、`AGENTS.md`、Agent 配置和 MCP 配置是可再生接线，文件归属、哈希和 `workspace_state_epoch` 记录在 `init.json`；`doctor` 检测漂移，`repair` 安全重建。项目工作空间根的 `./agenticops` 只解析 workspace 绑定并转发到中央 Product Root，不注入任务上下文，也不承载任务状态机。Workflow 事件随任务保存；Agent 为原生工具交互生成的输入、草稿、回执、回读和日志直接放入任务下的 `<run-id>/`，不得散落在状态根目录；reset 保留旧 run 材料供追溯，任务级 purge 随任务目录统一回收。历史根级交互文件不在线迁移或猜测归属。历史 Gate 事件保留用于追溯，根 `events.jsonl` 仍属于可清理的受控状态。旧 `.agenticops.json` 和 `.gate/` 只作为一次性迁移输入，不再是事实源。工作空间维护命令先列出精确目标再确认：`repair` 和 `clean --generated-only` 只收敛可再生接线；`detach` 删除已校验归属的接线和绑定但保留任务状态；`purge` 才会删除任务状态，且必须逐个工作空间明确确认。无法访问的登记只报告，不能被更新自动注销。

工作空间持久化兼容性由 `contracts/workspace-state-compatibility.json` 统一声明。兼容变更保持 `workspace_state_epoch` 不变；不兼容变更提升 epoch，且必须先以兼容版本发布对应的升级协议。`update` 和 `rollback` 在切换 Git 引用前读取目标清单并比较每个已登记工作空间；目标不支持当前 epoch 时，只有任务、linked worktree 和未识别旧状态均已清理的工作空间可以采用新代际，否则失败关闭并引导用户留在原版本处理。直接跨多个产品版本只比较单调递增的 epoch，不依赖逐个回放中间版本。

普通任务状态变更以工作空间 `.agenticops` 目录自身为互斥对象；Q1 等质量检查点、授权、CI 和任务事件均不要求写入 Product Root。`purge` 在删除该目录前持有同一把锁并回读绑定，以避免并发任务在已删除的工作空间状态上继续写入。Product Root `.local/` 继续用于生命周期、本机工作空间索引及跨工作空间共享的 Source Pool/worktree 租约，不保存任务事实。

多个 active 任务存在歧义时，Workflow 要求显式 issue key。所有任务状态变更请求绑定当前 run，advance 另绑定预期阶段；在同一锁内读取最新状态并校验，拒绝旧 run、重复推进和借用其它任务确认。CI 与质量记录继续使用各自的 run/revision 校验；跨文件或 Git 副作用依靠可恢复记录处理，不宣称跨系统原子事务。

## 6. Source Pool 与任务工作树

大型业务仓库由多个工作空间共享同一个 Source Pool，但不把实体仓库嵌入 Product Root。默认池根目录是 `${product_root}-repos`；Product Root 的 `.local/repository-pool.json` 保存默认池配置，`.local/repository-worktrees.json` 与 `repository-pool.lock` 保存跨工作空间 worktree 租约及互斥。工作空间可在初始化时覆盖池根目录，最终绑定固化在 `workspace.json`，产品默认值变化不会静默重绑已有工作空间。

```text
<repository-pool-root>/<owner>/<repo>/     # Git 主工作树，统一维护任务基线

<workspace>/.agenticops/worktrees/
└── <issue-key>/<run-id>/<owner>/<repo>/   # 任务实际修改目录
```

Project Package 的 `repositories.json` 是仓库、origin、基线分支和域标签的唯一目录。域只作为每个 `repositories[owner/repo].domains` 的数组标签维护，不再另建“域到仓库列表”；一个仓库可属于多个域，操作和验证可按标签动态筛选，避免双向目录漂移。用户可自行下载仓库，但必须满足 `owner/repo` 布局，且 origin、基线分支和 Git 根目录通过校验。准备任务时 Workflow 要求主工作树洁净、执行 `fetch --prune` 并 fast-forward 到远端基线，再固化 `base_sha`、仓库目录摘要和 worktree 路径；目录、分支或目录摘要漂移时失败关闭。

任务完成或显式清理会先检查 worktree 洁净度，再执行 `git worktree remove` 与 `prune`。本地任务分支默认保留；只有显式要求时才尝试 `git branch -d`，未合并分支不会被强删。同一 run 的恢复复用已有 worktree；reset 生成新 `run_id`。残留分支不会被静默复用，需要新分支或显式 `--reuse-existing-branch`。

已有开发成果按两个选择恢复：继续已有分支/PR，或从当前目标分支创建新分支处理同一 Jira 任务。同一 run 保留冻结基线，目标分支前进不要求任务重置。新 run 接管旧分支时，可通过 `--continuation-base owner/repo=<完整 SHA>` 显式绑定已核验的历史比较基线，要求它同时是工作分支 Head 与当前目标分支的祖先；共同祖先只证明比较关系，不证明原始创建点。参数不允许改变已冻结基线，不自动改写 Git 历史或继承旧验收。prepare 在创建前后检查祖先关系，创建后失败负责清理本次 worktree。

只有远端工作分支时，续办额外绑定 `--continuation-head owner/repo=<完整 SHA>`；Workflow 获取已登记 origin 上的精确分支并比较 Head，匹配后才创建本地分支/worktree，已有本地分支不被覆盖。版本规划查询的目标分支 SHA 与开发基线分别校验：前者证明当前调查对象，后者证明当前工作树的开发历史；目标分支正常前进不使冻结基线失效。

新分支路径在清理旧执行现场并 reset 到 `task_intake` 后，通过 `repository update` 更新未准备的仓库登记；该入口检查 run、租约与分支占用，保留仓库身份和目标分支，撤销旧授权，把旧 PR/CI 引用归档到 history 后清空当前引用。旧分支、提交、PR 和历史验证材料保留，通常不需要 purge。命令与恢复步骤由[任务授权指引](../usage/task-authorization.md)维护。此扩展不改变已有状态字段和祖先校验语义，仅增加显式输入及 history 事件，保持 `workspace_state_epoch=1`；旧状态须通过回归验证，回退后已准备任务仍使用原有 base_sha 校验。

`run_id` 是 Workflow 创建并持久化的任务执行身份，不是 Agent 会话身份。主 Agent、subagent 和恢复会话都必须读取同一个任务状态，不能各自生成 `run_id`。再次 init 已存在任务时保持状态不变并失败关闭，提示用户选择继续现有 run，或先清理 worktree 后显式 reset；只有后者创建新 run，并撤销旧授权。init/reset 按任务互斥；reset 必须携带 `--expected-run-id <当前值>`，并发或过期调用因 compare-and-swap 校验失败关闭。

## 7. 多仓库、授权与当前工作空间会话

本地执行采用当前 run 的用户确认事实，初始 Jira 快照保持原样。影响版本与实施分支独立记录；同步账本不进入质量 advance 的阻断条件。Workflow 的 external_sync 只读汇总评论、水印、版本差异及状态待办，供 next、PR Ready 和 evidence 输出警告；实际发送由 Agent 原生工具执行。同一未知操作先回读，不同检查点可独立同步，旧 run 的未决操作保留供恢复。

一个任务可登记多个仓库，每仓绑定 repository、work branch、base branch、修改范围和验证方式。准备 worktree 后，授权还绑定 `run_id` 与 `base_sha`。方案确认记录绑定任务、Agent、方案和完整仓库集合，在实现和后续验收阶段重新检查 run、期限与仓库绑定；不代表每次外部工具调用均受 AgenticOps 核验。新增仓库或修改稳定绑定后旧授权失效。每仓独立记录提交、PR、CI 和验证，最后汇总成任务证据。

项目可配置少量非阻断的 Jira 状态同步节点。Workflow 幂等准备当前 task/run/node 的同步信息，实际 Jira 调用由 Agent 原生工具完成，随后导入回读结果。取消 Hook 单次放行/消费的产品保证；不明结果先回读，可再次 complete 收敛，不能盲目重发。失败形成可回查人工接力，不自动推进本地阶段。PR Ready 由独立只读核对汇总关联测试任务、当前提交的 PR Checks 和到 Q4 为止的任务检查项；Jira 状态同步待办作为提示返回，不伪装成这三类验收事实。

Agent 由薄项目工作空间入口使用 `./agenticops start <agent>` 启动，当前会话始终以项目工作空间为 cwd。任务 worktree 位于该工作空间内，因此不再为每个 task/run 重启 Agent 或追加动态目录参数。`workflow/task.py repository context --issue-key <issue-key> --json` 复用 worktree 校验，返回当前 run 的仓库、路径、分支和 `base_sha`；Agent 从同一会话在这些已校验路径中分析、修改、构建和测试。任务命令继续显式绑定 workspace 和 issue key，不建立会话级“当前任务”状态。Source Pool 位于工作空间外，仍不得加入 Agent 可写范围。

文件修改、构建和测试发生在任务上下文返回的 worktree。工作空间是 Agent 原生文件系统边界；多个任务之间的边界由显式 issue key、run、分支、检查点确认和最终 Git 范围验证共同保证，而不是由第二个 Agent 会话伪造。linked worktree 的 `git add/commit/push` 与其它原生操作均交给 Agent 平台权限和外部服务端控制；Workflow 只在检查点核对结果。Source Pool 的 clone、fetch、worktree add/remove 由确定性 Workflow 执行。

## 8. 连续性与安全

未迁移的辅助能力优先由 Agent 原生能力完成；没有安全自动路径时，只暂停当前副作用并输出人工接力。事实不可信、权限不足、高风险操作或外部写入结果不明必须停止。

使用者运行不接线通用工具 Hook。确定性状态检查在 Workflow 的实际变更入口执行；外部操作不因有副作用而进入 Gate。显式调用 Gate 标准 API 的协议校验仍失败关闭，但不能据此宣称已限制所有原生调用。直接编辑状态文件不受支持，也不具备防篡改保证。

Hook 是流程控制点，不是安全沙箱。不得关闭 Agent 平台原生沙箱或把未命中透传配置成无条件外部写权限；凭证最小权限、服务端保护、CI 和人工审查仍是最终边界。合并、发布、Tag、保护分支写入、强推和历史改写不被普通任务授权覆盖。Agent Hook、共享 Adapter Runtime 和 Tool Adapter 分类策略属于发布信任根，修改后禁止自动发布，必须通过受保护 `main` 的独立人工审查 PR 完成升级。

## 9. 架构验收

- 公共入口可发现任意合规 Agent Manifest，不存在固定平台枚举。
- Gate 只接受标准协议，Adapter 重量门禁通过。
- 新工作空间不生成通用工具 Hook；旧托管 Hook 只经显式迁移移除，任一目标漂移时保留现场。
- 源码目录和安装产品根目录共用结构、入口和 `.local/` 约定。
- 工作空间明确区分初始化、配置和按任务隔离的数据。
- 多任务、多仓库上下文唯一，方案确认绑定变化阻止检查点推进；重复请求不连跳阶段。
- 多个工作空间共享 `owner/repo` 主工作树，任务只写工作空间内当前 run 的 worktree；启动权限不扩展到整个池。
- 主工作树、origin、基线、目录摘要、`base_sha`、清理和重做行为均由可执行测试约束。
- 新项目适配不修改公共 Gate；产品安装不包含 `internal/`。
- 四项固定测试覆盖 Runtime、资源、安装接线和发布治理。
