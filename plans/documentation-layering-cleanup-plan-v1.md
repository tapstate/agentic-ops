# AgenticOps 文档分层清理计划

> **For agentic workers:** 本计划用于清理设计文档与实施计划的边界。执行时必须保持设计文档只表达稳定设计事实，计划文件承担阶段、任务、勾选项、实现说明和剩余工作记录。

**Goal:** 将 AgenticOps 设计文档和计划文档职责分开，避免设计事实源混入实施进度、阶段状态和计划任务。

**Architecture:** `docs/project-rules.md` 定义文档治理规则；`docs/architecture/` 保留稳定架构、能力边界和用户已确认的设计决策；`plans/` 保留实施阶段、任务拆解、勾选项、验收命令和历史推进记录。

**Tech Stack:** Markdown 文档；使用 `rg` 验证设计文档中不再出现计划跟踪内容。

## Global Constraints

- 不修改 CLI 代码、契约 YAML 或运行资产。
- 不把阶段性状态、剩余工作或实现说明写入设计主叙事。
- 架构缺口如果依赖产品或流程取舍，必须标记为需要用户决策，不得伪装成默认计划。
- 保持中文正文，文件名使用英文 ASCII lowercase-kebab-case。

---

## Task 1: 更新文档治理规则

**Files:**
- Modify: `docs/project-rules.md`

**Interfaces:**
- Consumes: 当前 `docs/project-rules.md` 的文档与计划边界规则。
- Produces: 明确的设计、运行时状态和计划文档职责边界。

- [x] **Step 1: 扩展文档分层规则**

  更新 `docs/project-rules.md` 的“文档与计划边界”，明确：

  - 设计文档只写稳定架构事实和能力边界。
  - 运行时文档记录实现边界、命令能力和正式化缺口。
  - 计划文件记录阶段、任务、勾选项、验收命令和实现说明。
  - 架构缺口涉及取舍时必须提示用户决策。

## Task 2: 清理架构设计文档

**Files:**
- Modify: `docs/architecture/agenticops-current-design.md`
- Modify: `docs/architecture/full-design-implementation-design.md`
- Modify: `docs/architecture/project-structure.md`

**Interfaces:**
- Consumes: 已确认的 AgenticOps 当前设计和完整设计实现方案。
- Produces: 不含计划跟踪内容的架构设计文档。

- [x] **Step 1: 清理当前设计**

  从 `docs/architecture/agenticops-current-design.md` 移除“第一阶段交付物”和“第一阶段验收标准”，改为指向 `docs/README.md`、`docs/runtime/` 和 `plans/`。

- [x] **Step 2: 清理完整设计实现方案**

  将 `docs/architecture/full-design-implementation-design.md` 从“分阶段设计 / 执行顺序”改为“能力边界 / 设计决策 / 决策缺口”，阶段推进交给 `plans/full-design-implementation-plan-v1.md`。

- [x] **Step 3: 清理项目结构设计**

  从 `docs/architecture/project-structure.md` 移除当前实现状态和勾选项跟踪语气，只保留目标结构、目录职责和需要用户决策的目录边界取舍。

## Task 3: 验证

**Files:**
- Read: `docs/project-rules.md`
- Read: `docs/architecture/agenticops-current-design.md`
- Read: `docs/architecture/full-design-implementation-design.md`

**Interfaces:**
- Consumes: 更新后的文档。
- Produces: 文档分层自检结果。

- [x] **Step 1: 扫描计划跟踪词**

  Run:

  ```sh
  rg -n "实现说明|\\[x\\]|\\[ \\]|第一阶段交付物|第一阶段验收标准|执行顺序|分阶段设计|第一阶段操作" docs/architecture docs/project-rules.md
  ```

  Expected: 不再在 `docs/architecture/` 中出现计划跟踪内容；`docs/project-rules.md` 只允许出现规则说明语境。

- [x] **Step 2: 检查 Git 状态**

  Run:

  ```sh
  git status --short
  ```

  Expected: 只出现本次文档清理相关文件。
