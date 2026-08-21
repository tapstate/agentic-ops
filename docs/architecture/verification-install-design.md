# AgenticOps 指定分支验证安装设计

## 1. 目标与范围

本文定义 `tapstate/agentic-ops` 的「指定分支验证安装」：让研发工程师或项目维护者把非 `main` 分支（典型是 `develop`，也允许已推送的其它分支或 tag）安装到独立验证目录，并用该分支的 `ao-work` 初始化一名研发员，端到端验证尚未发布到 `main` 的功能，再决定是否按《源码发布工作流设计》发布。

本文只改变「安装来源分支」这一条边界；生产安装 `~/.agentic-ops` 仍然固定稳定 `main`，不接受任何分支覆盖。验证安装不授予任何超出生产安装的能力，也不放宽工作面隔离、安装身份、sparse 精确集、shared 协议白名单或 developer 分发白名单。

## 2. 现状与根因

- 生产安装 `developer/bootstrap/install.sh` 固定 `BRANCH=main`，并拒绝 `AGENTIC_OPS_BRANCH` 等身份覆盖环境变量；`~/.agentic-ops` 的 managed clone origin 必须精确等于 `tapstate/agentic-ops`。
- Runtime `developer/runtime/src/ao_work/installation.py` 的 `validate_install_root()` 对每个真实命令强制要求：origin 是官方仓库、`.local/current-ref` 与 HEAD 一致、HEAD 是 `origin/main` 的祖先、sparse 精确集与 shared/developer 分发白名单一致、无受管文件改动。
- 既有 `install-verify-branch.sh`（本地验证入口）从本地源码 worktree `--single-branch` 克隆，origin 是本地路径、不写 `.local/current-ref`，因此其 `ao-work` 会被 `install_origin_mismatch` / `install_ref_integrity_invalid` 阻断，无法执行 `workspace init` 初始化研发员。

## 3. 方案

引入「可运行的验证安装」：

- `install-verify-branch.sh` 默认从官方远端 `git@github.com:tapstate/agentic-ops.git` 按 `--source-branch` 克隆，`ls-remote` 先校验分支存在，克隆后写 `.local/current-ref`，并写入 `.agentic-ops/verification-only` 标记。
- `install-verify-branch.sh` 同时支持由 `gh api` 下载后通过标准输入启动；此时按 `--source-branch` 从同一远端分支加载 `developer/bootstrap/lib/common.sh`。调用侧必须先确认下载成功再执行完整脚本，404 或未授权响应不得进入 `bash`。
- Runtime 识别 `.agentic-ops/verification-only` 标记进入「验证安装身份模式」：把「HEAD 必须是 `origin/main` 祖先」这一条放宽为「HEAD 必须可达于任一 `refs/remotes/origin/*` 远端分支或 tag」；origin、sparse 精确集、shared/developer 分发白名单、`.local/current-ref` 一致性、无受管改动等校验全部保持不变。
- `--source-worktree` 保留为「仅验证安装流程」的本地场景：从本地 worktree 克隆、只校验 sparse/分发/runtime 同步，origin 是本地路径，因此不可运行（与现状一致），用于测试尚未推送的本地改动能否正确完成安装。

## 4. 安全边界（不弱化项）

- 验证安装与生产安装一样，origin 必须精确等于官方 `tapstate/agentic-ops`（`git@` / `ssh://` / `https://` 三种形式），并拒绝 `url.*.insteadOf` / `pushInsteadOf` 改写。
- sparse 精确集、shared 协议树、developer 分发白名单、无 maintainer 资产、无 tests/fixture/fake producer 的校验完全复用生产逻辑。
- `verification-only` 标记只放宽「HEAD 是 `origin/main` 祖先」一条，不授予任何额外能力；缺少该标记的非 `main` 安装仍被阻断。
- 验证安装仍在 `~/.agentic-ops` 之外；`install.sh`、`update.sh`、`rollback.sh` 通过 `agentic_reject_verification_mode` 拒绝把验证目录当生产目录维护。
- 验证安装产出的研发员不额外限制真实 Jira / GitHub 写操作：真实写操作继续受既有策略门禁、能力目录和人工确认约束，与生产安装行为一致。验证安装的「验证」只关于安装来源与运行时身份，不构成任务级沙箱。

