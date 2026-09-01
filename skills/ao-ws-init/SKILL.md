---
name: ao-ws-init
description: 创建、更新或受控清理 AgenticOps 项目工作空间；适用于确认 Product Root、Source Pool、目录和项目绑定，不用于业务任务接管。
---

# AgenticOps 工作空间初始化

用于“创建工作空间”“更新工作空间”或“重建工作空间绑定”。工作空间是中央 Product Root 的薄接线：只保存 `.agenticops/` 配置和按任务隔离的本地状态；项目规则、Policy 和 Skill 仍在 Product Root。不要将此 Skill 用于接管 Jira 任务、准备业务仓库、修改业务代码，或把 Source Pool 当作工作空间。

## 先判定目标

先只读解析所有路径并检查 Product Root：它必须含有 `agenticops`、`contracts/gate-request.schema.json` 和所选 `projects/<project>/`。用户说“当前项目”时，默认 Product Root 是当前 AgenticOps 项目根目录；必须明确回读这个默认值，不能把当前目录同时当作 Product Root 和工作空间。

工作空间目录必须由用户给出，或由上下文唯一给出且已回读确认。它不能是、不能位于、也不能包含 Product Root 或 Source Pool。先检查：

- `<product-root>/agenticops doctor --workspace <workspace>`（已绑定时）；
- `<product-root>/agenticops workspace list` 和 `<workspace>/.agenticops/workspace.json`（绑定归属与已有状态）；
- `<product-root>/bootstrap/repository_pool.py --product-root <product-root> read`（Product Root 默认 Source Pool）。

不要手改 `.agenticops/workspace.json`、`init.json`、生成的入口或 Skill 链接。

## 已存在工作空间：先问“清理还是更新”

当 `<workspace>/.agenticops/workspace.json` 表明它已绑定到当前 Product Root 时，先展示现有的 Product Root、workspace ID、项目、Source Pool、已接入 Agent、`doctor` 结果及任务数量，然后停止并请用户选择：**更新**、**清理**或取消。不得直接执行 `init`，也不得静默换项目、Source Pool 或 Agent 集合。

- **更新**：只可修复或刷新既有生成接线，保留当前绑定和任务语义。取得用户选择后执行 `<product-root>/agenticops repair --workspace <workspace>`，再执行 `doctor` 并回读结果。若用户想改项目、Source Pool 或接入 Agent，说明这不是更新；必须走清理后重建。
- **清理**：这是破坏性操作。先以只读方式列出该工作空间和任务状态，说明 `workspace purge` 会删除受管接线、绑定和所有本地任务状态；它会先清理受控 linked worktree，脏 worktree 或未知状态会停止并保留现场。获得用户对**精确工作空间路径和此删除范围**的明确确认后，才执行 `<product-root>/agenticops workspace purge --workspace <workspace> --yes`。不得使用 `--all`，不得删除 Source Pool、业务仓库或未受管文件。清理成功后，如用户仍要创建，重新走下节的创建确认。

若工作空间绑定到其他 Product Root、绑定文件无法读取，或路径存在但不是受管工作空间，停止并报告事实；不要 repair、purge 或覆盖它。路径存在且含有非受管文件时，创建前还要列出这些文件并取得针对该目录的明确确认。

## 新建：先确认四项绑定

在创建目录、Source Pool 或任何接线前，汇总并请用户一次确认以下四项。每项有默认值时必须明确标为“默认”：

| 必须确认的输入 | 取值规则 |
| --- | --- |
| 产品根目录（Product Root） | 默认当前 AgenticOps 项目根目录；必须先验证为完整 Product Root。 |
| 源码池（Source Pool） | 默认读取 Product Root 已配置的池；若尚未配置，提出 `<product-root>-repos` 作为**待确认默认**，并在创建命令中显式传入。不能与 Product Root 或工作空间嵌套。 |
| 工作目录（Workspace） | 没有安全默认值，必须给出绝对路径；不得使用 Product Root、Source Pool 及其子目录。 |
| 项目（Project） | 默认 `tapdata`，但只在 `<product-root>/projects/tapdata/` 存在时可用；其它值必须存在相应项目目录。 |

同时回读 Agent 接入范围：未指定时 `agenticops init` 默认接入全部已声明 Agent；若用户指定 Agent，逐个验证其 ID。确认只授权创建该工作空间的受管接线和必要的空 Source Pool 目录；不授权下载业务仓库、接管任务、提交、推送、PR、合并或发布。

用户确认后，按 Source Pool 的来源构造命令：确认的是已配置的 Product Root 默认池时，**不要**传 `--repository-pool`，以保留 `product-default` 绑定；用户选择独立池，或 Product Root 尚未配置而确认采用 `<product-root>-repos` 时，才显式传入该参数并形成 `workspace-override` 绑定。

默认池的命令为：

```sh
<product-root>/agenticops init --workspace <workspace> --project <project>
```

独立池或待确认默认池的命令为：

```sh
<product-root>/agenticops init --workspace <workspace> --project <project> --repository-pool <source-pool>
```

仅在用户已指定 Agent 集合时追加重复的 `--agent <agent-id>`。随后执行：

```sh
<product-root>/agenticops doctor --workspace <workspace>
```

回读工作空间路径、workspace ID、Product Root、项目、Source Pool（含 `product-default` 或 `workspace-override` 来源）、接入 Agent 和 `doctor` 结果。失败时不要改参数重试或手工补文件；报告失败原因和未写入/已写入的事实，等待用户决定。

## 结果边界

创建或更新成功只说明本地工作空间接线和检查通过；它不是 Jira 接管、业务源码基线、PR、CI 或发布验收。后续研发任务应在该工作空间内使用对应项目 Skill，并在任务 worktree 中修改业务代码。
