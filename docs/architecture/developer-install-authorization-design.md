# developer 安装级授权收口设计

## 1. 目标

developer 安装代表一名研发工程师，统一保存该研发工程师的 `agent_id`、Git 提交身份、Git SSH 远端授权模式、GitHub CLI 身份、Jira email 和 Jira API token。业务项目工作空间只绑定 Project Profile、源码位置和当前安装身份引用，不再保存、修改或迁移授权凭据。

本设计收口 D-046、D-048 已确定但尚未彻底落地的安装级身份方向，并保持以下边界：

- Shell Bootstrap 只安装 developer 资产并调用授权 Runtime，不实现授权校验或凭据写入。
- Python Runtime 负责授权输入、校验、私有文件原子写入、脱敏输出和失败码。
- token 只允许终端隐藏输入或安全标准输入，不进入命令参数、日志、JSON 输出或工作空间。
- 工作空间初始化继续负责使用安装凭据验证 Jira 当前用户与 Project 访问，但不收集或保存凭据。

## 2. 公开交互

授权公开入口收敛为一个顶层命令：

```sh
ao-work auth
ao-work auth --show
```

`ao-work auth` 交互式配置或更新安装级身份与 Jira 凭据；重复执行即为单独重新授权。非交互模式可提供 `--agent-id`、`--jira-email`、`--git-name`、`--git-email`、`--github-login`、`--execution-auth-mode`、`--confirm-replace-authorization`、`--token-stdin` 和 `--non-interactive`。token 不提供字符串参数。

`--execution-auth-mode` 必须显式选择 `global` 或 `installation`。`global` 只复用并回读机器已有 Git/SSH/`gh`，不修改全局配置；`installation` 在 `user/ssh/` 与 `user/gh/` 建立隔离授权，SSH 固定使用 `ssh.github.com:443`、安装私钥和严格主机密钥校验，不回退全局 SSH Agent。首次安装级 GitHub 授权使用 GitHub CLI 官方设备登录，不能非交互注入 GitHub token。

提交 author/committer、SSH 远端认证和 `gh api user` 是三类独立证据。尤其在 `global` 模式，`gh` 登录账户不能表述为 SSH push actor 证明。

删除以下重复或工作空间级入口：

```text
ao-work install identity ...
ao-work install auth ...
ao-work auth jira ...
```

查看只使用 `ao-work auth --show`。本次不新增多级命令，也不把 Project Profile 或 Jira 站点写入安装身份；具体项目访问仍由 `workspace init` 按 Project Profile 验证。

## 3. 安装编排

`developer/bootstrap/install.sh` 和远端可运行模式的 `install-verify-branch.sh` 在 Runtime 安装完成后调用同一 `ao-work auth`：

- 提供授权参数时，Bootstrap 只原样转交给 Runtime。
- 未提供授权参数且连接终端时，调用零参数 `ao-work auth` 进入引导。
- 未提供授权参数且没有终端时，安装成功，输出 `authorization_status=pending` 和可直接执行的 `ao-work auth` 下一步，不阻塞无交互安装。
- 提供不完整的非交互输入时由 Runtime 阻断；Bootstrap 不复制参数完整性判断。
- 本地 `--source-worktree` 验证模式不可运行，不接收授权参数；远端验证安装与正式安装行为一致。
- 首次 Bootstrap 只能使用调用者已有账户下载与 clone；安装级授权完成后，Runtime 为 managed clone 固化安装专属 `core.sshCommand`，后续更新使用该 SSH，回滚不联网。

安装成功和授权成功是两个可区分状态。授权失败不回滚已经完成的 developer-only 安装，输出必须明示安装位置、授权状态和重试入口。

## 4. 工作空间初始化

`ao-work workspace init` 删除以下输入：

- `--agent-id`
- `--jira-email`
- `--token-stdin`
- `--git-name`
- `--git-email`
- `--github-login`

初始化只接收 Project Profile、源码位置、确认和已有配置覆盖参数。Runtime 必须先完整加载当前安装的身份与凭据，再构造候选；缺失时在任何 Jira 或 Git 访问前阻断并提示 `ao-work auth`。

新初始化结果固定为 `agent.json` schema v5：

- 工作空间不保存 `agent_id`、Jira email、Jira accountId、Git/GitHub 执行身份或 token。
- `agent_id` 和执行身份只在初始化候选、工作空间索引、源码准备及任务执行时从安装目录读取。
- `agent.json` 只保存项目绑定、源码绑定和 `install_identity_ref`。
- 初始化使用安装凭据调用 Jira `currentUser` 和 Project 只读检查，验证成功后才写完成标记。

## 5. 旧工作空间处理

schema v4 及更早版本和工作空间 `.agentic-ops/.env` 不再是运行时授权来源：

