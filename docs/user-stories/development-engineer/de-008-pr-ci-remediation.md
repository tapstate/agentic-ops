# DE-008 PR CI 持续监控与失败自动修复

## 用户故事

作为使用 `development_change_v2` Project Profile 的研发工程师，我希望 AIAgent 在 PR 创建或更新后自动绑定最终 Head 和 Base、依据 GitHub PR/Base Workflow 事实判断是否需要 CI；需要时持续观察必需 CI，并在授权范围和三次预算内修复可信失败，以便无需重复提醒即可形成可恢复、可审计的开发闭环。

### 验收标准

1. v1 Profile 行为不变；只有 Profile 和 manifest 同时明确选择 v2 且 CI 配置精确一致时启用。
2. `ci_policy=detect_from_github_pr`；Runtime 从 GitHub 读取 PR Base 提交的 Workflow 树和 Blob。Base 无 Workflow 文件时输出 `not_required`，适用的已配置 Workflow 存在时输出 `required`，读取、映射或触发语义未知时人工介入。
3. 只有 `required` 才在每个 Head 首次观察建立 5 分钟启动截止时间；观察到 CI 执行后建立独立的 10 分钟完成截止时间。恢复运行不重置任一截止时间；建议观察间隔为 15 秒，任一超时都要求人工介入。
4. 必需检查必须存在且唯一；只有全部结论为 `SUCCESS` 才通过，缺失、重复、`NEUTRAL`、`SKIPPED` 和未知状态均不能通过。
5. 失败必须唯一绑定当前 Head 的 Workflow Run 和未过期 Artifact；下载摘要、路径、链接、文件类型、大小、数量和深度经过 Runtime 门禁。
6. `maven-failsafe-v1` 只读解析版本化 Fixture，拒绝 DTD/实体、损坏 XML 和统计冲突，输出脱敏摘要与稳定失败指纹。
7. 失败报告必须先分类；只有明确的业务代码缺陷允许自动修复。依赖、环境、Runner、Workflow、配置、报告不可信或未知原因必须人工介入且不消耗修复预算。自动修复不扩大已确认范围，不改变测试预期、Workflow、项目规则或保护规则；每次修复都重新执行全部本地验证并绑定提交、远端新 Head 和原失败事件。
8. 初始失败后最多三次修复，预算跨恢复保持；相同 Head、Artifact 和失败事件重复调用幂等。
9. 外部 Head/Base/所有权变化、超时、Artifact 或报告不可信、范围扩大、验证失败或预算耗尽时失败关闭并进入风险决策。
10. 最终 Head CI 通过或 GitHub 明确判定无需 CI 后输出 `current_stage=completed`、`ci_status=passed|not_required`、`agentic_next_action=none`，生成完成证据并关闭本地运行。
11. 完成不执行 developer 内置代码审查，也不自动合并、Jira Done、发布、Tag、强推或修改保护分支；项目明确要求的 QA、安全、运维和流程门禁继续有效。

### 保护行为

1. 未显式选择 `development_change_v2` 或 Profile 与 manifest 的 CI 配置不一致时，不进入 CI 自动化。
2. CI 要求未知、5 分钟启动超时、执行后 10 分钟完成超时、非代码缺陷、证据不可信、外部 Head 变化、范围扩大或预算耗尽时，停止自动修复并要求人工介入。
3. Artifact 只进入当前 task-run 受管目录；拒绝路径穿越、链接、特殊文件、覆盖写入和超过版本化上限的内容。
4. CI 通过或 GitHub 明确判定无需 CI，只关闭本地 AIAgent run，不授权合并 PR、Jira Done、发布、Tag、强推或保护分支写入。

### 验收证据

1. Runtime 测试覆盖 GitHub PR/Base 无 Workflow 时无需 CI、适用 Workflow 的自动识别、CI 未在 5 分钟内启动、启动后未在 10 分钟内完成、严格 `SUCCESS`、`SKIPPED` 失败及外部 Head 变化。
2. Runtime 测试证明非代码失败不能记录修复，只有唯一 `ci_code_defect` 事件能够绑定修复提交并消耗预算。
3. 版本化 Failsafe 合成 Fixture 与测试覆盖脱敏解析、DTD/统计冲突拒绝，以及 ZIP 路径、链接和大小门禁。
4. 共享 manifest/result Schema、maintainer 验收端和 developer 执行端对 v2 CI 配置及完成证据保持一致。

### 固定验证

```sh
bash maintainer/scripts/test-python-runtime.sh
bash maintainer/scripts/test-resources.sh
bash developer/tests/bootstrap/test_install_boundary.sh
bash maintainer/scripts/test-release-workflow.sh
git diff --check
```
