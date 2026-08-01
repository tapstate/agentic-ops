# AgenticOps 源码发布工作流设计

## 1. 目标与范围

本文定义 `tapstate/agentic-ops` 源头仓库进入正式研发后的分支、版本、验证、发布和紧急修复流程。

本文中的“发布”是指代码完成验证后，通过拉取请求合入稳定分支 `main`，并按版本规则管理 Git tag。它不表示创建 GitHub Release，也不改变安装后 AIAgent 执行业务 Jira 任务的运行规范。

## 2. 分支职责

- `main` 是稳定主分支，也是 GitHub 默认分支和安装脚本读取的分支。
- `develop` 是日常开发分支。正常发布只能通过 `develop → main` 拉取请求完成。
- `<user>/<jira-id>/fix-main` 是紧急修复分支，只能从最新 `origin/main` 创建，只能通过拉取请求合回 `main`。
- `main` 禁止直接提交、直接推送、强制推送和删除。
- 所有合入 `main` 的拉取请求使用 Merge commit，不要求 GitHub CI 或代码审查批准。
- Hotfix 合入 `main` 后，由研发工程师人工决定如何把修复同步回 `develop`；脚本只提示，不自动回同步。

## 3. 版本与 Tag

### 3.1 编译版本

保留现有版本格式和计算规则：

```text
TYPE-vX.Y.COMMIT_NUM-COMMIT
```

例如：

```text
INS-v0.2.17-a68372d
```

`TYPE` 继续表示源码态、开发态或安装产物，不使用分支名替换，也不增加独立分支字段。`COMMIT_NUM` 继续按现有全部可达提交数量计算；Merge commit 造成的序号跳跃属于允许行为，末尾 commit 标识负责保证完整版本可追溯。

### 3.2 Git tag

- Git tag 只允许二段式 `vX.Y`，例如 `v0.3`。
- `vX.Y` 是该版本线的编译基线，只创建一次，不代表该版本线中的每次交付。
- 正常发布通过 `release.sh prepare --version vX.Y` 创建本地 annotated tag。
- `prepare` 后的构建和必要修正继续以该 tag 为基线，通过 `COMMIT_NUM` 和 commit 标识形成具体编译版本。
- 本地 tag 只有在对应代码已通过拉取请求合入 `main` 后才推送到远端。
- 远端 tag 不移动、不覆盖、不删除、不强制推送。
- Hotfix 复用 `main` 历史中最近的 `vX.Y`，不创建或移动 tag。

## 4. 用户入口与组件边界

用户入口只保留两个脚本：

```text
scripts/release.sh
scripts/hotfix.sh
```

内部公共实现放在：

```text
scripts/lib/release-common.sh
scripts/lib/development-workflow.sh
```

- `release.sh` 负责 `develop` 正常发布的 `prepare` 和 `publish`。
- `hotfix.sh` 负责紧急修复的 `create`、`prepare` 和 `publish`。
- `release-common.sh` 负责参数、仓库、版本、验证、确认、拉取请求、等待和审计公共逻辑。
- `development-workflow.sh` 负责本地 Hooks、`develop` 和 GitHub `main` 保护的检查与幂等配置。
- 源头仓库维护脚本可以编排 `git`、`gh` 和固定验证命令。该例外只适用于 AgenticOps 源头仓库维护，不允许把安装后 AIAgent 的 Jira、GitHub、Git、策略或证据业务逻辑迁回 Shell。

版本化本地 Hooks：

```text
.githooks/pre-commit
.githooks/pre-push
```

- `pre-commit` 阻止在 `main` 直接提交。
- `pre-push` 阻止直接向远端 `main` 推送。

## 5. 正常发布流程

### 5.1 准备版本

命令：

```bash
scripts/release.sh prepare --version v0.3
```

顺序：

