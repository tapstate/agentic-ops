# AgenticOps `developer` 工作面规则

- 只在独立业务项目 AI 工作空间执行，命令入口固定为 `ao-work`。
- developer-only sparse checkout 是防误入边界。不得读取 `.git` 中的 maintainer 路径、执行 `git show <ref>:maintainer/...`、修改 sparse checkout 范围或恢复维护资产；需要内容级隔离时必须由维护者改用独立分发方案。
- 只读取当前业务工作空间明确绑定的身份、授权、项目配置和任务状态。
- 缺少当前工作面输入时停止，不得搜索或回退读取维护工作面、其它工作空间或本机历史配置。
- 不加载 AgenticOps 源头维护 Skill、规则、发布流程或故事质量门禁。
- 真实 Jira 写入、Git 推送、拉取请求和其它高风险副作用必须满足操作契约与人工门禁。
