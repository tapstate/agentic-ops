# PROD-001 安装并接入多种 Agent

研发工程师安装稳定 `main` 的产品资产，并为一个产品项目工作空间接入 Claude、
Codex 或两者；工作空间可同时接管项目下多个任务，每个任务可以组织多个代码仓库。

### 验收标准

- macOS、Linux 使用 Git 和 Python 3.9+ 即可安装、更新和回退。
- 中央 Product Root 保存唯一运行资产；源码根和安装根使用同一入口和产品结构。
- 工作空间只获得版本化项目绑定、Agent 原生入口、Hook 接线和归一化多任务 `.gate/`，不复制
  Project Skill、Policy 或 Runtime。
- Claude、Codex 原生事件转换为同一版本化标准请求和标准判定。
- `agenticops doctor` 发现产品版本和薄接线漂移，`agenticops repair` 重建派生接线，
  不修改任务状态和授权；带 `product: agenticops` 标记的旧版复制 Project Skill 可被
  清理，而同名非产品文件必须拒绝删除。

### 保护行为

- 安装只检出产品目录，不包含 `internal/`、仓库发布脚本和发布凭据。
- Agent 适配器不得复制 Policy、任务状态机或项目规则。
- 适配器必须通过文件数、代码预算、禁止依赖和禁止状态写入的重量门禁。

### 验收证据

- 产品稀疏安装、中央入口、项目工作空间初始化、漂移诊断和幂等修复结果。
- 更新到新提交、工作目录刷新并回退到上一提交的测试结果。
- Agent Manifest 产物生成和 Claude/Codex 标准语义一致性结果。
