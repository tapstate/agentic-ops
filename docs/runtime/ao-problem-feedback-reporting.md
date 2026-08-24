# AO问题反馈上报规范

## 1. 目的与所有权

本规范定义研发工程师发现 AgenticOps 问题后，交给项目维护者的最小完整上报合同。目标不是堆积原始日志，而是输出足以让维护者分类、复现、选择修复载体、实现、回归和验收的脱敏事实。

工作面边界固定如下：

- `developer` 工作面只发现问题、校正当前业务任务并形成脱敏上报，不修改 AgenticOps 源头资源。
- `maintainer` 是 AgenticOps 源头仓库及 `developer/**` 被维护资源的唯一维护入口；developer 问题也必须在 maintainer 工作面修复。
- 维护者可以在 AgenticOps 源头仓库修改 `developer/runtime/`、`developer/skills/`、`developer/rules/`、`developer/standards/`、`developer/bootstrap/` 和 `developer/tests/`，但不得进入业务项目工作空间自修、继承业务凭证，或直接修改稳定安装目录 `~/.agentic-ops`。
- developer 行为只能用显式、脱敏的 fixture 和黑盒入口复现；修复发布后再由 developer Bootstrap 更新并在原业务场景复验。

## 2. 输出状态

每份反馈声明：

```text
report_schema: ao_problem_feedback/v1
repair_readiness: ready | needs_information
```

每个信息项只能使用以下一种状态：

- **已提供**：给出结构化事实和事实来源。
- **不适用**：给出不适用原因。
- **未获取**：给出缺失原因、应查询的事实源、最小补齐动作和是否阻断修复。

不得静默省略字段，不得用无解释的“未知”占位，也不得猜测 Jira、仓库、版本、分支、PR、CI、Artifact、日志或错误根因。

## 3. 必需信息清单

| 信息组 | 必需内容 | 修复就绪要求 |
| --- | --- | --- |
| 来源绑定 | 原业务 Jira key；无业务任务时说明原因；Project Profile；工作空间安全标识；`agentic_run_id` | 必须明确来源或无任务原因 |
| 版本与环境 | AgenticOps 版本、Git ref 或构建标识；安装来源；OS、Shell、Python 等与问题相关的环境摘要 | 必须有可追溯版本；环境按问题相关性提供 |
| 问题上下文 | 触发命令、操作或 capability；发生阶段；前置状态；输入类型 | 必须能定位到操作和阶段 |
| 实际行为 | 用户可见现象；结构化错误码；脱敏消息；时间、频率；稳定复现或偶发 | 必须提供 |
| 期望行为 | 正确输出、状态、副作用和应保留证据 | 必须提供 |
| 最小复现 | 前置条件、最短步骤、最小输入或脱敏 fixture；每步实际结果 | 必须提供步骤或等价可验证证据 |
| 影响范围 | 受影响角色、项目、工作面、流程阶段、严重程度；是否阻断交付；数据与安全风险 | 必须提供 |
| 外部事实 | 涉及 Jira、Git、PR、CI、Workflow Run 或 Artifact 时，提供对应稳定标识、状态和 current Head；不涉及则标记不适用 | 按场景必需 |
| 证据清单 | 可移交的结构化输出、文件名或稳定引用、来源、采集时间和 SHA-256；只保留最小必要片段 | 至少有复现证据或可执行 fixture |
| 人工介入 | 已做的确认、重试、绕过、手工修复；结果；仍无法自动化的动作 | 没有时明确写无 |
| 初步判断 | 已观察事实、尚未证实的根因假设、候选修复载体及排除其它载体的依据 | 假设必须与事实分开，可标记未获取 |
| 最小回归 | 可重复输入或 fixture、精确验证命令、预期结果、失败判定和必要失败路径 | 必须可执行；暂缺时标记阻断修复 |
| 验收标准 | 修复完成的可观察条件、边界条件、原场景复验方式 | 必须提供 |
| 缺失事实 | 所有未获取项、原因、事实源、补齐动作、责任角色和阻断性 | 必须完整列出 |
| 脱敏声明 | 未包含 token、密钥、原始客户数据、完整敏感日志、敏感代码片段、隐藏目录或凭证文件 | 必须提供 |

### 3.1 情境化外部事实

- Jira：project、issue type、status、相关 field id、transition、plan/readback 标识；不得复制完整业务 Description。
- Git：repo、base/target/task branch、HEAD、merge-base 和 dirty 状态；不得猜测缺失仓库。
- GitHub：PR URL/number/current Head、required checks、失败 check、Workflow Run、Artifact 名称/ID/SHA-256/生成时间。
- 安装更新：安装 ref、目标 ref、更新或回滚阶段、失败码、是否保留工作空间状态。
- 网络：代理来源类别、目标类别、脱敏 errno 和探测阶段；不得输出代理 URL、userinfo 或原始响应。

## 4. 修复就绪门禁

满足以下条件时才可设置 `repair_readiness: ready`：

1. 来源和版本可追溯。
2. 实际行为、期望行为和影响范围明确。
3. 有最小复现步骤、等价证据或可移交 fixture。
4. 有可执行的最小回归方法和验收标准。
5. 所有情境字段均已提供、标记不适用，或以不阻断修复的未获取项列明。
6. 脱敏声明通过。

任何条件不满足时仍可在用户确认后创建 AO 缺陷，避免问题丢失，但必须设置 `repair_readiness: needs_information`，在描述开头明确当前不能独立修复，并列出最小补齐动作。维护者接管后先补齐阻断事实，不能直接修改设计或代码。

## 5. Jira 描述模板

```markdown
report_schema: ao_problem_feedback/v1
repair_readiness: ready | needs_information

## 来源绑定
## 版本与环境
## 问题上下文
## 实际行为
## 期望行为
## 最小复现
## 影响范围
## 外部事实
## 证据清单
## 人工介入
## 初步判断与候选修复载体
## 最小回归
## 验收标准
## 缺失事实与补齐动作
## 脱敏声明
```

摘要只描述一个问题，建议格式为：`[developer][组件或操作] <可观察的问题>`。同一反馈包含多个独立根因或不同修复载体时，应拆分问题并保留互相链接。

## 6. 上报与修复闭环

1. developer AI 按本规范整理完整 description，展示给研发工程师确认。
2. 通过 `ao-work jira create` 的 plan → apply → readback 在 AO 创建 `Agentic 缺陷`；结果不明确时只回读，不重复 apply。
3. 项目维护者在独立 AgenticOps 源头 worktree 通过 `ao-maint takeover <AO-KEY>` 接管。
4. maintainer 校验 `repair_readiness`、脱敏状态和事实来源；缺失阻断事实时先补资料。
5. maintainer 选择正确的 developer 源头修复载体，以 fixture 或黑盒入口复现，完成实现和对应工作面的回归。
6. 变更经故事影响分析、固定验收、代码审查和受控发布后，由 developer Bootstrap 更新并回到原业务场景复验。

`feedback_bundle` capability 实现后应生成符合本规范的结构化结果；当前仍为 `capability_gap` 时，只能从用户明确确认的安全事实人工整理，不得模拟命令成功、扫描业务工作空间或伪造反馈包。