- schema v4 及更早版本在读取工作空间 `.env` 或访问 Jira 前返回升级阻断。
- 人工先执行 `ao-work auth` 在安装目录重新输入授权，再显式确认 `workspace init --confirm-existing-config`。
- Runtime 不从旧 `.env` 自动复制 token，也不自动删除旧凭据文件；重新初始化成功后由引导明确提示人工清理。
- schema v5 以 `install_identity_ref` 防止误用其它研发工程师安装；模式、执行身份或安装公钥指纹漂移只能显式重新初始化，不能由普通 preflight 重绑。

## 6. 已有授权保护与恢复

Runtime 在任何写入前先只读检查全局身份摘要、安装身份、安装 SSH/`gh` 路径和 managed clone 的 `core.sshCommand`，不得读取或输出私钥、token 或完整敏感配置。

- `absent`：允许创建。
- `managed_same`：幂等回读，不重复写入。
- `managed_different`：返回脱敏 `existing`、`candidate` 和 `change_digest`；只有 `--confirm-replace-authorization <change_digest>` 精确绑定当前候选才允许更新受管身份或受管配置。
- `unmanaged_conflict`：失败关闭，普通 `ao-work auth` 不接管。

既有私钥轮换、不同安装级 `gh` 账户和自定义 `core.sshCommand` 需要独立风险决策，不由普通确认参数放行。机器已有目录或私有文件权限过宽也只提示人工处理，不静默 `chmod`。

GitHub 登录、公钥登记、本地配置和身份落盘按可恢复阶段执行，不宣称跨 GitHub 与本地文件系统原子提交。失败不得删除全局配置、远端公钥或现有安装凭证。项目构建和验证子进程始终使用无安装 SSH/`gh` 凭证环境。

## 7. 标准资产与文档

同步修订：

- DE-002 初始化工作空间故事合同和决策日志。
- developer AI 员工手册、规则提示、能力下一步和安全错误信息。
- `initialize-project-workspace`、`configure-authorization` 及引用旧授权入口的 developer Skills。
- 安装、初始化、入门、授权、Runtime 和配置说明文档。
- AO-43 Description 中的本地 developer 验证安装与工作空间初始化脚本。

新工作空间复制的 Skill 必须只介绍安装级 `ao-work auth`，不得再生成工作空间授权操作。

## 8. 验收

目标测试必须证明：

1. `ao-work auth` 可交互或非交互写入安装目录 `user/identity.yaml` 与 `user/.env`，权限为 `0600`，输出不含 token；两种授权模式和旧身份升级均失败关闭或显式选择。
2. `ao-work auth --show` 只输出脱敏身份与凭据配置状态。
3. 三组旧多级授权入口和工作空间身份/凭据参数均解析失败。
4. Bootstrap 不包含身份校验或凭据写入实现，只调用安装后的 `ao-work auth`；无终端时输出待授权状态和精确下一步。
5. `workspace init` 缺安装身份或凭据时在外部访问前阻断；成功结果固定 schema v5，工作空间不产生或修改 `.env`。
6. schema v4 及更早版本不读取旧 `.env`，只输出重新授权和显式重新初始化动作。
7. 新工作空间的 `AGENTS.md`、Skills、手册与人读初始化文档只发布安装级授权入口。
8. AO-43 本地验证脚本可以在安装阶段安全传入授权，随后以简化参数初始化 TAP 工作空间。
9. 安装级 SSH 固定走 443、禁用 Agent 回退；全局授权不被修改，已有私钥、不同 `gh` 账户、自定义仓库 SSH 配置和非受管路径不能被静默覆盖。
10. Git/`gh` 直接操作使用选定授权环境，而构建和测试子进程看不到安装凭证。

最后执行固定完整验证：

```sh
bash maintainer/scripts/test-python-runtime.sh
bash maintainer/scripts/test-resources.sh
bash developer/tests/bootstrap/test_install_boundary.sh
bash maintainer/scripts/test-release-workflow.sh
```

## 9. 风险与停止条件

- 删除旧 CLI 会使未迁移的私有脚本失败；通过现役资产扫描和解析拒绝测试确认不再发布旧入口。
- schema v4 及更早版本不自动迁移 token，旧工作空间需要人工重新输入一次，但避免跨作用域静默复制凭据。
- 安装已经完成但授权失败时形成可恢复的部分状态；输出必须明确区分，不得宣称研发工程师已就绪。
- 修改安装身份、授权模式或 SSH 公钥会让既有 schema v5 工作空间触发 `install_identity_drift`；必须逐个显式重新初始化，不能批量静默重绑。
- 若实现需要恢复工作空间凭据回退、新增授权站点映射、自动删除旧凭据或改变上述公开命令，视为范围变化并重新进入设计审查。
