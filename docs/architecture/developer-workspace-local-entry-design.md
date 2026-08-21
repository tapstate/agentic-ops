# developer 工作空间本地 ao-work 受管入口设计

## 1. 任务绑定

- Jira：AO-55
- 父任务：AO-43
- 仓库：`tapstate/agentic-ops`
- 工作分支与目标分支：`develop`
- 审查通道：在 `develop` 形成未推送本地提交后，按提交编号完成推送前代码审查。

## 2. 目标

业务工作空间不依赖全局 `PATH`，而是绑定初始化时确认的 developer 安装，并提供受管的工作空间本地 `ao-work` 入口。Codex 收到自然语言中的 `ao-work <args>` 时，必须使用该工作空间入口执行，不得从进程 `PATH`、`~/.agentic-ops` 或其它安装目录猜测入口。

该能力解决同一机器存在多个 developer 安装时，未设置全局 `PATH` 的业务工作空间无法调用 `ao-work`、或错误调用其它研发员安装的问题。

## 3. 当前事实与根因

1. 安装目录的 `bin/ao-work` 启动器可以由自身路径推导 developer 安装根，并把该根传递给 Python Runtime；该入口本身不需要全局 `PATH`。
2. `workspace init` 只在 `agent.json` schema v4 中保存 `install_identity_ref`，用于运行时身份绑定；它不保存可调用的安装入口。
3. 业务工作空间生成的 `AGENTS.md` 只声明命令名为 `ao-work`。当 Codex 的终端环境没有把对应安装目录放入 `PATH` 时，裸命令无法被 shell 解析。
4. 通过全局 `PATH`、扫描 `~/.agentic-ops` 或搜索多个安装目录恢复命令，会破坏“一工作空间绑定一名研发员安装”的边界，并可能选错安装身份。

## 4. 方案概览

初始化成功后，业务工作空间拥有一个普通受管文件：

```text
<workspace>/.agentic-ops/bin/ao-work
```

该文件是 workspace-local launcher（工作空间本地启动器），不是 symlink，不保存 Token 或身份明文。它只以初始化时验证过的 canonical absolute path 执行对应安装的：

```text
<install-root>/bin/ao-work
```

`AGENTS.md` 和 `CLAUDE.md` 的受管内容明确将自然语言命令映射为：

```sh
./.agentic-ops/bin/ao-work <args>
```

因此 Codex 无需修改终端全局 `PATH`。人工终端同样使用该相对入口；若输入裸 `ao-work`，shell 在未设置 PATH 的情况下仍会按操作系统规则报找不到命令，Runtime 不承诺改变这一行为。

## 5. 数据与启动器合同

### 5.1 agent.json schema v5

新工作空间将 `agent.json` 升级为 schema v5，在已有 schema v4 的项目绑定与 `install_identity_ref` 之外增加：

```json
{
  "schema_version": 5,
  "install_identity_ref": "install:<sha256>",
  "workspace_entry": ".agentic-ops/bin/ao-work",
  "install_entry_sha256": "<sha256>"
}
```

- `workspace_entry` 固定为工作空间内相对路径，不能由用户输入覆盖，不能是绝对路径或 symlink。
- `install_entry_sha256` 是初始化时受信 `<install-root>/bin/ao-work` 文件的 SHA-256，用于诊断安装入口漂移；它不是凭据，也不替代安装身份绑定。
- 不在工作空间 `agent.json` 写入安装根绝对路径、Jira email、Token、Git 身份或 GitHub 身份。绝对路径只存在于本地启动器正文中，且不会作为业务仓库提交内容。

### 5.2 workspace-local launcher

启动器由 Runtime 原子写入，权限为 owner executable，内容必须是固定版本化模板并满足以下行为：

1. 从自身位置解析工作空间根；拒绝 symlink、路径逃逸和非受管位置。
2. 固定执行初始化时确认的 `<install-root>/bin/ao-work`，参数原样转发。
3. 不读取环境变量以选择安装根，不修改 `PATH`，不加载工作空间或安装外的授权文件。
4. 目标不存在、不可执行或不是 developer-only managed clone 时返回稳定失败码 `workspace_install_entry_unavailable`，并要求由指导员重新初始化；不得回退到 `PATH` 或扫描其它安装。

启动器仅用于发现已绑定安装。目标 `ao-work` 启动后仍由 Python Runtime 按 `install_identity_ref`、developer-only sparse managed clone、工作空间边界和凭据隔离进行现有校验；启动器不是信任绕过。

## 6. 初始化、迁移和调用流程

```text
安装目录 bin/ao-work
  -> workspace init 验证安装与身份绑定
  -> 写入 schema v5 agent.json 与本地启动器
  -> 生成声明本地入口的 AGENTS.md / CLAUDE.md
  -> Codex 执行 ./.agentic-ops/bin/ao-work takeover TAP-12289
  -> Runtime 再校验安装身份、工作空间和 Jira 边界
```

