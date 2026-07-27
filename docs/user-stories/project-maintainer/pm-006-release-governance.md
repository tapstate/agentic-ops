# PM-006 治理 latest 更新、回滚和兼容性

作为项目维护者，
我希望 AgenticOps 的 latest 更新、回滚和兼容性有明确治理边界，
以便研发工程师能安全升级，AIAgent 不会在不兼容资产上继续执行高风险操作。

### 触发方式

```sh
bash scripts/install.sh
bash tests/e2e/local-install-flow.sh
```

### 前置条件

- 已确认 latest-only 支持策略。
- 已有 `install-resources/checksums.txt` 和 `.local/current-ref`。
- 安装资源提交、回滚权限和审计记录要求明确。

### 主流程

1. 安装脚本检查当前 managed clone commit。
2. 安装脚本更新到 `origin/main` latest。
3. 应用更新前校验 `install-resources/checksums.txt`。
4. 更新失败时回滚到 `.local/previous-ref`。
5. 维护者记录安装资源提交、更新或回滚审计信息。

### 输出

```json
{
  "ok": true,
  "operation": "update",
  "previous_ref": "<git-commit>",
  "current_ref": "<git-commit>",
  "next_action": "workspace_init"
}
```

### 失败处理

- GitHub clone 或 fetch 不可达时提示网络或权限问题。
- 安装资源校验失败时拒绝切换。
- 更新后 `preflight` 失败时回滚到 `.local/previous-ref`。
- 跨版本兼容最低承诺不明确时，必须提示用户决策。

### 验收标准

- 更新、安装资源提交和回滚都有结构化审计记录。
- 不兼容版本不会继续执行受影响的高风险操作。
- latest-only 支持策略不会被误读为维护旧版本补丁线。

### 保护行为

- AgenticOps 使用 latest-only 支持策略，不维护旧版本补丁线。
- 更新前必须校验安装资源校验和。
- 必要更新只能阻断受影响操作，不能无差别阻断所有工作。
- 更新失败或新版本不可用时必须能回滚到上一个可用版本。

### 审核问题

- 当前 commit 和目标 commit 如何识别。
- 哪些操作会被必要更新阻断，阻断理由是什么。
- 回滚需要哪些本地记录和审计信息。
- 跨版本兼容最低承诺是否已经由用户决策。

### 验收证据

- `bash scripts/install.sh` 输出。
- `.local/current-ref`、`.local/previous-ref` 和 `.local/install-log.json`。
- 安装资源提交、更新或回滚的结构化审计记录。

### 关联设计

- `docs/runtime/versioning.md`
- `docs/runtime/problem-resolution-and-update.md`
- `docs/architecture/full-design-implementation-design.md`
- `docs/development-phase-rules.md`
