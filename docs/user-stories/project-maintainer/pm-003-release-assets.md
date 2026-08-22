# PM-003 发布 AgenticOps developer 交付物

作为项目维护者，
我希望能受控验证和发布 developer 工作面，
以便研发工程师通过稳定安装入口获得 latest 可验证版本，而不会收到 maintainer 能力。

### 触发方式

```sh
maintainer/scripts/release.sh prepare --version vX.Y
maintainer/scripts/release.sh publish --version vX.Y
```

### 前置条件

- 设计、契约、两个工作面、测试和文档已同步。
- [DE-001](../development-engineer/de-001-install.md) 已纳入发版验收。
- 发布内容不包含 secrets、tokens、private keys 或原始敏感日志。
- `main` 只允许通过 PR 的 Merge commit 合入。

### 主流程

1. 维护者在 maintainer 工作面固定待发布 `develop` HEAD。
2. `prepare` 验证 Python 锁文件、developer Skill / Rule / 标准资产、Shell Bootstrap 和工作面隔离。
3. 在临时目录按 DE-001 执行 developer-only sparse 安装。
4. 验证 `ao-work` 可运行、`maintainer/` 不在安装文件树、无兼容别名和跨包导入。
5. 软门禁的 `prepare` 先固定本地 `release/vX.Y`；`publish` 在最终确认后推送该分支并创建或复用目标为 `main` 的 PR，等待合并事实。
6. 脚本确认 `origin/main` 包含固定发布 HEAD 且 PR 使用 Merge commit 后，先将 `develop` 快进到已验证的 `origin/main`，再在该 Merge commit 创建 Tag、记录发布审计；快进不成立时必须失败关闭。
7. Hotfix 从包含当前 `main` 的 `develop` 固定修复线，合入后自动快进远端和本地 `develop` 并切回；其审计必须声明 `tag_action=none`，不得执行任何 Tag 写操作。

### 验收标准

- DE-001 必须通过。
- `ao-maint` 只在源头仓库使用，不进入 developer 安装。
- `ao-work`、developer Runtime、Skill、Rule、标准和 Bootstrap 来自同一固定提交。
- 不构建或发布 Go 平台二进制；旧 Go Runtime、`agentic-cli` 和旧分发资产已删除，资源验证必须阻止其重新进入现役结构。
- 流程禁止直推 `main`，软门禁不能伪装成服务器端保护。
- 更新后不可用时能按 Git ref 回滚 latest 安装。

### 保护行为

- developer 发布物只能包含 `developer/` 及明确批准的根版本元数据，不得包含 `maintainer/`。
- `ao-work` 必须由 developer Runtime 自检通过，且不能接受切换工作面的参数。
- 发布、回滚和安装都必须保持同一 developer-only 资产集合；失败时不得留下可被误认为完成的安装状态。
- `ao-maint`、项目维护授权和项目故事状态不得进入研发安装目录。
- `.local/release-runs` 审计路径必须逐级拒绝符号链接、特殊文件和物理越界，并以同目录临时文件原子写入普通 JSON；审计落盘失败时不得报告发布完成或等待状态。

### 验收证据

- DE-001 发版验收记录。
- maintainer/developer Runtime 与边界测试。
- developer-only 安装、更新和回滚测试。
- PR、合并事实和发布审计记录。

### 关联设计

- `docs/architecture/project-structure.md`
- `docs/runtime/python-runtime.md`
- `docs/architecture/source-release-workflow-design.md`
- `maintainer/scripts/release.sh`
