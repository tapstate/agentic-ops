# PM-002 维护操作契约、标准流程和工作流配置

作为项目维护者，
我希望能维护 AgenticOps 的操作契约、标准流程注册处和工作流配置，
以便 AIAgent 面向稳定标准工作，而不是直接猜测 Jira、GitHub 或 Git 的底层事实。

### 触发方式

```text
新增或调整一个受控操作。
新增或调整一个任务分类和标准流程。
适配一个项目的 Jira 工作流配置。
```

### 前置条件

- 已确认对应故事线和设计边界。
- 已明确标准字段、阶段、动作、失败码和人工确认点。
- 已明确具体项目 Jira 字段、状态、`transition` 和代码仓库映射来源。

### 主流程

1. 维护者更新操作契约。
2. 维护者更新标准流程注册处。
3. 维护者更新或新增 workflow profile。
4. 维护者运行契约和配置校验。
5. 维护者补充对应测试和示例输出。
6. 维护者更新相关文档入口。

### 输出

```json
{
  "ok": true,
  "operation": "maintain_standard_assets",
  "validated_assets": [
    "operation_contract",
    "standard_process_registry",
    "workflow_profile"
  ],
  "agentic_next_action": "run_e2e"
}
```

### 失败处理

- 未知 Jira 状态、缺失字段映射或缺失标准流程时，返回稳定缺口。
- `transition` 采用 ID 严格优先、唯一名称受控兜底；名称重复、来源/目标状态不匹配或事实不一致时不能自动裁决，必须阻断并提示流程负责人处理。
- 缺少测试覆盖时，不能把配置描述为可正式使用。

### 验收标准

- AIAgent 不需要直接理解 Jira 自定义字段和工作流状态。
- CLI 能校验操作契约、标准流程和 workflow profile。
- 缺失映射能输出稳定错误码、缺口说明和所需人工动作。

### 保护行为

- AIAgent 不能直接猜测 Jira 字段、状态、`transition`、目标仓库或标准流程。
- 未知 Jira 状态、缺失字段映射、缺失任务分类或缺失标准流程必须输出稳定缺口。
- 操作契约必须声明输入、输出、前置门禁、失败码、副作用和人工确认要求。
- workflow profile 必须承载具体项目 Jira / GitHub / 本地路径映射。

### 审核问题

- 新增或调整的能力是否已有明确操作契约。
- 标准流程注册处是否能解释任务分类、阶段、责任角色和完成清理。
- workflow profile 是否避免把具体项目事实写死到通用规则。
- 缺失映射时是否有稳定错误码和人工动作。

### 验收证据

- 操作契约校验输出。
- 标准流程注册处校验输出。
- workflow profile 校验输出。
- 缺失字段、未知状态或缺失映射的结构化失败输出。

### 关联设计

- `docs/contracts/operation-contract.md`
- `docs/processes/standard-process-registry.md`
- `docs/profiles/workflow-profile.md`
- `docs/forms/task-form-standard.md`
- `docs/architecture/full-design-implementation-design.md`
