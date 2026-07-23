# PM-003 发布 AgenticOps 版本和安装资产

作为项目维护者，
我希望能受控发布 `agentic-cli`、标准资产、安装脚本和版本清单，
以便研发负责人能通过稳定安装入口获得可验证版本。

### 触发方式

```sh
bash scripts/release.sh
bash scripts/test-build-release.sh
bash scripts/publish-release.sh <release_dir>
```

### 前置条件

- 设计、契约、运行资产、测试和文档已经同步。
- 发布内容不包含 secrets、tokens、private keys 或原始敏感日志。
- 发布权限、人工确认和审计要求已经满足。

### 主流程

1. 维护者构建当前平台或多平台 release 产物。
2. 维护者生成版本清单、校验和和安装资产。
3. 维护者运行本地发布安装闭环验证。
4. 维护者在人工确认后发布到 GitHub Release。
5. 维护者记录发布审计信息。

### 输出

```json
{
  "ok": true,
  "operation": "publish_release",
  "artifact": "agentic-cli",
  "next_action": "verify_install"
}
```

### 失败处理

- 构建失败时停止发布。
- 校验和不匹配时停止安装或发布。
- 权限不足时返回 `missing_permission`。
- 发布后发现版本不可用时进入回滚或重新发布流程。

### 验收标准

- release 产物、版本清单和校验和一致。
- 安装脚本能安装发布后的 `agentic-cli` 和运行资产。
- 发布动作受人工确认和审计约束。

### 保护行为

- 发布必须产出可验证的 `agentic-cli` 二进制、标准资产、版本清单和校验和。
- 安装入口必须安装到 `~/.agentic-ops`，不能把具体项目运行资料写入全局安装目录。
- 发布动作必须受权限、策略、人工确认和审计记录约束。
- 失败或不可用版本必须能进入受控回滚或重新发布流程。

### 审核问题

- release 产物、版本号、清单和校验和是否一致。
- 安装脚本是否只处理全局安装和通用运行资产。
- 发布过程是否需要人工确认，以及确认记录写在哪里。
- 发布后如何证明安装后的 `agentic-cli` 可运行。

### 验收证据

- `bash scripts/test-build-release.sh`
- `bash tests/e2e/local-release-install-flow.sh`
- release directory 中的版本清单和校验和。
- 发布或安装审计记录。

### 关联设计

- `docs/runtime/versioning.md`
- `docs/runtime/cli-runtime.md`
- `docs/runtime/problem-resolution-and-update.md`
- `scripts/release.sh`
- `scripts/init.sh`
- `scripts/publish-release.sh`
