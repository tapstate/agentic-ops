# 缺陷修复任务准入检查清单（tapdata）

> **本文件由 `projects/tapdata/admission.json` 生成，请勿手工编辑。**
> 改规则请改 JSON，然后执行 `python3 workflow/project_rules.py render --project tapdata`。

执行时不要读本文件，直接用机读接口：

```sh
python3 workflow/task.py checklist --task-class defect_fix          # 人读
python3 workflow/task.py checklist --task-class defect_fix --json   # 机读
python3 workflow/task.py record --issue-key <JIRA-KEY> --key <fact key> --value <值>
```

## 必填项（缺一不可，`task.py advance` 硬拦）

| 项 | fact key | 到哪里找 | 示例 | 说明 |
|---|---|---|---|---|
| 问题分支 | `problem_branch` | Jira 描述「问题分支」章节 | develop | 缺陷在哪个分支可复现/被发现 |
| 问题版本 | `problem_version` | Jira 描述「问题版本」章节 | develop | 问题所属主仓库版本；各仓库基线分支按 profile.json baseline_branches 对齐 |
| 问题现象 | `problem_symptom` | Jira 摘要或描述「问题现象」章节 | TM 启动持续输出 ES health check refused 告警 | 用户/日志/系统实际观察到的现象 |

以下项 agent 可结合卡片/日志/源码给出**建议值**，但必须由研发工程师确认后再 record，不得替确认：问题分支、问题版本、问题现象。

## 可选项（有则记录）

| 项 | fact key | 到哪里找 | 说明 |
|---|---|---|---|
| 复现路径 | `reproduce_path` | 描述「复现路径」章节 | 无法稳定复现时写明已知触发条件 |
| 验收标准 | `acceptance_criteria` | 描述「验收标准」章节 | 修复后怎样判断可以验收 |

## 准入失败流程（强制点：workflow/task.py advance（离开 task_intake 时 exit 3））

1. 一次列全所有缺失项，不要挤牙膏
2. 把缺失项与 supplement 文案写成 Jira 评论发布（write_jira_comment 属 free）
3. python3 workflow/task.py block --reason "..." 并结束本轮，等研发工程师补卡

## 修复前门禁（强制点：workflow/task.py advance（进入 implementation 时校验授权存在且 issue_key 一致））

授权作用域 `task_execution`，由 workflow/authorization.py grant（研发工程师执行，即设计确认的载体）。授权签发前不得修改任何代码；方案实质变更需 task.py reset --stage design_review 重新确认

## 验证结论规则（强制点：workflow/task.py advance（离开 implementation 时 exit 3））

离开 implementation 前必须 `record --key verification`，且不得命中：

- `-DskipTests|-Dmaven\.test\.skip` —— 跳过测试的构建不能作为验证结果；必须单独执行与变更范围匹配的测试
- `^\s*(未验证|无需验证|不需要验证|待补充|无|N/?A|TODO|skipped)\s*$` —— 验证结果必须是实际执行的命令及其退出结果，不能是占位词
