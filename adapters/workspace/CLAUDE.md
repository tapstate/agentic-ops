<!-- 由 AgenticOps 生成；不要在项目工作空间直接维护。 -->
@AGENTS.md

## Claude 接线

Claude 的副作用操作由项目工作空间 `.claude/settings.json` 中的 `PreToolUse` Hook 接入
AgenticOps Gate。`AGENTS.md` 是 Claude 与其它 Agent 共用的协作入口，平台专属行为
只保留在本节和 Claude Adapter 中。
