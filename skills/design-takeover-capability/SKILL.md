---
name: design-takeover-capability
description: Use when AgenticOps needs to design and connect a new Jira task takeover capability, task type, workflow, or Jira form mapping before implementation. Guides the agent to define the standard execution process, inspect whether Jira fields and workflow transitions satisfy takeover requirements, guide the user to complete Jira/profile configuration when gaps exist, and only then produce an implementation and test plan.
---

# Design Takeover Capability

Use this skill before developing any new AgenticOps task takeover capability.

The purpose is to turn a vague "support this kind of task" request into a confirmed standard process, a verified Jira workflow/form fit, and an implementable development plan. Do not write code until the standard process and Jira adaptation have both been confirmed.

## Required Inputs

- Target project or workspace, for example `tapdata`.
- One or more representative Jira issues, or a clear task type if no issue exists yet.
- Expected business outcome for the new takeover capability.

If any input is missing, ask for it before proceeding.

## Phase 1: Define The Standard Process

Start from the business flow, not the CLI implementation.

Produce a concise draft covering:

- `task_type`: the stable machine-readable task type.
- `process_id`: the standard process this capability follows or introduces.
- Stages: entry stage, execution stages, review or human gate stages, completion stage, blocked stage.
- Gates: what must be true before takeover, before execution, before completion, and before release.
- Required Jira task properties at each stage.
- Output data written back at each stage.
- Completion evidence required for audit.
- Roles that must make human decisions, such as 研发负责人、流程负责人、代码审查人、QA.

Then present a task property matrix:

| Stage | Required Input | Written Output | Jira Field Or Source | Missing Behavior |
| --- | --- | --- | --- | --- |

Ask the user to confirm the standard process before moving to Phase 2.

## Phase 2: Inspect Jira Workflow And Form Fit

After the standard process is confirmed, inspect representative Jira issues and workflow metadata.

Check:

- Issue exists and belongs to the expected Jira project.
- Assignee and current user ownership rules can be evaluated.
- Current status maps to a standard entry stage.
- Required transitions exist for start, blocked, review, completion, and release.
- Required fields exist, are visible, and are writable at the stage where AgenticOps needs them.
- Required fields are mapped in the workflow profile.
- `current_agent_id` and `takeover_at` have a stable Jira field or comment/template mapping.
- Missing fields have an approved fallback, such as a Jira comment template or description template.

If Jira does not satisfy the process, do not proceed to development planning. Output a configuration guide with:

- Missing or incompatible field.
- Required field type.
- Required stage or screen.
- Suggested Jira field name.
- Suggested workflow profile mapping.
- Whether the user must change Jira configuration or only AgenticOps profile/assets.

Ask the user to confirm the Jira/profile adjustment approach before moving to Phase 3.

## Phase 3: Produce Development Plan

Only after Phase 1 and Phase 2 are confirmed, write a development plan.

The plan must include:

- Operation contract changes or new operation contracts.
- Process contract changes or new process contracts.
- Workflow profile mapping changes.
- CLI command or handler changes, if needed.
- Jira adapter changes, if needed.
- Evidence template changes.
- Fake Jira fixtures and tests.
- Real Jira gate or dry-run validation steps.
- Acceptance criteria using representative Jira issues.

The plan should be implementation-ready, but still stop before coding unless the user explicitly asks to execute it.

## Hard Rules

- Do not code before the standard process is confirmed.
- Do not produce a final implementation plan before Jira workflow and form fit are checked.
- Do not bypass missing Jira fields by inventing unmapped data storage.
- Do not treat comments as a field replacement unless the standard process or user explicitly approves it.
- Do not weaken takeover ownership rules to make a demo pass.
- If a Jira workflow gap blocks automation, guide the user to configure Jira or adjust the workflow profile.

