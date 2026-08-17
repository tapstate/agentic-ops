# AgenticOps Python Runtime

## 1. 目的

本文定义 AgenticOps Python Runtime 的稳定运行边界。Python Runtime 是 Skill 调用的结构化操作层；Shell Bootstrap 只负责安装、更新和启动。

## 2. 运行入口

Python Runtime 按工作面提供两个入口：

```sh
./maintainer/bin/ao-maint <operation> [args]
ao-work <operation> [args]
```

`ao-maint` 只加载维护包，`ao-work` 只加载研发任务包：

```text
ao-maint -> python -m ao_maint
ao-work  -> ~/.agentic-ops/developer/.venv/bin/python -m ao_work
```

两个解析器不提供 `--mode`，两个 Python 包不得互相导入。安装实现可以使用等价的 `uv run --locked`，但业务项目的 Python 环境不得影响 Runtime。

## 3. 组件边界

| 组件 | 职责 |
| --- | --- |
| Skill | 选择流程、组织操作、解释结果、触发 AI 判断或人工门禁 |
| maintainer Python Runtime | 项目故事门禁、源头维护检查和维护结构化输出 |
| developer Python Runtime | 业务契约、配置、状态、API、证据、恢复和结构化输出 |
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

当前 Jira Comment 和 Worklog 公开独立 `readback`；Jira Description 在 `apply` 内完成写后回读，不公开独立 readback 子命令。能力目录必须精确反映这种差异，不能用目标协议推断 CLI 语法。

## 6. 本地状态

- 人工维护配置和标准使用 YAML / Markdown。
- Runtime 管理的任务状态使用 JSON / NDJSON。
- `task.json`、`progress.json` 和 `sync.json` 保存当前快照；`decisions.ndjson` 和 `journal.ndjson` 只追加历史。
- 每个状态文件包含 `schema_version`、`issue_key`、`agentic_run_id`、`updated_at` 和内容版本。
- 更新在同目录写临时文件，flush / fsync 后原子替换。
- journal 只追加，并记录操作、状态、失败码、幂等键和安全摘要。
- 同一 Jira 任务使用任务级文件锁；锁超时返回稳定错误码，不自动破坏未知持有者的锁。

## 7. 配置解析

developer 工作面的 effective 配置来源顺序：

```text
项目工作空间 overlay
> ~/.agentic-ops/user/
> developer/standards/projects/<project>/
> developer/standards/company/
> Runtime 默认值
```

该顺序只用于配置字段合并；规则冲突继续按 `项目规则 > AIAgent 规则 > 公司规则 > 个人规则` 处理。映射缺失时返回 `capability_gap` 或明确配置错误，不猜测 Jira 字段、状态和仓库。

Runtime 不在同一进程中区分 mode。`ao_maint` 与 `ao_work` 分别由目录、AI 入口、命令、Python 包、Git remote、仓库根和项目工作空间标记验证；不一致时返回 `workplane_mismatch`。聊天指令、环境变量和 `--mode` 不能改变工作面。

Jira 配置分为研发员账户、Connection、Project Profile 和 Project AI Workspace。一个业务项目工作空间代表一名研发员并只维护一个 Jira 账户；`~/.agentic-ops` 共享安装没有人员身份，一台电脑可以维护多个隔离的研发员工作空间。项目工作空间通过 Project Profile 选择 Connection，旧工作空间中的显式 `connection_id` 只作一致性校验。任务身份仍包含 `connection_id`、`jira_issue_id`、`issue_key` 和 `project_key`；站点、Profile 或 Issue 事实不一致时返回 `jira_workspace_mismatch`。

## 8. Python 与依赖

- Python 3.12 由 `.python-version` 固定。
- 两个工作面的依赖分别由 `maintainer/pyproject.toml`、`developer/pyproject.toml` 声明，并由各自目录的 `uv.lock` 锁定；根目录不再提供混合 Python 项目。
- 安装和 CI 使用 `uv sync --locked --project <workplane>`。
- 单元测试、类型检查、格式检查和安全扫描命令写入对应工作面的 `pyproject.toml` 或固定脚本，发布流程不得临时替换。
- 首选标准库，第三方库必须解决明确问题并提供测试。

## 9. 安全边界

Python Runtime 提供正常路径的门禁和审计，但不是唯一硬安全边界：

- Jira 权限和 token scope 控制 Jira 写入上限。
- Git hooks、GitHub Ruleset 和分支权限保护 `develop`、`main` 等分支。
- 合并、发布和范围变化继续需要人工责任人确认。
- Skill 和 AI 不得绕过 Runtime 已提供的受控操作直接调用底层写接口。
- Superpowers 只提供可选分析、规划、调试和审查辅助；其目录不是任务状态或审计事实源，插件缺失不得影响主流程。

## 10. Jira 字段与 Worklog

