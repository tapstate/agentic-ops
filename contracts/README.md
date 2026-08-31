# AgenticOps 标准契约

`contracts/` 是 Agent、Tool Adapter 与 Gate Core 之间的唯一协议事实源。

- `gate-request.schema.json`：Adapter 交给 Gate 的标准操作请求。
- `gate-decision.schema.json`：Gate 返回给 Adapter 的三态标准判定。
- `adapter-manifest.schema.json`：Agent 能力和生成产物声明。
- `operation-catalog.json`：标准操作名称、类别、语义和是否可作为请求输入。
- `product-state.schema.json`：产品根目录（Product Root）的本地模式、跟踪分支和版本状态。
- `workspace.schema.json`：产品根目录、项目和 Agent 集合的工作空间配置。
- `workspace-init.schema.json`：生成接线的产品版本、普通文件内容哈希，以及中央 Project
  Skill 的受控符号链接清单。
- `task-registry.schema.json`：项目工作空间内多个任务的统一注册与激活状态。
- `task-state.schema.json`：每个 Jira 任务统一的阶段、事实、仓库和恢复状态。

## 兼容规则

- 协议使用整数 `protocol_version`，Manifest 和操作词表使用 `schema_version`。
- 新增可选字段或标准操作可以保持当前版本，但必须补充一致性测试。
- `task-state-v1` 的仓库 `authorized_endpoint` 是向后兼容的可选字段；新登记仓库必须
  从 Project catalog 固化该字段，旧任务可在受控 `repository prepare` 时迁移。
  旧授权不会被静默补写：缺少该字段时非 push 操作继续按 v1 通用绑定校验，push
  必须失败关闭并重新签发授权。
- 删除字段、增加必填字段或改变既有字段和操作语义必须升级主版本。
- 未知版本、缺失字段和契约与 Policy 漂移必须拒绝；未知操作必须转人工。
- Adapter、Gate、Policy 不得私自定义未登记的标准操作或字段语义。
