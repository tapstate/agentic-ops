# AgenticOps 使用指引

本指引面向使用 AgenticOps v1 执行研发任务的工程师。AgenticOps 是流程规则、操作
门禁、任务恢复与证据边界；Jira、Git、GitHub、CI 与 Agent 本身仍各自是事实源或
执行主体。

## 1. 使用前准备

- 准备 Git 与 Python 3.9+。
- 准备 Bash，并具有 `git@github.com:tapstate/agentic-ops.git` 的读取权限；默认安装器
  使用该 SSH 地址拉取稳定版本。
- 为 GitHub、Jira 配置与职责相符的最小权限账号或令牌；不要把令牌写入工作空间、
  文档或提交。权限配置见[权限与安全边界](security/permissions.md)。
- 准备一个业务项目的独立工作空间。它不能是 AgenticOps Product Root 或其子目录。
- 确认要使用的项目适配已安装。当前示例使用 `tapdata`。

## 2. 安装并初始化工作空间

首次安装先取得一个临时源码 Product Root，再由它安装稳定版本。默认安装目录为
`~/.agentic-ops`：

```sh
git clone git@github.com:tapstate/agentic-ops.git agentic-ops-bootstrap
cd agentic-ops-bootstrap
test -x ./agenticops
./agenticops install
```

`test -x` 失败说明所选分支尚未发布包含 v1 Product Root 的安装资产；不要从其它
工作空间拼接文件或改用旧版入口，应联系仓库维护者发布含 `agenticops`、`bootstrap/`
及产品目录的版本后再继续。

安装目录不是默认值时，传入 `--install-home <安装目录>`；后续命令中的
`<Product Root>` 都替换为这个目录。若改用 HTTPS 或内部镜像，可将其 Git URL 传给
`--repository <Git URL>`。

初始化业务工作空间，并选择要接线的 Agent：

```sh
<Product Root>/agenticops init \
  --workspace <项目工作空间> \
  --project tapdata \
  --agent both
```

`--agent` 可选 `claude`、`codex` 或 `both`。初始化会建立工作空间绑定、`.gate/`
任务状态目录，以及可再生的 `AGENTS.md`、`CLAUDE.md`、`.mcp.json` 和所选 Agent
专属接线；不会把 Policy、项目 Skill 或产品 Runtime 复制到业务仓库。

维护 Product Root 源码时，不必另行安装，可直接运行：

```sh
./agenticops init --workspace <项目工作空间> --project tapdata --agent both
```

## 3. 检查与修复接线

在启动 Agent 前或 Product Root 更新后，先检查工作空间：

```sh
<Product Root>/agenticops doctor --workspace <项目工作空间>
```

若检查指出绑定或生成文件漂移，执行幂等修复后再次检查：

```sh
<Product Root>/agenticops repair --workspace <项目工作空间>
<Product Root>/agenticops doctor --workspace <项目工作空间>
```

`repair` 重建可再生接线，不修改 `.gate/` 中的任务、授权或门禁事件。它还会清理旧版
复制到工作空间的 Project Skill，但只删除带 `product: agenticops` 标记的文件；遇到
同名的非 AgenticOps 文件会拒绝覆盖或删除。

## 4. 更新与回退安装版本

以下命令只适用于稳定安装目录；源码 Product Root 会拒绝执行，要求使用 Git 与发布
流程。更新会拉取 `origin/main`、仅允许 fast-forward，并保存一个回退提交：

```sh
<Product Root>/agenticops update
```

回退只可回到最近一次更新前保存的版本，并以 detached HEAD 运行：

```sh
<Product Root>/agenticops rollback
```

更新或回退后，逐个执行 `doctor`；若提示产品版本或接线漂移，再执行 `repair`。这两个
命令不会替代发布流程，也不适用于合并、发布、Tag 或保护分支写入。

## 5. 启动 Agent

