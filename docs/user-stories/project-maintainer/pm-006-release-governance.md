# PM-006 治理 latest 更新、回滚和兼容性

作为项目维护者，
我希望 AgenticOps 的 latest 更新、回滚和兼容性有明确治理边界，
以便研发工程师能安全升级，AIAgent 不会在不兼容资产上继续执行高风险操作。

### 触发方式

```sh
bash developer/bootstrap/install.sh
bash developer/bootstrap/update.sh
bash developer/bootstrap/rollback.sh
bash developer/tests/bootstrap/test_install_boundary.sh
```

### 前置条件

- 已确认 latest-only 支持策略。
- 目标目录是 developer-only sparse managed clone，不包含 `maintainer/`。
- 当前环境可找到 Git、`uv` 和锁定的 developer Python 项目。
- 已确认 latest-only 更新、回滚权限和审计记录要求。

### 主流程

1. Bootstrap 检查目标目录是 managed clone，并重建只包含 `developer/`、只读 `shared/` 协议与 `.python-version` 的 sparse checkout 边界。
2. 更新入口记录当前 commit，然后对配置分支执行 fetch 和 fast-forward-only 更新。
3. Bootstrap 执行 `uv sync --locked --project <install-dir>/developer --python 3.12`，重建 `bin/ao-work` 并执行入口自检。
4. 运行时同步或自检失败时，把更新前 commit 写入 `.local/pending-rollback-ref` 并停止；回滚入口优先使用该引用。
5. 成功后写入 `.local/current-ref` 和 `.local/previous-ref`，并输出不含凭据的结构化安装、更新或回滚结果。

### 输出

```json
{
  "ok": true,
  "operation": "bootstrap_update",
  "status": "completed",
  "retry_safe": true,
  "previous_ref": "<git-commit>",
  "current_ref": "<git-commit>"
}
```

### 失败处理

- GitHub clone 或 fetch 不可达时提示网络或权限问题。
- 目标不是 managed clone、developer 分发被 maintainer 资产污染、`uv` 不可用或锁定依赖不一致时必须停止。
- 更新后 Runtime 同步或 `ao-work --help` 自检失败时保留 `.local/pending-rollback-ref`，要求显式执行回滚。
- 跨版本兼容最低承诺不明确时，必须提示用户决策。

### 验收标准

- 安装、更新和回滚都有结构化结果与引用状态记录。
- 安装、更新和回滚后都保持 developer-only 分发边界，且锁定 Python Runtime 可启动。
- 不兼容版本不会继续执行受影响的高风险操作。
- latest-only 支持策略不会被误读为维护旧版本补丁线。

### 保护行为

- AgenticOps 使用 latest-only 支持策略，不维护旧版本补丁线。
- 安装和更新必须使用已提交的 developer 锁文件和 `uv sync --locked`，不得临时解析未锁定依赖。
- developer 安装不得出现 `maintainer/`、`ao-maint` 或 maintainer Runtime、Skill、Rule、授权和配置。
- 必要更新只能阻断受影响操作，不能无差别阻断所有工作。
- 更新失败或新版本不可用时必须能回滚到上一个可用版本。

### 审核问题

- 当前 commit、目标 commit 和待回滚 commit 如何识别。
- sparse checkout 和 Python 锁文件如何阻止跨工作面资产或未锁定依赖进入安装。
- 哪些操作会被必要更新阻断，阻断理由是什么。
- 回滚需要哪些本地记录和审计信息。
- 跨版本兼容最低承诺是否已经由用户决策。

### 验收证据

- `bash developer/tests/bootstrap/test_install_boundary.sh` 的安装、更新、回滚和工作面隔离结果。
- `bash maintainer/scripts/test-resources.sh` 的锁文件、Bootstrap 资产和旧分发残留检查结果。
- `.local/current-ref`、`.local/previous-ref`、必要时的 `.local/pending-rollback-ref` 和不含凭据的结构化命令输出。

### 关联设计

- `docs/runtime/versioning.md`
- `docs/runtime/problem-resolution-and-update.md`
- `docs/architecture/full-design-implementation-design.md`
- `docs/architecture/source-release-workflow-design.md`