- Custom Field 的目标设计通过项目 Profile 中的稳定 field ID 映射，状态为 `active`、`read_only`、`pending_validation`、`unsupported` 或 `deprecated`；当前自动写入仍是 `capability_gap`，不得把设计映射描述为已实现命令。
- 普通映射缺失属于配置修复；涉及 Jira 元数据、字段语义、Context、Screen、权限、自动化或跨项目影响时必须进入专题治理。
- 未明确声明写入能力时默认只读，不允许按字段名称模糊匹配。
- Worklog 记录中文标题、实际处理区间、累计耗时和本次耗时包含的工作；等待人工、等待外部系统、无人处理暂停和 CI 排队不计入。
- Jira Comment 和 Worklog 执行 `plan -> apply -> readback`；Jira Description 执行 `plan -> apply`，由 apply 内部回读。

## 11. 验收

- 没有 Go 环境时 Runtime 可以安装和执行。
- 同一输入产生稳定 JSON、退出码和失败码。
- 状态写入中断不会留下被误认为成功的半文件。
- Jira / GitHub 写入结果不明确时先回读，不重复副作用。
- `capability_gap` 能被 Skill 识别，并生成任务级反馈而非静默继续。
- Python 源码或标准资产更新后不需要构建项目自有平台二进制。
- 两个工作面不会交叉加载规则、授权、配置或状态，多 Jira Connection 的任务身份和凭证保持隔离。
- Worklog 可以解释每段真实耗时所包含的处理，并能安全回读避免重复登记。

## 12. 授权入口

业务外部系统授权统一通过 `ao-work auth` 管理。Jira 当前支持 `list`、`show`、`set`、`remove` 和 `verify`；常规 `show`、`set`、`verify` 不需要 Connection 或 scope 参数，用户不需要手工猜测环境变量名或编辑 `.env`。`ao-maint` 不读取业务工作空间凭证，也不提供该授权入口。

授权入口只返回配置状态、脱敏身份和来源，不返回 token。凭证文件使用锁、原子替换和 `0600` 权限；真实 Jira 操作前必须验证 Connection、当前身份和项目工作空间绑定。详细操作见 [AgenticOps 授权管理](authorization.md)。

## 12.1 developer 能力目录

`developer/standards/capabilities/operations.yaml` 是 developer Runtime 可调用性的机器事实源，`ao-work capability list|show` 只读输出稳定 JSON。目录覆盖每个 Operation Contract，并把真实 parser 中没有等价实现的旧契约标记为 `capability_gap`；契约存在不等于命令存在。

`status=implemented` 的公开能力必须声明实际 parser 命令路径；`status=capability_gap` 不得声明命令，并必须提供中文 `next_action`。`task init|inspect` 与 `report write` 属于 `visibility=internal` 的 Runtime 状态原语，只允许版本化 Skill 编排，不能对外解释为 Jira 接管、完成审计或 Jira 回写。

## 13. 业务项目工作空间初始化

人用常规入口为 Python Runtime 的 `ao-work workspace init`。交互模式从 Project Profile 安全默认值开始，统一确认 `agent_id`、Jira 空间、脱敏授权账户、默认仓库和源码目录。`agent_id` 默认由纯小写主机名规范化得到，最终必须匹配 `^[0-9A-Za-z_-]+$`。站点、Project、状态/字段映射和默认仓库不按任务重复询问；任务事实来自 Jira，run/digest/time 由 Runtime 生成，用户只审查 AI 提议和高风险授权。

确认后 Runtime 先对候选配置执行无副作用预检，再准备源码和原子写入工作空间文件；`.agentic-ops/agent.json` 作为初始化完成标记最后写入。Jira 身份、目标 Project 访问、Git 远端访问或本机 `agent_id` 冲突任一检查失败时，不得进入任务执行。

初始化生成的业务工作空间 `AGENTS.md` 固定进入 developer 工作面，不得引用根 `AGENTS.md` 或 `maintainer/`。共享安装的 `user/workspace-index.json` 只是可重建冲突索引，不保存凭证、不授权、不代表研发员。Jira 凭证仍只在业务项目工作空间 `.agentic-ops/.env` 中维护。

指定分支验证安装（`developer/bootstrap/install-verify-branch.sh` 远程模式）的 `ao-work` 复用同一套安装身份校验：origin 必须是 `tapstate/agentic-ops`、sparse 精确集与 shared/developer 分发白名单不变，仅把「HEAD 是 `origin/main` 祖先」放宽为「HEAD 可达于任一 `origin/*` 远端分支或 tag」；该放宽只在 `.agentic-ops/verification-only` 标记存在时生效。生产安装 `~/.agentic-ops` 仍固定 `main`，不接受分支覆盖。

## 14. 项目故事质量门禁

maintainer 工作面通过 `ao-maint story impact|approve|verify` 守护项目维护故事和研发工程师故事。影响检测基于 Git 内容指纹和机器注册表；确认与固定验收必须匹配同一 `impact_id`。故事受影响、故事修订、验收失败或映射缺失时，pre-commit 停止提交。`ao-work` 不提供故事门禁子命令。详细规则见 [项目故事质量门禁](story-quality-gate.md)。
