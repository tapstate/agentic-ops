# Claude 端到端验证

1. 执行 `<产品根目录>/agenticops init --workspace <项目工作空间> --project tapdata --agent claude`。
2. 进入项目工作空间，通过 `./agenticops start claude` 启动 Claude，要求接管两个 TapData 测试任务并保持两者 active；工作空间由根入口自动绑定。
3. 验证缺少准入事实时会补卡并停止，未授权时不能进入实现。
4. 分别按 issue key 登记仓库并执行 `repository prepare`，在同一 Claude 会话读取 `repository context --issue-key <key> --json` 后继续处理，验证状态和授权互不串用。
5. 验证授权仓库工作分支的 commit/push 可放行，未登记仓库和其它分支会收回放行。
6. 验证 merge、release、Tag、强推和保护分支写入不会被任务授权覆盖。
7. 完成各仓 PR/CI 记录，生成任务级证据并人工确认后回写 Jira。

自动化基线（在源码产品根目录或安装产品根目录执行）：

```sh
bash internal/tests/test_runtime.sh
bash internal/tests/test_resources.sh
bash tests/test_install.sh
bash internal/tests/test_release.sh
```