## 5. 失败码

- `source_branch_not_found`：远端不存在指定分支（远程模式）或本地 worktree 不存在该分支（本地模式）。
- `verification_home_forbidden`：验证安装目标为 `~/.agentic-ops`。
- `verification_branch_unreachable`：验证安装的 HEAD 不可达于任一 `origin/*` 远端引用。
- `install_ref_integrity_invalid`：验证安装缺少或漂移 `.local/current-ref`。
- `verification_only_install_forbidden`：生产维护命令（install/update/rollback）被用于带标记的验证目录。

## 6. 组件变更

### 6.1 `developer/bootstrap/install-verify-branch.sh`

- 新增 `REPO_URL="git@github.com:tapstate/agentic-ops.git"`。
- `--source-worktree` 从必填默认改为可选：未提供时进入远程模式，`--source-branch` 指向远端分支（默认 `develop`）；提供时进入本地模式，仅验证安装流程。
- 标准输入启动时不依赖本地 `SCRIPT_DIR`；先从参数只读解析 `--source-branch`，校验 ref 格式后通过 `gh api` 获取同分支公共库。公共库读取失败时输出稳定错误并停止，不能 `eval` GitHub 错误响应。
- 远程模式顺序：`agentic_require_unrewritten_url "$REPO_URL"` → `git ls-remote --heads "$REPO_URL" "refs/heads/$SOURCE_BRANCH"` 校验存在 → `git clone --no-checkout --filter=blob:none --single-branch --branch "$SOURCE_BRANCH" "$REPO_URL" "$INSTALL_HOME"` → `git remote set-url origin "$REPO_URL"` → `git checkout "$SOURCE_BRANCH"`。
- 标记 `.agentic-ops/verification-only` 记录 `source`（`remote` / `local`）、`source_branch` 与时间；`source` 用于区分模式。

### 6.2 `developer/bootstrap/lib/common.sh`

- `agentic_sync_runtime_for_verification` 在安装 `bin/ao-work` 后写入 `.local/current-ref = HEAD`（复用 `agentic_write_refs`），使运行时 ref 一致性校验可通过。

### 6.3 `developer/runtime/src/ao_work/installation.py`

- 新增 `_is_verification_install(root)`：检测 `.agentic-ops/verification-only` 是普通文件且非符号链接。
- `validate_install_root()` 在共享/分发校验后分支：验证安装走 `_validate_verification_checkout_integrity`，生产安装走 `_validate_checkout_integrity`。
- `_validate_verification_checkout_integrity`：`.local/current-ref == HEAD`、无受管改动、受管资产存在（与生产一致），并把「HEAD 是 `origin/main` 祖先」替换为「HEAD 可达于任一 `refs/remotes/origin/*` 引用」，不可达时返回 `verification_branch_unreachable`。

## 7. 测试与验证

- 扩展 `developer/tests/bootstrap/test_install_verify_boundary.sh`：使用离线 transport 夹具模拟官方远端，覆盖远程模式可运行安装（`ao-work capability list` / `workspace inspect` 通过）、`source_branch_not_found`、`--source-worktree` 仅流程验证、`~/.agentic-ops` 禁止、无标记的非 `main` 安装仍被阻断、非官方 origin 仍被拒绝。
- `maintainer/scripts/test-resources.sh` 增加对 `install-verify-branch.sh` 与 `test_install_verify_boundary.sh` 的 `require_executable` 与相关合同断言。
- 固定完整验证保持不变（`test-python-runtime.sh`、`test-resources.sh`、`test_install_boundary.sh`、`test-release-workflow.sh`），验证安装回归随 `test_install_boundary.sh` 链路纳入发布验证。

## 8. 文档与故事

- `docs/development-engineers/agent-init.md`、`getting-started.md`、`de-001-install.md`：把「指定分支验证安装不能初始化研发员」改写为「远程模式可运行、本地模式仅流程验证」，并说明与生产 `main` 安装的边界。
- `docs/runtime/python-runtime.md` 第 13 节与 `docs/architecture/project-structure.md` 第 6 节补充验证安装身份模式。
- 新增 developer 用户故事并注册故事，覆盖「指定分支验证安装可运行、生产安装仍固定 main、标记只放宽 main 祖先」的固定验收。
