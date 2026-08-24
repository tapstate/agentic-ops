# 工作流配置

> 本文记录现役 `ProjectProfile` 的机器可解析结构和运行边界。公开命令是否可调用仍以 `ao-work capability list|show` 为准；本文出现字段不代表 Runtime 已提供对应写操作。

## 1. 作用与事实源

Project Profile 把通用 Runtime 绑定到具体业务项目，源头位于：

```text
developer/standards/projects/<profile-id>/profile.yaml
```

Runtime 按以下顺序深度合并，后者覆盖前者：

```text
版本化项目 Profile
< ~/.agentic-ops/user/projects/<profile-id>/profile.local.yaml
< <workspace>/.agentic-ops/profiles/<profile-id>.local.yaml
```

工作空间 `agent.json` 固化 `project_profile`、`connection_id`、`jira_project`、`source_root` 和绑定仓库。Runtime 每次加载 Profile 后都会回验这些绑定，不能靠 overlay 静默切换项目、Jira Connection 或源码身份。

Jira 站点和环境变量名属于 `developer/standards/connections/<connection-id>.yaml`；研发员身份与真实凭证只保存在 developer 安装的 `user/identity.yaml` 和 `user/.env`，不得写入 Profile 或工作空间。

## 2. 现役结构

下面只展示 Runtime 当前解析的字段：

```yaml
schema_version: 1
profile_id: tapdata
connection_id: tapdata-cloud

jira:
  project_key: TAP
  issue_types: [Story, 故事, Bug, 缺陷, Task, 任务]
  task_query: project = TAP AND assignee = currentUser() AND statusCategory != Done

repositories:
  default: tapdata/tapdata
  list:
    - tapdata/tapdata
    - tapdata/tapdata-enterprise

  analysis_mount:
    mode: all                 # all | include | exclude
    include: []
    exclude: []

  worktree_domains:
    - id: product
      baseline_repository: tapdata/tapdata
      repositories:
        - tapdata/tapdata
        - tapdata/tapdata-enterprise

  branches:
    derive_from: default
    default_branch: main
    default_rule: same_name
    baseline_branches:
      tapdata/tapdata: develop
      tapdata/tapdata-enterprise: develop
    dev_branches:
      tapdata/tapdata: develop
      tapdata/tapdata-enterprise: develop
    overrides:
      - from_branch: release-v3.30
        repo: tapdata/tapdata-enterprise
        branch: release-v3.30

workspace:
  source_root: /optional/independent/checkout
  repository: tapdata/tapdata

fields:
  owner:
    source: jira_field
    jira_field: assignee
    state: read_only
    required: true
  problem_version:
    source: jira_description_section
    section: 问题版本
    state: active
    writable: true
  target_repo:
    source: workspace_repo_mapping
    state: active
    required: true
  target_branch:
    source: task_worktree_mapping
    state: active
    required: true

statuses:
  打开: waiting_takeover
  正在进行: implementation
  完成: completed

transitions:
  start_progress:
    name: Implementation started
    id: "91"
    from: [打开]
    to: 正在进行
```

## 3. Jira 与字段映射

- `jira.project_key`、`issue_types` 和 `task_query` 限定候选任务及项目边界。
- `fields` 只接受 `source`、`jira_field`、`section`、`state`、`writable`、`required`。`state` 只能是 `active`、`read_only`、`pending_validation`、`unsupported` 或 `deprecated`。
- `writable: true` 是字段写入白名单元数据，不会自行产生写能力。若能力目录仍标为 `capability_gap`，AIAgent 必须停止，不能根据 Profile 拼装 Jira 写请求。
- Jira `Assignee`、`Status` 和受管 Comment 是接管事实；`agentic_run_id`、内部阶段、幂等记录和证据保存在本地任务状态，不映射为业务 Jira Custom Field。

