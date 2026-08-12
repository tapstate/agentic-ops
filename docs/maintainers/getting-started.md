# 项目维护者上手

本文面向第一次接触 AgenticOps 源头仓库的项目维护者。项目维护者维护的是 AgenticOps 项目本身，不等同于安装后 AIAgent 执行业务 Jira 任务。

## 先理解边界

开始工作前先读：

1. [README](../../README.md)：了解 AgenticOps 定位、核心模型和角色入口。
2. [当前设计](../architecture/agenticops-current-design.md)：确认长期架构和事实源边界。
3. [项目规则](../project-rules.md)：确认文档、运行资产、提交和安全边界。
4. [配置规范](../configuration-standards.md)：确认配置分类、密钥落点、统一读取入口和变更审查要求。
5. [源码发布流程](../architecture/source-release-workflow-design.md)：确认 `develop`、`main`、Tag 和 Hotfix 规则。
6. [发布检查清单](../review-checklist.md)：确认完整验证和人工门禁。
7. [项目结构](../architecture/project-structure.md)：确认目录职责。

## 初始化本地项目

从源头仓库根目录开始：

```sh
git status --short
go test ./...
bash scripts/test-install.sh
bash tests/e2e/local-fake-flow.sh
```

如果只是修改文档，至少检查工作区状态、目标文档链接和常见占位词。修改运行代码时，必须执行对应 Go 测试、脚本测试或端到端流程。

## 开始推进工作

推荐顺序：

1. 从 [文档索引](../README.md) 找到对应设计、规则或流程入口。
2. 从对应 Jira 工作项确认当前阶段、已完成项、阻塞和剩余工作。
3. 修改源头文档、运行资产或 `agentic-cli` 实现。
4. 执行验证命令。
5. 用聚焦提交记录一个逻辑变更。

日常开发在 `develop` 分支进行。发布前先执行 `scripts/release.sh prepare --version vX.Y`，审查并提交生成资源，再执行 `scripts/release.sh publish --version vX.Y`。`main` 只能通过 PR 的 Merge commit 合入，不得直接提交或推送。紧急修复统一使用 `scripts/hotfix.sh`。

GitHub Free 私有仓库无法启用所需 Ruleset 与 Auto-merge 时，只能由发布者显式增加 `--allow-soft-gate`，脚本不会自动降级：

```sh
scripts/release.sh prepare --version v0.3 --allow-soft-gate
scripts/release.sh publish --version v0.3 --allow-soft-gate --confirm-release
```

普通发布会从已验证的 `develop` HEAD 创建固定 `release/vX.Y` 分支。首次 `publish` 创建 PR 后返回状态码 `2`，表示发布尚未完成；研发工程师必须在 GitHub 页面选择 Merge commit 人工合并，再重新执行输出中的同一条 `publish` 命令。第二次执行会对固定 HEAD 重新运行全部验证，确认 `main` 保留该提交历史后才推送 Tag。发布分支保留，不自动删除。

Hotfix 使用相同的 `--allow-soft-gate`、状态码 `2`、人工 Merge commit 和二次验证规则，但复用最近的 `vX.Y`，不创建或移动 Tag。软门禁不能从服务器端阻止其他账号直接推送 `main`，因此命令输出、PR 和审计中的 `protection_mode=soft` 风险提示不得忽略。

## 不要混用的资料

- 项目维护者规则只约束 `tapstate/agentic-ops` 源头仓库维护。
- AIAgent 执行业务 Jira 任务时读取的是 AI 员工手册、操作契约、工作流配置、策略、运行手册和模板。
- 具体业务项目的 AI 工作空间、任务执行日志、Jira 原文和敏感上下文不得写入 AgenticOps 源头仓库。
