---
name: cortex
description: Explicit invocation only router to one Cortex KB, Notes, or Collaborative Workspace skill.
---

# Cortex router

Run only when the user explicitly invokes `cortex`. Generic workspace, note, KB, or coding requests are insufficient triggers. This is an instruction-only router with no scripts, runtime, domain operations, CLI forms, or confirmation logic.

Determine exactly one domain from the explicit request. For `KB` or `Notes`, also determine exactly one action, `build`, `ingest`, or `manage`. For `Collaborative Workspace`, determine exactly one operation, `prepare`, `status`, or `validate`. Then read and follow exactly one sibling skill:

- KB build: `../cortex-kb-build/SKILL.md`
- KB ingest: `../cortex-kb-ingest/SKILL.md`
- KB manage: `../cortex-kb-manage/SKILL.md`
- Notes build: `../cortex-notes-build/SKILL.md`
- Notes ingest: `../cortex-notes-ingest/SKILL.md`
- Notes manage: `../cortex-notes-manage/SKILL.md`
- Collaborative Workspace: `../cortex-collaborative-workspace/SKILL.md`

If the domain or action/operation is ambiguous, ask for clarification before reading a sibling skill or acting. If the requested operation is unsupported, the selected skill is unavailable, or required existing state is missing, fail closed. Never select two skills, duplicate their domain rules, copy their CLI logic, weaken their explicit selectors, or reproduce their confirmation procedure.