1. 检查目标仓库、`git`、`gh` 和 GitHub 登录状态。
2. 检查 GitHub 默认分支为 `main`。
3. 检查本地 Hooks、远端 `develop` 和 `main` PR-only 保护；缺失时展示配置动作并逐项取得确认后修复。
4. 要求当前分支为 `develop`、工作区干净且本地没有落后或分叉。
5. 校验版本参数符合 `^v[0-9]+\.[0-9]+$`。
6. 校验远端不存在同名 tag；同名本地 tag 只有在指向当前版本线基线时才允许复用。
7. 在当前 HEAD 创建 annotated tag，暂不推送。
8. 构建四个平台二进制并更新 `install-resources/checksums.txt`。
9. 输出生成文件清单，停止在研发工程师审查和提交点。

`prepare` 不暂存、不提交、不推送代码。构建失败时保留本地 tag 和生成文件，修复后可以在同一版本线中重复执行。

### 5.2 发布版本

命令：

```bash
scripts/release.sh publish --version v0.3
```

顺序：

1. 重复执行仓库和研发流程门禁。
2. 要求当前分支为 `develop`、工作区干净且全部变更已提交。
3. fetch 远端；本地 `develop` 可以领先 `origin/develop`，但不得落后或分叉。
4. 校验本地 tag 存在且是当前 HEAD 的祖先；远端同名 tag 只允许在其目标与本地完全一致的恢复执行场景中复用。
5. 在临时 Git worktree 中执行完整验证。
6. 展示版本、目标仓库、源分支、目标分支、待推送提交和验证结果。
7. 交互取得最终人工确认；非交互环境必须显式传入 `--confirm-release`。
8. 推送 `develop`。
9. 创建或复用唯一的开放 `develop → main` 拉取请求。
10. 使用 Merge commit 并启用 Auto-merge。
11. 等待拉取请求实际合并。
12. fetch `origin/main`，确认发布时的 `develop` HEAD 已包含在 `origin/main`。
13. 确认本地 tag 目标已包含在 `origin/main` 后推送 tag。
14. 写入结构化发布审计并输出发布结果。

## 6. Hotfix 流程

### 6.1 创建修复分支

命令：

```bash
scripts/hotfix.sh create --jira-id AO-123
```

顺序：

1. 要求当前仓库工作区干净。
2. fetch `origin/main`。
3. 从 Git 配置读取用户名；无法读取时要求显式提供。
4. 校验 Jira ID 格式和分支名安全性。
5. 确认本地和远端不存在同名分支。
6. 从最新 `origin/main` 创建 `<user>/<jira-id>/fix-main`。

### 6.2 准备修复产物

命令：

```bash
scripts/hotfix.sh prepare
```

顺序：

1. 校验当前分支符合 `<user>/<jira-id>/fix-main`。
2. 校验分支以 `origin/main` 为基础且工作区干净。
3. 自动解析 `main` 历史中最近的二段式 `vX.Y`。
4. 构建四平台二进制并更新 checksum。
5. 输出生成文件清单，停止在研发工程师审查和提交点。

该命令不创建、移动或推送 tag，也不提交生成产物。

### 6.3 发布修复

命令：

```bash
scripts/hotfix.sh publish
```

顺序：

1. 校验修复分支、Jira ID、工作区和远端同步状态。
2. 自动解析并校验版本线基线。
3. 在临时 Git worktree 中执行完整验证。
4. 展示修复范围、目标仓库、验证结果和最终人工确认。
5. 推送修复分支。
6. 创建或复用修复分支到 `main` 的开放拉取请求。
7. 使用 Merge commit 并启用 Auto-merge。
8. 等待并验证 `origin/main` 包含修复分支 HEAD。
9. 写入结构化审计并提示研发工程师人工把修复同步回 `develop`。

## 7. 研发流程配置门禁

发布脚本在任何测试、推送或拉取请求操作前检查：

