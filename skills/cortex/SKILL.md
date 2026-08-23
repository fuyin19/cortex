---
name: cortex
description: Explicit invocation only router to exactly one canonical Cortex KB or Notes build, ingest, or manage role.
---

# Cortex router

Run only when the user explicitly invokes `cortex`. Generic note, KB, or coding requests are insufficient triggers. This is an instruction-only router with no scripts, runtime, domain operations, CLI forms, or confirmation logic.

Determine exactly one domain, `KB` or `Notes`, and exactly one action, `build`, `ingest`, or `manage`, from the explicit request. Then read and follow exactly one sibling role skill:

- KB build: `../cortex-kb-build/SKILL.md`
- KB ingest: `../cortex-kb-ingest/SKILL.md`
- KB manage: `../cortex-kb-manage/SKILL.md`
- Notes build: `../cortex-notes-build/SKILL.md`
- Notes ingest: `../cortex-notes-ingest/SKILL.md`
- Notes manage: `../cortex-notes-manage/SKILL.md`

If the domain or action is ambiguous, ask for clarification before reading a role skill or acting. If the requested operation is unsupported, the selected role is unavailable, or required existing state is missing, fail closed. Never select two roles, duplicate their domain rules, copy their CLI logic, weaken their explicit selectors, or reproduce their confirmation procedure.
