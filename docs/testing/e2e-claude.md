# Claude 端到端验证

1. 执行 `<产品根目录>/agenticops init --workspace <项目工作空间> --project tapdata --agent claude`。
2. 进入项目工作空间，通过 `./agenticops start claude` 启动 Claude，要求接管两个 TapData 测试任务并保持两者 active；工作空间由根入口自动绑定。
3. 验证缺少准入事实时会补卡并停止，未授权时不能进入实现。
4. 分别按 issue key 登记仓库并执行 `repository prepare`，在同一 Claude 会话读取 `repository context --issue-key <key> --json` 后继续处理，验证状态和授权互不串用。
5. 核对不存在生成的 `.claude/settings.json`；Git/Jira/PR 使用平台原生权限。Workflow 状态写入带 expected-run-id，advance 另带 expected-stage，过期确认或证据缺失时不能推进。
6. 在独立夹具验证旧托管 Hook 只经显式迁移移除，漂移文件保留；合并、发布等继续按用户授权和服务端保护处理，不声称本地检查点拦截这些调用。
7. 完成各仓 PR/CI 记录，生成任务级证据并人工确认后回写 Jira。

自动化基线（在源码产品根目录执行；安装目录不含 internal）：

```sh
bash internal/tests/test_runtime.sh
bash internal/tests/test_resources.sh
bash tests/test_install.sh
bash internal/tests/test_release.sh
```