### 6.1 新工作空间

- 指导员以已安装目录的绝对入口运行首次初始化，例如 `<install-root>/bin/ao-work workspace init`。
- `workspace init` 在写入配置前验证安装根、身份、授权、项目 Profile、工作空间边界和启动器目标。
- 初始化结果返回 `workspace_entry`，供人和 AI 查阅。

### 6.2 schema v4 旧工作空间

- schema v4 不自动推断绑定安装，也不使用当前 `PATH` 补齐。
- 所有需要 Runtime 的操作返回 `workspace_local_entry_upgrade_required`，说明旧工作空间缺少本地入口。
- 指导员从期望安装目录显式运行 `ao-work workspace init --confirm-existing-config`，完成 schema v5 迁移并重建受管入口。
- 迁移不复制、删除或读取旧工作空间凭据；安装级身份与凭据仍只在原安装目录。

### 6.3 Codex 与人工使用

- 工作空间 `AGENTS.md` 明确：自然语言 `ao-work ...` 指代工作空间本地入口，不得直接运行裸命令。
- Codex 实际执行 `./.agentic-ops/bin/ao-work <args>`。
- 人工终端使用同一相对命令；可自行做会话级 PATH 设置，但项目不要求、不会写入 shell profile，且不以此作为标准路径。

## 7. 安全与隔离边界

- 本地启动器、`.agentic-ops` 状态根和 `agent.json` 都使用现有 workspace managed path 校验；符号链接、硬链接替换、路径逃逸和可疑权限必须阻断。
- 启动器不能包含 token、email、private key、Jira URL、GitHub token 或全局安装搜索逻辑。
- 安装入口摘要漂移、安装身份引用不匹配、安装根不是 developer-only sparse managed clone、工作空间归属错误时均失败关闭。
- 业务工作空间继续只加载 developer Rule、Skill 与标准资产；本地启动器不能读取或恢复 `maintainer/` 资产。
- 不新增 `--install-root`、环境变量或 `--mode` 来切换工作面；入口绑定只能在 `workspace init` 中由已调用的安装决定。

## 8. 变更范围

修改 developer 工作面资产：

- `developer/runtime/src/ao_work/workspace_init/`：生成、校验与迁移本地启动器，升级 schema。
- `developer/runtime/src/ao_work/work_cli.py`、安装与工作空间校验模块：在非 init 操作前校验 schema v5 和本地入口合同。
- `developer/AGENTS.md`、初始化生成模板及 developer 文档：将裸 `ao-work` 的 AI 执行约束替换为本地入口约束。
- developer Runtime 与 bootstrap 测试：覆盖多安装、无 PATH、入口篡改、旧工作空间迁移及隔离边界。

不修改 maintainer Runtime，不新增全局 PATH 写入，不修改 shell profile，不把安装授权迁入工作空间。

## 9. 验收与验证

新增或修订自动测试，至少覆盖：

1. 从非默认 developer 安装初始化工作空间后，本地启动器固定指向该安装。
2. 清空或不含安装目录的 `PATH` 时，`./.agentic-ops/bin/ao-work workspace preflight` 和 `takeover` 可按绑定安装运行。
3. 同机两个安装各自初始化不同工作空间，入口不串用，身份引用不匹配时阻断。
4. 启动器、`agent.json` 或路径被 symlink/hardlink/路径逃逸篡改时失败关闭。
5. schema v4 工作空间不扫描 PATH，明确要求由目标安装执行迁移。
6. 重复 `workspace init --confirm-existing-config` 保持幂等，只更新受管入口，不复制凭据。
7. 工作空间 AGENTS.md、CLAUDE.md 和人读文档都使用 `./.agentic-ops/bin/ao-work`，不再要求全局 PATH。
8. developer-only 稀疏安装、工作面隔离、安装身份绑定和 TAP-12289 接管回归均保持通过。

固定完整验证：

```sh
bash maintainer/scripts/test-python-runtime.sh
bash maintainer/scripts/test-resources.sh
bash developer/tests/bootstrap/test_install_boundary.sh
bash maintainer/scripts/test-release-workflow.sh
```

`develop` 上形成未推送本地 commit 后，运行故事影响与固定验收，向指导员展示提交编号、变更点和风险，确认后才允许推送。

## 10. 风险与停止条件

- 如果工作空间本地启动器需要通过 PATH 搜索、扫描多个安装目录或读取全局凭据，停止实施并重新进入设计审查。
- 如果 schema 升级会让旧工作空间静默改绑到其它安装，停止实施；必须保持显式迁移和失败关闭。
- 如果安装入口摘要在正常安装更新时频繁漂移，先补充更新后重建本地入口的受管路径与测试，不能删去漂移检查。
- 如果实现需要把安装根绝对路径提交进业务仓库、将授权复制进工作空间、或修改 shell profile，视为范围扩大并重新请求人工决策。
