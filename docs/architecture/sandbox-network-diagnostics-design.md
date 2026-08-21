# 沙箱网络与本机代理诊断设计

- Jira：AO-70
- 工作面：maintainer
- 状态：待设计审查确认

## 问题与结论边界

当 AIAgent 配置 `HTTP_PROXY`、`HTTPS_PROXY` 或 `ALL_PROXY` 指向本机代理时，失败不能直接归因为 Jira、GitHub、凭证或代理服务。本案已通过同一命令在两个执行边界的差分验证确认：沙箱内访问 `127.0.0.1:7890` 返回 `Operation not permitted`，获准的非沙箱环境使用相同代理变量可完成 Jira 只读校验。

该证据只说明本案是沙箱阻断本机回环/出网造成；产品不能把任意网络失败一概标记为沙箱问题。

## 目标与范围

在 maintainer Runtime 新增只读 `ao-maint diagnose network`，让维护者在 Jira 或 GitHub 访问失败前后获得脱敏、可区分的诊断结果。

- 读取代理环境变量并报告来源、scheme、主机类别、端口、是否含 userinfo 及 `NO_PROXY` 对 Jira/GitHub 目标的实际匹配结论；不输出完整 URL、userinfo、Token、Authorization 或环境变量原文。
- 只有代理对失败目标实际生效、代理为 loopback、TCP 返回 `EPERM` / `EACCES` 且存在 `CODEX_SANDBOX_NETWORK_DISABLED` 标记时，才分类为 `network_sandbox_loopback_blocked`。
- 对有效代理地址执行受限 TCP 连通性探测；共享代理路由已被阻断时，Jira/GitHub 必须标为 `not_run/shared_route_blocked`，不重复发起请求。
- 路由可达后才对 Jira 与 GitHub 执行只读 probe。GitHub 使用当前 `gh` 会话的静默只读 probe；分别保留 DNS、超时、TLS、代理不可达、认证/授权、服务端响应和未知读取失败。探测不写 Jira、Git、GitHub 或本地安装身份。
- 诊断阻断使用 `ok=false`、`status=blocked`、退出码 `2`，但保留完整 `checks`；全部检查通过才返回成功 JSON。统一 JSON 包含 `checks.proxy`、`checks.loopback`、`checks.jira`、`checks.github`、`diagnosis`、`agentic_next_action`。`diagnosis` 至少包含稳定 `code`、置信度、根因和经过长度限制的证据摘要。

本任务不修改 developer Runtime、不新增通用跨工作面 Runtime、不开启或绕过沙箱，也不把“非沙箱重试”伪装为常规业务任务授权。

## 分类与人工动作

| 条件 | 稳定分类 | 下一步 |
| --- | --- | --- |
| loopback 代理连接返回权限拒绝、代理对目标实际生效，且存在 `CODEX_SANDBOX_NETWORK_DISABLED` | `network_sandbox_loopback_blocked` | 在获得的非沙箱执行环境重试原 Runtime 命令；不得修改凭证或 Jira 状态 |
| 直接出网被权限拒绝，且存在 `CODEX_SANDBOX_NETWORK_DISABLED` | `network_sandbox_egress_blocked` | 请求在非沙箱环境完成只读验证 |
| 代理地址不可达/被拒绝，但非沙箱特征不完整 | `network_proxy_unreachable` | 检查代理服务和地址 |
| DNS 或超时 | 保留具体网络分类 | 检查网络、DNS 或服务状态 |
| Jira/GitHub 返回认证或授权失败 | 保留各自认证分类 | 校验对应授权，不把它归因为沙箱 |
| 读取成功 | `network_diagnosis_passed` | 继续原只读或受控工作流 |

只有第一、二行可以给出高置信度的沙箱结论；其它情形保持保守诊断。稳定代码还包括 `network_proxy_configuration_invalid`、`network_dns_failed`、`network_timeout`、`network_tls_failed`、`jira_authorization_failed`、`github_authorization_failed`、`network_probe_failed` 与 `network_diagnosis_passed`。

## 实现与验证

1. 在 maintainer Runtime 增加 `diagnose network` 命令和纯 Python 诊断服务，并接入顶层 CLI。
2. 复用 Jira 的连接配置和既有只读认证校验；GitHub probe 通过当前 `gh` 会话确认访问，但不读取或输出登录身份。
3. 为代理解析、`NO_PROXY` 生效/绕过、loopback `EPERM`、直接出网 `EPERM`、普通拒绝、超时、Jira/GitHub 独立失败、共享路由短路、脱敏、CLI JSON 与退出码增加 fixture 测试。
4. 更新 maintainer 用户故事和故事注册表，使 Runtime、用户说明和故事门禁对同一公开命令达成一致；不在本任务建立新的 maintainer 操作契约体系。
5. 在本案环境中运行诊断：沙箱内应给出 `network_sandbox_loopback_blocked`；获准非沙箱环境应验证 Jira probe 成功或给出非沙箱的精确失败分类。

风险：执行环境没有可靠的官方沙箱标记时，不能输出高置信度结论；代理、服务和凭证状态可同时变化，Runtime 必须保留每个检查的独立结果，不能用单一错误覆盖它们。
