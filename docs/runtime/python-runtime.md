# AgenticOps Python Runtime

## 1. 目的

本文定义 AgenticOps Python Runtime 的稳定运行边界。Python Runtime 是 Skill 调用的结构化操作层；Shell Bootstrap 只负责安装、更新和启动。

## 2. 运行入口

统一入口：

```sh
agentic-cli <operation> [args]
```

`bin/agentic-cli` 是 Shell 包装入口，最终执行仓库锁定环境中的 Python module：

```text
~/.agentic-ops/.venv/bin/python -m agentic_ops
```

安装实现可以使用等价的 `uv run --locked`，但业务项目的 Python 环境不得影响 Runtime。

## 3. 组件边界

| 组件 | 职责 |
| --- | --- |
| Skill | 选择流程、组织操作、解释结果、触发 AI 判断或人工门禁 |
| Python Runtime | 契约、配置、状态、API、证据、恢复和结构化输出 |
| Shell Bootstrap | 安装、更新、回滚、环境准备和统一启动 |
| Rule | 事实源、权限、语言、分支、授权和停止条件 |

## 4. 输入输出

- stdout 只输出一个 JSON 对象。
- stderr 输出中文诊断，不包含 token 和原始敏感响应。
- 成功退出码为 `0`。
- 已知业务阻断退出码为 `2`。
- 能力缺口退出码为 `3`，输出 `code=capability_gap`，允许 Skill 进入 AI 判断和反馈流程。
- 运行或外部系统失败退出码为 `1`。

通用结果至少包含：

```json
{
  "ok": false,
  "operation": "jira_add_comment",
  "status": "blocked",
  "code": "jira_write_result_unknown",
  "retry_safe": false,
  "message": "Jira 评论写入结果不明确",
  "required_human_action": "请先回读 Jira 评论"
}
```

`status` 固定为：

- `completed`：操作完成且需要的回读已验证。
- `blocked`：标准门禁明确要求停止。
- `capability_gap`：现有 Runtime 没有稳定处理能力，需要 AI 分析并形成反馈。
- `failed`：运行时、配置、文件或外部服务失败。

## 5. 外部写入协议

有副作用的操作统一采用：

```text
plan
-> apply
-> readback
```

- `plan` 解析目标、映射、权限、当前事实、预期变更和幂等键，不执行写入。
- `apply` 要求显式确认引用或有效任务级授权，执行一次写入。
- `readback` 使用外部事实验证结果，更新本地 `sync.json` 和 journal。
- 网络中断或响应不完整时返回 `retry_safe=false`，不得自动再次 `apply`。

## 6. 本地状态

- 人工维护配置和标准使用 YAML / Markdown。
- Runtime 管理的任务状态使用 JSON / NDJSON。
- `task.json`、`progress.json` 和 `sync.json` 保存当前快照；`decisions.ndjson` 和 `journal.ndjson` 只追加历史。
- 每个状态文件包含 `schema_version`、`issue_key`、`agentic_run_id`、`updated_at` 和内容版本。
- 更新在同目录写临时文件，flush / fsync 后原子替换。
- journal 只追加，并记录操作、状态、失败码、幂等键和安全摘要。
- 同一 Jira 任务使用任务级文件锁；锁超时返回稳定错误码，不自动破坏未知持有者的锁。

## 7. 配置解析

effective 配置来源顺序：

```text
项目工作空间 overlay
> ~/.agentic-ops/user/
> standards/projects/<project>/
> standards/company/
> Runtime 默认值
```

该顺序只用于配置字段合并；规则冲突继续按 `项目规则 > AIAgent 规则 > 公司规则 > 个人规则` 处理。映射缺失时返回 `capability_gap` 或明确配置错误，不猜测 Jira 字段、状态和仓库。

Runtime 必须区分两种运行模式：

- `source_maintenance`：AgenticOps 源头维护，加载设计红线、源头维护规则和项目目标。
- `project_execution`：业务项目任务执行，加载业务仓库规则、AI 执行规则和项目标准，不加载 AgenticOps 源头维护规则。

模式由项目工作空间配置、Git remote、仓库根目录、Profile 和操作要求共同验证；不一致时返回 `workspace_mode_mismatch`。

Jira 配置分为 Connection、Project Profile 和 Project AI Workspace。一个工作空间默认绑定一个 `connection_id`；任务身份包含 `connection_id`、`jira_issue_id`、`issue_key` 和 `project_key`。站点、Profile 或 Issue 事实不一致时返回 `jira_workspace_mismatch`。

## 8. Python 与依赖

- Python 3.12 由 `.python-version` 固定。
- 依赖由 `pyproject.toml` 声明、`uv.lock` 锁定。
- 安装和 CI 使用 `uv sync --locked`。
- 单元测试、类型检查、格式检查和安全扫描命令写入 `pyproject.toml` 或固定脚本，发布流程不得临时替换。
- 首选标准库，第三方库必须解决明确问题并提供测试。

## 9. 安全边界

Python Runtime 提供正常路径的门禁和审计，但不是唯一硬安全边界：

- Jira 权限和 token scope 控制 Jira 写入上限。
- Git hooks、GitHub Ruleset 和分支权限保护 `develop`、`main` 等分支。
- 合并、发布和范围变化继续需要人工责任人确认。
- Skill 和 AI 不得绕过 Runtime 已提供的受控操作直接调用底层写接口。
- Superpowers 只提供可选分析、规划、调试和审查辅助；其目录不是任务状态或审计事实源，插件缺失不得影响主流程。

## 10. Jira 字段与 Worklog

- Custom Field 通过项目 Profile 中的稳定 field ID 映射，状态为 `active`、`read_only`、`pending_validation`、`unsupported` 或 `deprecated`。
- 普通映射缺失属于配置修复；涉及 Jira 元数据、字段语义、Context、Screen、权限、自动化或跨项目影响时必须进入专题治理。
- 未明确声明写入能力时默认只读，不允许按字段名称模糊匹配。
- Worklog 记录中文标题、实际处理区间、累计耗时和本次耗时包含的工作；等待人工、等待外部系统、无人处理暂停和 CI 排队不计入。
- Jira Comment、Description、Worklog 及其它副作用均执行 `plan -> apply -> readback`。

## 11. 验收

- 没有 Go 环境时 Runtime 可以安装和执行。
- 同一输入产生稳定 JSON、退出码和失败码。
- 状态写入中断不会留下被误认为成功的半文件。
- Jira / GitHub 写入结果不明确时先回读，不重复副作用。
- `capability_gap` 能被 Skill 识别，并生成任务级反馈而非静默继续。
- Python 源码或标准资产更新后不需要构建项目自有平台二进制。
- 两种运行模式不会交叉加载规则，多 Jira Connection 的任务身份和凭证保持隔离。
- Worklog 可以解释每段真实耗时所包含的处理，并能安全回读避免重复登记。
