---
name: cortex
description: Explicit invocation only router to one Cortex KB, Notes, or Collaborative Workspace skill.
---

# Cortex

Run only when the user explicitly invokes `cortex`; generic workspace, note, KB, or coding requests are insufficient. This instruction-only entry has no scripts or runtime.

Determine exactly one domain and exactly one action or operation, then read and follow exactly one sibling skill contract through its focused reference:

| Domain | Operation | Reference |
|---|---|---|
| KB | ingest | `references/kb-ingest.md` |
| KB | build | `references/kb-build.md` |
| KB | manage | `references/kb-manage.md` |
| Notes | ingest | `references/notes-ingest.md` |
| Notes | build | `references/notes-build.md` |
| Notes | manage | `references/notes-manage.md` |
| Collaborative Workspace | prepare/status/validate | `references/collaborative-workspace.md` |

Each focused reference delegates to its existing sibling compatibility skill; for Collaborative Workspace that sibling is `../cortex-collaborative-workspace/SKILL.md`.

The framework is always explicit. For Registry v2 require the selected adapter's exact identity: `cortex-kb-registry/v2` or `cortex-notes-registry/v2`. Its native runtime also accepts its exact legacy Registry v1 and performs full validation. Never infer a framework from a path or ancestor.

If domain or operation is ambiguous, ask for clarification. If unsupported, unavailable, or required existing state is missing, fail closed. This entry has no scripts, runtime, domain operations, CLI forms, or confirmation logic. Never combine roles or weaken native selectors, Results, confirmation, locking, or lifecycle rules.