通过统一入口启动，会先刷新工作空间接线、切换到该目录，再将 `--` 后参数交给
Agent：

```sh
<Product Root>/agenticops codex \
  --workspace <项目工作空间> -- <Codex 参数>

<Product Root>/agenticops claude \
  --workspace <项目工作空间> -- <Claude 参数>
```

若选择 Codex，按当前 Codex 版本支持的 Hook 配置加载生成的
`.codex/agenticops-hooks.example.json`。Codex 对需要人工确认的 `ask` 会保守降级为
带指引的拒绝；它不会自动绕过确认。启动命令会执行与 `repair` 相同的接线刷新与
文件归属检查。详情见 [Codex 端到端验证](testing/e2e-codex.md)。

## 6. 开始或恢复 Jira 任务

在项目工作空间中，先查看已注册任务：

```sh
python3 <Product Root>/workflow/task.py list --dir <项目工作空间>
```

新任务先由 Agent 读取 Jira 与 Git 事实，再建立本地任务状态：

```sh
python3 <Product Root>/workflow/task.py init \
  --issue-key TAP-123 \
  --task-class defect_fix \
  --dir <项目工作空间>
```

可用任务类型为 `defect_fix`、`feature_change`、`technical_task`。已有任务用以下命令
恢复并查看当前阶段：

```sh
python3 <Product Root>/workflow/task.py status \
  --issue-key TAP-123 --dir <项目工作空间>
```

一个工作空间可以同时有多个 active 任务。涉及具体任务的 Workflow 命令必须带
`--issue-key`，避免把授权或证据误用于另一个任务；只读取项目级信息的 `list` 与
`branch` 不接受该参数。

`task.py list` 只列出当前工作空间 `.gate/` 中已经注册的本地任务，不是 Jira 的
“分配给我”列表。要查看当前 Jira 用户在 TapData 项目下的未完成任务，在已加载本工作
空间 `.mcp.json` 的 Agent 会话中使用 Atlassian MCP，并采用当前 Product Profile 的
查询：

```text
project = TAP AND assignee = currentUser() AND statusCategory != Done ORDER BY updated DESC
```

这是一项只读查询。查询结果是接管候选，不会自动写入 `.gate/`；选定具体任务、读取其
Jira 与 Git 事实后，再执行 `task.py init` 建立本地受控状态。

## 7. 日常受控流程

典型链路如下：

1. Agent 读取 Jira、Git 与项目准入事实；缺失事实时补齐并停止当前受影响步骤。
2. 通过 `task.py checklist` 查看机读准入要求；按任务登记仓库、工作分支、范围和验证方式。
3. 人工确认方案后，才签发与任务、仓库、分支和范围绑定的执行授权。
4. Agent 原生完成代码、Git、PR 与 CI 操作；关键副作用先由 Hook 进行 `allow`、`ask` 或 `deny` 判定。
5. 分仓记录验证、PR、CI，汇总任务级证据；人工确认后再回填 Jira。

合并、发布、Tag、强推和保护分支写入始终需要单独的人为决策，普通任务授权不覆盖。
如果某项辅助能力尚未迁移，应优先采用安全的 Agent 原生能力；没有安全路径时仅暂停
该副作用，并保留结构化人工接力，不伪造成功或绕过门禁。

`task.py` 只允许相邻阶段推进。新任务的最小闭环如下：

1. 从 `waiting_takeover` 推进到 `task_intake`，并记录已核对的 Jira 接管事实。
2. 用 `checklist` 读取当前项目的必填项，逐项 `record`；必填项齐全后才可推进到
   `design_review`。
3. 登记至少一个目标仓库，在 `design_review` 由人工确认方案并签发授权；随后才可
   推进到 `implementation`。
4. 实现后记录实际验证结论，才可离开实现阶段；每个仓库分别记录 PR 与 CI 结果，
   再生成证据。

以下命令展示前 3 步。任务同时 active 时都显式传入 `--issue-key`：

