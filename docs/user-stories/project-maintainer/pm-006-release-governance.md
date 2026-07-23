# PM-006 治理发布权限、回滚和兼容性

作为项目维护者，
我希望 AgenticOps 的发布、回滚和兼容性有明确治理边界，
以便研发负责人能安全升级，AIAgent 不会在不兼容资产上继续执行高风险操作。

### 触发方式

```sh
agentic-cli update check
agentic-cli update apply
agentic-cli update rollback
```

### 前置条件

- 已确认 latest-only 支持策略。
- 已有版本清单、校验和和本地当前版本记录。
- 发布权限、回滚权限和审计记录要求明确。

### 主流程

1. CLI 检查当前版本和远程版本。
2. CLI 判断更新严重程度和受影响操作。
3. 必要更新只阻断受影响操作。
4. 应用更新前校验产物。
5. 更新失败时回滚到上一个可用版本。
6. 维护者记录发布、更新或回滚审计信息。

### 输出

```json
{
  "ok": true,
  "operation": "update_apply",
  "previous_version": "RES-v0.1.3-a68372d",
  "current_version": "RES-v0.1.4-b7c29e1",
  "next_action": "run_preflight"
}
```

### 失败处理

- 版本清单不可达时提示网络或权限问题。
- 产物校验失败时拒绝切换。
- 更新后 `preflight` 失败时进入 rollback。
- 跨版本兼容最低承诺不明确时，必须提示用户决策。

### 验收标准

- 更新、发布和回滚都有结构化审计记录。
- 不兼容版本不会继续执行受影响的高风险操作。
- latest-only 支持策略不会被误读为维护旧版本补丁线。

### 保护行为

- AgenticOps 使用 latest-only 支持策略，不维护旧版本补丁线。
- 更新前必须校验版本清单和产物校验和。
- 必要更新只能阻断受影响操作，不能无差别阻断所有工作。
- 更新失败或新版本不可用时必须能回滚到上一个可用版本。

### 审核问题

- 当前版本和目标版本如何识别。
- 哪些操作会被必要更新阻断，阻断理由是什么。
- 回滚需要哪些本地记录和审计信息。
- 跨版本兼容最低承诺是否已经由用户决策。

### 验收证据

- `agentic-cli update check` 输出。
- `agentic-cli update apply` 输出。
- `agentic-cli update rollback` 输出。
- 发布、更新或回滚的结构化审计记录。

### 关联设计

- `docs/runtime/versioning.md`
- `docs/runtime/problem-resolution-and-update.md`
- `docs/architecture/full-design-implementation-design.md`
- `docs/development-phase-rules.md`
