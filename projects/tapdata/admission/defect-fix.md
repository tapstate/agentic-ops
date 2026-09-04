# 缺陷修复任务准入检查清单（tapdata）

> **本文件由 `projects/tapdata/admission.json` 生成，请勿手工编辑。**
> 改规则请改 JSON，然后执行 `python3 workflow/project_rules.py render --project tapdata`。

执行时不要读本文件，直接用机读接口：

```sh
python3 workflow/task.py checklist --task-class defect_fix          # 人读
python3 workflow/task.py checklist --task-class defect_fix --json   # 机读
python3 workflow/task.py record --issue-key <JIRA-KEY> --key <fact key> --value <值>
```

## 核对项（缺项披露，在质量检查点记录处置）

| 项 | fact key | 到哪里找 | 示例 | 说明 |
|---|---|---|---|---|
| 问题分支 | `problem_branch` | Jira 描述「问题分支」章节 | develop | 缺陷在哪个分支可复现/被发现 |
| 问题版本 | `problem_version` | Jira 影响版本 fields.versions（多选） | 4.18.0、4.21.0 | 保留全部影响版本；主仓找不到对应分支则拒绝。导入结果会逐项列出版本、对应主仓分支和远端 SHA，供用户引用并回写 Jira 分支确认；该引用不替代 repository prepare 固化的任务基线。先核验 develop 是否有同一缺陷，有则优先修复 develop，影响版本由研发合并修复；否则只选择一个影响版本修复，其余人工合并。由 task.py issue-versions 导入，不手填分支替代版本。 |
| 问题现象 | `problem_symptom` | Jira 摘要或描述「问题现象」章节 | TM 启动持续输出 ES health check refused 告警 | 用户/日志/系统实际观察到的现象 |

以下项 agent 可结合卡片/日志/源码给出**建议值**，但必须由研发工程师确认后再 record，不得替确认：问题分支、问题现象。

## 可选项（有则记录）

| 项 | fact key | 到哪里找 | 说明 |
|---|---|---|---|
| 修复方案 | `fix_plan` | 本地源码分析及研发确认 | 根因、修改范围、修复方式、风险及回滚；在 Q2 确认前记录，并随检查点回写 Jira |
| 复现路径 | `reproduce_path` | 描述「复现路径」章节 | 无法稳定复现时写明已知触发条件 |
| 验收标准 | `acceptance_criteria` | 描述「验收标准」章节 | 修复后怎样判断可以验收 |

## 准入失败流程（强制点：workflow/task.py checklist 与 quality.py 检查点确认）

1. 一次列全缺失事实与 Jira「已链接工作项」中的 Test 用例，继续不依赖缺项的源码分析。
2. 在质量检查点报告缺口并请用户决定补充或带风险继续，保留来源和理由。
3. 事实不可信、仓库基线不明或权限不足时停止对应步骤，不以质量处置替代安全授权。

## 修复前门禁（强制点：workflow/task.py advance（进入 implementation 时校验授权存在且 issue_key 一致））

授权作用域 `task_execution`，由 workflow/authorization.py grant（研发工程师执行，即设计确认的载体）。授权签发前不得修改任何代码；方案实质变更需 task.py reset --stage design_review 重新确认

## 验证结论规则（强制点：workflow/task.py advance（离开 implementation 时 exit 3））

由 `workflow/quality.py` 核对用例、实际执行及用户处置；用户可选择补测、不适用、延期或接受风险。原始结果不改写，不要求全绿。文本 verification 仅为记录，不代替用例验收。
仓库基线、实施授权及外部 Jira Validator 仍独立生效。详见 [质量检查与证据](../../../docs/usage/quality-checkpoints.md)。