```sh
python3 <Product Root>/workflow/task.py advance \
  --issue-key TAP-123 --note '已核对 Jira 接管事实' --dir <项目工作空间>
python3 <Product Root>/workflow/task.py checklist \
  --issue-key TAP-123 --dir <项目工作空间>
python3 <Product Root>/workflow/task.py record \
  --issue-key TAP-123 --key <checklist 中的键> --value '<已核对事实>' \
  --dir <项目工作空间>
python3 <Product Root>/workflow/task.py advance \
  --issue-key TAP-123 --note '准入必填项已齐备' --dir <项目工作空间>
python3 <Product Root>/workflow/task.py branch \
  --repo tapdata/tapdata --dir <项目工作空间>
python3 <Product Root>/workflow/task.py repository add \
  --issue-key TAP-123 --repo tapdata/tapdata \
  --work-branch feature/TAP-123 --base-branch develop \
  --scope '<已确认范围>' --verification '<验证方式>' \
  --dir <项目工作空间>
python3 <Product Root>/workflow/authorization.py grant \
  --issue-key TAP-123 --agent-id <Agent 标识> --plan-version <方案版本> \
  --dir <项目工作空间>
python3 <Product Root>/workflow/task.py advance \
  --issue-key TAP-123 --note '方案已确认且任务授权有效' --dir <项目工作空间>
```

授权只能在 `design_review` 阶段、且至少已登记一个仓库时签发。CI 观察使用
`workflow/ci.py` 并依赖已登录的 `gh`；先用 `gh auth status` 检查登录状态。实现完成后，
至少记录验证、PR/CI 与证据：

```sh
python3 <Product Root>/workflow/task.py record \
  --issue-key TAP-123 --key verification --value '<实际命令与退出结果>' \
  --dir <项目工作空间>
python3 <Product Root>/workflow/task.py repository record-result \
  --issue-key TAP-123 --repo tapdata/tapdata --pr 123 --ci '<CI 结果>' \
  --dir <项目工作空间>
python3 <Product Root>/workflow/ci.py watch \
  --issue-key TAP-123 --repo tapdata/tapdata --pr 123 --dir <项目工作空间>
python3 <Product Root>/workflow/evidence.py \
  --issue-key TAP-123 --verification '<实际验证结论>' --dir <项目工作空间>
```

`evidence.py` 将 Markdown 输出到标准输出，先经人工审阅后再由 Agent 回写 Jira；命中
敏感信息或不合规验证结论时会拒绝输出，而不是自动删改内容。

## 8. 常见问题

**`doctor` 提示工作空间未初始化**

先执行 `agenticops init`；不要手写 `.agenticops.json` 或复制其它工作空间的 `.gate/`。

**`doctor` 提示绑定或薄接线漂移**

运行 `agenticops repair` 后重新执行 `doctor`。若工作空间已有同名且非
AgenticOps 生成的文件，工具会拒绝覆盖，应先人工确认文件归属。

**启动时找不到 `codex` 或 `claude`**

先安装并确认对应 Agent 命令已在 `PATH`；入口只负责刷新接线和启动，不负责安装
Agent 客户端。

**任务被拒绝或要求人工确认**

先核对 Jira/Git/授权事实和当前任务范围。不要换命令、手改 `.gate/` 或跳过 Hook；
缺事实、权限不足、高风险操作或外部写入结果不明时应按输出完成补卡、确认或人工接力。

## 9. 相关资料

- [v1 工程架构](architecture/agenticops-v1-architecture.md)：分层、工作空间与多任务模型。
- [权限与安全边界](security/permissions.md)：GitHub、Jira 与 Hook 的三层防线。
- [Claude 端到端验证](testing/e2e-claude.md)、[Codex 端到端验证](testing/e2e-codex.md)：平台接线与验收重点。
- `projects/tapdata/skills/tapdata-task/SKILL.md`：TapData 受控研发任务的具体协作流程。
