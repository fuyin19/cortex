---
name: cortex-notes-ingest
description: Explicit invocation only for capturing one canonical Markdown note into an initialized Cortex Notes 2 Bundle.
---

# Cortex Notes ingest

Use this role only when the user explicitly names `cortex-notes-ingest`, or explicitly invokes `cortex` and the router selects Notes ingest. Generic note, KB, or coding requests are insufficient triggers.

Own only `notes.note.add`. Never initialize or reconfigure Notes storage, and never read, edit, archive, or delete an existing note. Read the selected Bundle's validated Note 1, Tag 2, and Layout 1 profiles; select or derive the partition only as Layout 1 directs. Never dispatch from the Bundle id.

Invoke the complete skill-local runtime with the explicitly configured absolute Python 3.11/UCD 14 interpreter:

```text
"%CORTEX_PYTHON%" -I <skill>/scripts/run_notes.py --json --root <absolute-notes-root> [--tools-root <absolute-tools-root>] note add --bundle <bundle> [--partition <partition>] --title <title> --body-file <absolute-markdown> [--timestamp <canonical-+08:00-time>]
```

Every path is explicit and absolute. Omit `--partition` only when the selected Layout 1 date policy derives it from the canonical Hong Kong timestamp. Pass the exact configured tag for a tag-group layout. Pass `--tools-root` only when the selected layout uses Git-repository admission; it is then mandatory. Report the exact Result without claiming search, indexing, restoration, synchronization, or deployment.
