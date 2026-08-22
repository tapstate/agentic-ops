# 项目维护者上手

本文面向第一次接触 AgenticOps 源头仓库的项目维护者。项目维护者维护的是 AgenticOps 项目本身，不等同于安装后 AIAgent 执行业务 Jira 任务。

## 先理解边界

开始工作前先读：

1. [README](../../README.md)：了解 AgenticOps 定位、核心模型和角色入口。
2. [项目结构](../architecture/project-structure.md)：确认两个工作面和 AI 入口硬隔离。
3. [项目规则](../project-rules.md)：确认文档、运行资产、提交和安全边界。
4. [配置规范](../configuration-standards.md)：确认配置分类、密钥落点、统一读取入口和变更审查要求。
5. [源码发布流程](../architecture/source-release-workflow-design.md)：确认 `develop`、`main`、Tag 和 Hotfix 规则。
6. [发布检查清单](../review-checklist.md)：确认完整验证和人工门禁。
7. [历史实现记录（冻结）](../architecture/agenticops-current-design.md)：只在追溯已删除实现时阅读，不作为现役操作说明。

## 初始化本地项目

从源头仓库根目录开始：

```sh
git status --short
uv sync --locked --project maintainer
./maintainer/bin/ao-maint --help
bash maintainer/scripts/test-python-runtime.sh
```

维护入口和固定测试只使用 `maintainer/.venv`，不会回退根目录或系统 Python。

根 `AGENTS.md` 是固定 maintainer AI 入口，并继续加载 `maintainer/AGENTS.md`。不要在源头仓库调用 `ao-work`，不要读取业务项目凭证或把业务仓库规则带入维护 worktree。

## 初始化 maintainer Jira 配置

维护者需要与 Jira AO 任务交互（读取任务、回写进度评论和 Worklog）时，先初始化维护工作面的 Jira 配置：

```sh
bash maintainer/bin/init-maintainer-config.sh
```

脚本会校验源头仓库身份（`.agentic-ops-source` 标记和固定官方 origin）、准备 `maintainer/.local/` 状态目录、校验 Connection 定义（`maintainer/standards/connections/`），并引导隐藏输入维护者 Jira 凭证写入 `maintainer/.local/.env`（权限 `0600`，已 gitignore，随 worktree 隔离）。之后即可使用 `ao-maint jira` 命令组：

```sh
./maintainer/bin/ao-maint jira auth show
./maintainer/bin/ao-maint jira auth verify
./maintainer/bin/ao-maint jira inspect --issue-key AO-11
```

`ao-maint jira` 与 developer 面的 `ao-work jira` 命令同名但独立实现，不读取业务项目工作空间凭证；建卡、评论、Worklog 和状态流转沿用 `plan -> apply -> readback` 协议并显式要求 `user-confirmation:<KEY>:<plan_id>` 确认引用。创建 Jira 子任务时必须使用 `jira create plan --issuetype 子任务 --parent <PARENT-KEY>`，Runtime 会回读父任务并校验项目、类型和写前事实；禁止通过通用 `--field` 猜测 `parent` 结构。

`ao-maint` 的 Jira 任务操作硬限制为 AO 项目。非 AO 的 issue、project、父任务、计划文件或远端回读会在读取凭证和联网前返回 `maintainer_jira_project_scope_mismatch`；直接调用 Service 也会重复校验。`jira auth show|set|remove|verify` 只管理 maintainer 本地凭证并输出 `allowed_project_keys: ["AO"]`，认证成功不表示可以处理 TAP 等业务项目；这类任务必须在对应 developer 工作空间使用 `ao-work`。

处理 AO 维护任务时，对外只使用接管入口：

```sh
./maintainer/bin/ao-maint takeover AO-45
```

Runtime 自动区分新接管、恢复、接纳存量和阻断。新接管会按固定顺序完成开始评论、流转“正在进行”和逐项回读；恢复或接纳存量会明文提示并留下审计。设计方案通过同一入口的 `--design-file` 绑定摘要，人工确认后用 `--confirm <DIGEST>` 建立工作项级连续执行授权。授权内的正常实现、验证和必要 Jira 回写连续推进；设计审查、提交前精确内容确认和风险决策是固定暂停点。根仓库及任何 AgenticOps worktree 都从版本化 maintainer 入口和 Rule 继承该规范，不依赖聊天上下文。

如果只是修改文档，至少检查工作区状态、目标文档链接和术语一致性。修改运行代码时，必须执行对应 maintainer/developer 单元、边界、安装或端到端验证。

## 开始推进工作

推荐顺序：

1. 从 [文档索引](../README.md) 找到对应设计、规则或流程入口。
2. 从对应 Jira 工作项确认当前阶段、已完成项、阻塞和剩余工作。
3. 在对应工作面修改源头文档、运行资产或 Python Runtime；跨工作面变更必须分别验证。
4. 执行验证命令。
5. 用聚焦提交记录一个逻辑变更。

日常开发在 `develop` 分支进行。发布前先执行 `maintainer/scripts/release.sh prepare --version vX.Y`，审查准备结果并提交待发布变更，再执行 `maintainer/scripts/release.sh publish --version vX.Y`。`main` 只能通过 PR 的 Merge commit 合入，不得直接提交或推送。紧急修复统一使用 `maintainer/scripts/hotfix.sh`。

GitHub Free 私有仓库无法启用所需 Ruleset 与 Auto-merge 时，只能由发布者显式增加 `--allow-soft-gate`，脚本不会自动降级：

```sh
maintainer/scripts/release.sh prepare --version v0.3 --allow-soft-gate
maintainer/scripts/release.sh publish --version v0.3 --allow-soft-gate --confirm-release
```

普通发布会在 `prepare` 时从已验证的 `develop` HEAD 创建固定本地 `release/vX.Y` 分支。首次 `publish` 推送该固定分支并创建 PR 后，每 5 秒查询一次 PR 状态，最多等待 30 分钟；研发工程师仍必须在 GitHub 页面选择 Merge commit 人工合并，检测到合并后脚本自动重新验证固定 HEAD，将 `develop` 快进到已验证的 `main`，并在实际 Merge commit 创建和推送 Tag。快进条件不成立时失败关闭，不会普通 merge、rebase 或改写 `develop` 历史。若需要立即返回，可加 `--no-wait-for-merge`；该模式返回状态码 `2`，人工合并后重新执行同一条 `publish` 命令。发布分支保留，不自动删除。

Hotfix 从包含当前 `main` 的最新 `develop` 创建修复分支，使用相同的 `--allow-soft-gate`、状态码 `2`、人工 Merge commit 和二次验证规则；合入后自动把远端和本地 `develop` 快进到 `main` 并切回 `develop`。它复用最近的 `vX.Y`，不创建、移动或推送 Tag；快进不成立时失败关闭。软门禁不能从服务器端阻止其他账号直接推送 `main`，因此命令输出、PR 和审计中的 `protection_mode=soft` 风险提示不得忽略。

## 不要混用的资料

- 项目维护者规则只约束 `tapstate/agentic-ops` 源头仓库维护。
- AIAgent 执行业务 Jira 任务时读取的是 AI 员工手册、操作契约、工作流配置、策略、运行手册和模板。
- 具体业务项目的 AI 工作空间、任务执行日志、Jira 原文和敏感上下文不得写入 AgenticOps 源头仓库。
