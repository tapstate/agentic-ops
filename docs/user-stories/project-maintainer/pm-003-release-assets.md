# PM-003 构建 AgenticOps 安装资源

作为项目维护者，
我希望能受控构建 `agentic-cli`、标准资产、安装脚本和校验和，
以便研发负责人能通过稳定安装入口获得 latest 可验证版本。

### 触发方式

```sh
bash scripts/build.sh
bash scripts/test-build.sh
bash tests/e2e/local-install-flow.sh
```

### 前置条件

- 设计、契约、运行资产、测试和文档已经同步。
- 发布内容不包含 secrets、tokens、private keys 或原始敏感日志。
- 构建、提交、人工确认和审计要求已经满足。
- [DL-001 安装 AgenticOps](../development-lead/dl-001-install.md) 已作为本次发版验收条件纳入验证清单。

### 主流程

1. 维护者构建当前平台或多平台 `agentic-cli` 二进制到 `install-resources/<os-arch>/agentic-cli`。
2. 维护者确认 `install-resources/basic/` 中的标准资产已经同步。
3. 维护者生成并校验 `install-resources/checksums.txt`。
4. 维护者按 [DL-001 安装 AgenticOps](../development-lead/dl-001-install.md) 运行安装故事验收。
5. 维护者在人工确认后提交 `install-resources/` 中的已编译产物和校验和。
6. 维护者记录构建和安装验证审计信息。

### 输出

```json
{
  "ok": true,
  "operation": "build",
  "artifact": "agentic-cli",
  "next_action": "verify_install"
}
```

### 失败处理

- 构建失败时停止提交安装资源。
- 校验和不匹配时停止安装或更新。
- 权限不足时返回 `missing_permission`。
- 更新后发现版本不可用时回退到 `.local/previous-ref`，维护者再修复并重新提交 latest。

### 验收标准

- [DL-001 安装 AgenticOps](../development-lead/dl-001-install.md) 必须通过；安装失败、入口不可访问、权限提示不可执行或安装后 `agentic-cli` 不可用时不得发版。
- `install-resources/basic/`、平台二进制和 `checksums.txt` 一致。
- 安装脚本能从 managed clone 安装最新 `agentic-cli` 和运行资产。
- 安装脚本使用仓库中已提交的二进制，不在研发负责人机器上编译。
- 安装资源提交动作受人工确认和审计约束。

### 保护行为

- 构建必须产出可验证的 `agentic-cli` 二进制、标准资产和校验和。
- 提交到仓库的 `install-resources/<os-arch>/agentic-cli` 必须是预先编译好的产物，并由 `checksums.txt` 校验。
- 安装入口必须安装到 `~/.agentic-ops`，不能把具体项目运行资料写入全局安装目录。
- 安装资源提交必须受权限、策略、人工确认和审计记录约束。
- 失败或不可用版本必须能通过 Git 回退或重新提交 latest 修复。

### 审核问题

- 安装资源、版本号和校验和是否一致。
- 安装脚本是否只处理全局安装和通用运行资产。
- 发布过程是否需要人工确认，以及确认记录写在哪里。
- 发布后如何证明安装后的 `agentic-cli` 可运行。

### 验收证据

- DL-001 发版验收记录。
- `bash scripts/test-build.sh`
- `bash tests/e2e/local-install-flow.sh`
- `install-resources/checksums.txt`
- `install-resources/<os-arch>/agentic-cli`
- 发布或安装审计记录。

### 关联设计

- `docs/runtime/versioning.md`
- `docs/runtime/cli-runtime.md`
- `docs/runtime/problem-resolution-and-update.md`
- `scripts/build.sh`
- `scripts/install.sh`
