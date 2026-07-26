# TapData 缺陷修复准入信息缺失

AgenticOps 无法继续接管该缺陷，因为 Jira 卡片缺少缺陷修复准入信息。

- 当前操作：`<operation>`
- 缺失字段：
<missing_fields>

## 建议补充内容

<missing_field_guidance>

## AIAgent 项目分析建议

<admission_suggestions>

## 卡片模板

请在 Jira 描述或对应项目字段中补齐：

```text
问题分支
<例如：develop>

修复分支
<例如：develop>

问题现象
<说明用户、日志或系统实际观察到的问题>

复现路径（可选）
<说明如何复现，无法稳定复现时写明已知触发条件>

验收标准
<可选；说明缺陷修复后怎样判断可以验收>
```

如果 AIAgent 给出了候选分支或目标仓库建议，研发负责人必须确认后再继续执行。

准入通过后，AIAgent 必须先输出修复方案并等待研发负责人确认，确认前不得修改代码。
