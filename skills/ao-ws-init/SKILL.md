---
name: ao-ws-init
description: 创建、刷新或受控解绑 AgenticOps 单任务工位，核验产品根、项目和持久材料复用；不接管业务任务。
---

# 工位生成与清理

工作空间与 Product Root 必须分开。先只读核验产品根入口、contracts 和目标 Project，回读工作空间绝对路径、项目、Agent 集合及现有绑定。用户已给出的有效输入不重复确认；未知路径或其它产品绑定不覆盖。

## 生成与刷新

新工位执行：

```sh
<product-root>/agenticops init --workspace <workspace> --project <project>
<product-root>/agenticops doctor --workspace <workspace>
```

明确 Agent 集合时重复追加 --agent。生成 config/source/runtime/archive 和空 current，不下载业务源码或接管 Jira。非空 source/config/archive 须列出保留材料并取得明确复用决定，追加 --reuse-materials；runtime 必须为空，不覆盖文件、不导入配置或历史授权。已有同产品绑定用 repair 刷新并 doctor 回读，不通过 init 静默更换项目或 Agent。

## 清理与再生成

先检查 current 和 operation。存在任务则由项目 Skill 完成 release/clean，不用 workspace purge 代替任务归档；未完成操作先恢复。仅空闲、操作完成、runtime 干净时，展示精确工作空间及接线/状态删除范围，明确保留 source/config/archive 与用户文件，取得确认后执行：

```sh
<product-root>/agenticops workspace purge --workspace <workspace> --yes
```

不使用 --all。核验 .agenticops 与受管接线已删除、持久材料仍在、提示索引已注销。未知状态、路径漂移或脏现场失败时保留，不手工补删。之后需要重建才重新执行生成；这是同版本可独立验证的能力。

## 升级边界

不兼容时停止：用原版本保存材料并受控解绑，再安装/切换产品并生成新工位。新版本不解析旧任务；升级只编排已成功的原版清理和目标生成，不能用 repair 在线迁移。本次不发过渡版，旧安装不保证直接 update。

结果报告包括 workspace_id、产品根、项目、Agent、current/op、四类目录及 doctor，区分生成成功与任务、应用、PR/CI 验收。此 Skill 不授权下载业务仓库、修改代码、提交、推送、PR、合并、发布或删除未受管材料。