现役 `target_repo` 有一个需要明确记录的特殊路径：Profile 仍声明 `source: workspace_repo_mapping`，但池模式在建立来源上下文前会直接读取 Jira 描述的“目标仓库”首行。值不在 `repositories.list` 时返回 `target_repository_unknown`；章节缺失时回退 `repositories.default`。Runtime 尚未根据其它 Jira 文本、源码命中或路径线索推断候选仓库，也不会检测多线索领域冲突。

## 4. 仓库池、领域和分支

- `repositories.default` 和 `list` 均使用唯一的 `owner/repository`；声明 `list` 时，`default` 必须在列表内。
- `workspace init` 在中央源码池准备 `repository_candidates()` 返回的全部成员；任务接管只刷新、验证并挂载已有池成员，不补 clone。
- `analysis_mount` 仅在没有显式领域的兼容 Profile 中计算分析集合。`include` 和 `exclude` 引用的仓库必须在 `list` 内。
- `worktree_domains` 的成员不得重叠，`baseline_repository` 必须同时属于该领域和 `repositories.list`。TapData 必须显式声明领域；目标仓库未映射时失败关闭，不回退全量仓库。
- `branches.baseline_branches` 给出未声明问题版本时目标仓库的显式基线；一旦配置该映射，缺少具体仓库条目时不能猜 `default_branch`。
- 其它领域的分支推导顺序为：基线仓库使用问题版本；精确 `overrides`；当问题版本等于基线仓库开发分支时使用目标仓库 `dev_branches`；最后仅支持 `default_rule: same_name`。
- TapData 产品域使用版本化 `tap_align_branches.py plan --no-fetch --remote-only` 计算领域内逐仓分支。Runtime 在创建任何工作树前解析全部远端提交，已有工作树也必须与同一批提交一致。

池模式任务目录固定为：

```text
<source_pool_root>/.worktree/<JIRA-KEY>/<问题版本>/<repo-short-name>
```

当前来源上下文只把目标仓库工作树设为 `source_root`，并输出目标仓库的 `problem_version`、`target_branch` 和路径；尚未把领域内逐仓分支、远端提交和对齐理由作为完整证据输出。

## 5. 状态与 transition

- `statuses` 把真实 Jira 状态名映射到 `waiting_takeover`、`implementation`、`completed` 等标准阶段。
- `transitions` 是唯一的 Jira 流转配置点，结构为 `{name, id, from, to}`。
- 配置 `id` 时优先按 ID 匹配，并严格校验起止状态；未配置时只允许唯一名称匹配。候选重复、当前不可用、目标不符或回读不一致都必须阻断。
- 未配置的流程不得由 AIAgent 猜测；应先读取 Jira 可用 transitions，再通过版本化 Profile 增补。
- AIAgent 默认无合入权，不能仅凭 `statuses` 配置把卡片推进完成态。

## 6. 不属于现役 Profile 的概念

下列旧文档概念当前不由 `ProjectProfile` 解析，不能写入 YAML 后声称已经适配：

- `jira.user`、`jira_form_mapping`、`task_class_mapping`、`standard_process_mapping`。
- `github.repositories.by_component`、`github.repositories.by_label`。
- `local.tasks_dir`、`human_gates`、`review_gates`、`retry_redo`、`templates`。
- 分离的 `transition_mapping` 与 `jira_transition_mapping`。

任务分类、流程选择、人工门禁、重试和证据模板分别由 Runtime、Operation Contract、策略和标准流程资产承载。若要把这些概念引入 Profile，必须先更新配置模型、校验、能力目录、测试和本文，不能只加示例字段。

## 7. 验证边界

Profile 加载会校验标识、仓库格式与引用、领域重叠、分支映射和 transition 结构。配置错误统一按 Project Profile 配置失败关闭；部分设计中的细分失败码尚未实现。

修改版本化 Profile 或本契约后至少运行资源契约和 Python Runtime 验收；涉及初始化、源码池或发布信任根时还必须运行项目规定的四项固定完整验证。