- GitHub CLI 认证可用：先执行 `gh auth status -h github.com`；状态检查失败时回退执行 `gh api user`，只有两项都失败才阻断，且不输出令牌或认证响应正文。
- `core.hooksPath` 指向 `.githooks`。
- 远端 `develop` 存在。
- GitHub 默认分支是 `main`。
- `main` 的仓库规则禁止直接推送、强推和删除，并要求通过拉取请求合入。
- `main` 不配置必需 GitHub CI 和必需 Review。

发现缺失或漂移时：

1. 展示当前值、期望值和准备执行的动作。
2. 逐项请求用户确认。
3. 确认后调用幂等配置逻辑修复。
4. 用户拒绝、权限不足或修复后复检失败时立即停止。

非交互环境默认只检查并失败；只有显式传入 `--configure-workflow` 时才允许执行配置。

## 8. 完整验证

`publish` 在临时 Git worktree 中固定执行：

```bash
go test ./...
bash scripts/test-resources.sh
bash scripts/test-build.sh
bash scripts/test-install.sh
bash tests/e2e/ao-profile-flow.sh
bash tests/e2e/local-fake-flow.sh
bash tests/e2e/local-install-flow.sh
bash tests/e2e/problem-resolution-flow.sh
```

验证命令不可由普通参数替换或跳过。任一命令失败时，不执行推送、创建拉取请求、Auto-merge 或 tag 推送。

## 9. 幂等、失败与恢复

- 本地分支落后或与远端分叉时停止，不自动 pull、merge 或 rebase。
- 已存在源分支和目标分支匹配的开放拉取请求时复用。
- 匹配的拉取请求已合并时直接进入远端包含关系验证。
- 推送后失败不回滚远端分支，不自动关闭拉取请求。
- 不删除分支，不移动或强制更新远端 tag。
- 重复执行从当前可验证阶段继续，不重复创建拉取请求或重复推送相同 tag。
- 每个失败返回稳定错误码、失败阶段和可执行人工恢复动作。

## 10. 发布证据

拉取请求描述记录：

- 版本基线。
- 源分支和目标分支。
- 待合并 HEAD。
- 固定验证命令及结论。
- 本地验证完成时间。

脚本在 `.local/release-runs/` 写入结构化 JSON，至少包含：

- 操作模式和阶段。
- Jira ID（Hotfix）。
- 版本基线。
- PR 编号和地址。
- Merge commit。
- Tag 及其目标（正常发布）。
- 验证时间和最终状态。

GitHub PR 和 Merge commit 是发布事实源，本地 JSON 是执行审计记录。输出不得包含 token、完整环境变量或原始敏感日志。

## 11. 正式规则状态

- 原临时开发限制已移除，不再作为当前执行规则。
- 分支职责、质量门禁、版本、发布、Hotfix、人工确认和审计要求已迁入永久项目规则。
- 真实 Git、GitHub 和 Jira 操作继续受永久策略和明确人工确认约束，不再一律禁止。
- 发布检查清单、当前架构、版本设计、项目维护者故事、维护者上手和 README 索引必须与本流程保持一致。
- `main` 直提规则已由 `develop` 日常开发和 PR-only 发布流程替代。

## 12. 测试要求

新增：

```text
scripts/test-release-workflow.sh
```

测试使用临时 Git 仓库和 fake `gh`，不得真实修改 GitHub 仓库设置、推送分支、创建拉取请求或推送 tag。至少覆盖：

- `main` 提交和推送被 Hooks 阻止，`develop` 正常工作。
- 缺失研发流程配置时的确认、拒绝、非交互失败和幂等修复。
- 正常发布 `prepare` 和 `publish`。
- Hotfix `create`、`prepare` 和 `publish`。
- Jira ID 和修复分支命名校验。
- tag 格式、冲突、祖先关系、远端不可覆盖和 Hotfix 不创建 tag。
- 拉取请求创建、复用、已合并恢复和等待超时。
- 任一验证失败时没有远端写入。
- Merge commit 后 `origin/main` 包含关系验证。
- 审计 JSON 不包含敏感信息。

完整回归继续执行第 8 节列出的全部命令。
