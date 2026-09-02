---
name: cortex
description: Explicit-only entry for Cortex KB, Notes, and Collaborative Workspace operations.
---

# Cortex

Run only when the user explicitly invokes `cortex`; generic workspace, note, KB, or coding requests are insufficient.

Determine exactly one domain and exactly one action or operation, then read and follow exactly one focused reference:

| Domain | Operation | Reference |
|---|---|---|
| KB | ingest | `references/kb-ingest.md` |
| KB | build | `references/kb-build.md` |
| KB | manage | `references/kb-manage.md` |
| Notes | ingest | `references/notes-ingest.md` |
| Notes | build | `references/notes-build.md` |
| Notes | manage | `references/notes-manage.md` |
| Collaborative Workspace | prepare/status/validate | `references/collaborative-workspace.md` |

The references call one of three private adapters under `scripts/`: `kb`, `notes`, or `collaborative-workspace`. These directories are runtime assets, not independently discoverable skills, and contain no `SKILL.md`.

The framework is always explicit. For Registry v2 require the selected adapter's exact identity: `cortex-kb-registry/v2` or `cortex-notes-registry/v2`. Its native runtime also accepts its exact legacy Registry v1 and performs full validation. Never infer a framework from a path or ancestor.

If domain or operation is ambiguous, ask for clarification. If unsupported, unavailable, or required existing state is missing, fail closed. Never combine roles or weaken native selectors, Results, confirmation, locking, or lifecycle rules.
