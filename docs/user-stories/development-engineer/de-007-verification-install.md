# DE-007 指定分支验证安装

作为研发工程师或项目维护者，
我希望把 AgenticOps 的指定非 `main` 分支安装到独立验证目录并用它初始化研发员，
以便在发布到 `main` 前端到端验证未发布功能。

> 本故事是发版验收条件。验证安装不得弱化生产安装身份、工作面隔离或 `main` 祖先门禁。

### 触发方式

远程模式（默认，可运行）：

```sh
bash developer/bootstrap/install-verify-branch.sh \
  --source-branch develop \
  --json
```

本地流程验证（不可运行）：

```sh
bash developer/bootstrap/install-verify-branch.sh \
  --source-worktree . \
  --source-branch develop \
  --json
```

### 前置条件

- 远程模式：`--source-branch` 已推送到 `tapstate/agentic-ops`，本机具备 SSH 只读访问。
- 本地模式：`--source-worktree` 指向本地 AgenticOps 源码目录且存在该分支。
- 验证安装目标不得是 `~/.agentic-ops`。

### 主流程

1. 远程模式先校验 Git URL 未被改写，再 `ls-remote` 确认分支存在；本地模式校验 worktree 与本地分支存在。
2. 从来源克隆 `--single-branch --branch <branch>`；远程模式固定 origin 为官方仓库。
3. 配置 developer-only sparse checkout 后再检出，确保 `maintainer/` 与 `.agentic-ops-source` 不落盘。
4. 写入 `.agentic-ops/verification-only` 标记，记录来源（remote/local）、分支与时间。
5. 校验 sparse 精确集、developer 分发白名单、shared 协议树，同步 uv 运行时并写 `.local/current-ref`。
6. 远程模式产物可运行：`ao-work workspace init` 可初始化研发员；本地模式 origin 为本地路径，不可运行。

### 验收标准

- 远程模式从官方远端按指定分支克隆，产物 origin 精确等于 `tapstate/agentic-ops`。
- 验证安装的 `ao-work` 复用生产安装身份校验，仅把「HEAD 是 `origin/main` 祖先」放宽为「HEAD 可达于任一 `origin/*` 远端引用」；该放宽只在 `verification-only` 标记存在时生效。
- 无 `verification-only` 标记的非 `main` 安装仍被 Runtime 阻断。
- `--source-worktree` 本地模式只校验安装流程，不可运行。
- 验证安装禁止写入 `~/.agentic-ops`；`install.sh`、`update.sh`、`rollback.sh` 拒绝带标记的验证目录。
- 验证安装不引入 maintainer 资产，sparse 精确集与分发白名单与生产一致。

### 保护行为

- 生产安装 `~/.agentic-ops` 仍固定 `main`，不接受分支覆盖。
- `verification-only` 标记只放宽「`main` 祖先」一条，不授予任何额外能力。
- origin 必须是官方 `tapstate/agentic-ops`，拒绝 `url.*.insteadOf` / `pushInsteadOf` 改写。

### 验收证据

- 远程模式离线夹具安装，以及 `ao-work capability list` / `workspace inspect` 可运行结果。
- `source_branch_not_found`、`verification_home_forbidden` 与无标记非 `main` 阻断结果。
- `verification_branch_unreachable` 阻断结果（HEAD 不可达于任一 `origin/*` 引用）。
- 生产安装、更新、回滚回归结果。

### 关联设计

- `docs/architecture/verification-install-design.md`
- `docs/architecture/project-structure.md`
- `docs/runtime/python-runtime.md`
- `developer/bootstrap/install-verify-branch.sh`
- `developer/runtime/src/ao_work/installation.py`
