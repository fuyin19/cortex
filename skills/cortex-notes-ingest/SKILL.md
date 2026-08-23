---
name: cortex-notes-ingest
description: Capture one canonical Markdown note into an initialized Cortex Notes Bundle.
---

# Cortex Notes ingest

Use this skill only for `notes.note.add`. Never initialize or reconfigure Notes storage, and never read, edit, archive, or delete an existing note.

Invoke the complete skill-local runtime with the explicitly configured absolute Python 3.11/UCD 14 interpreter:

```text
"%CORTEX_PYTHON%" -I <skill>/scripts/run_notes.py --json --root <absolute-notes-root> --tools-root <absolute-tools-root> note add --bundle <bundle> [--partition <partition>] --title <title> --body-file <absolute-markdown> [--timestamp <canonical-+08:00-time>]
```

Default roots are `C:\Users\fuyin\Desktop\anti-entropy\notes` and `C:\Users\fuyin\Desktop\anti-entropy\tools`. Omit `--partition` only for `daily-notes`; its partition is derived from the canonical Hong Kong timestamp. Pass one of the configured exact partitions for the other Bundles. Report the exact Result without claiming search, indexing, restoration, synchronization, or deployment.
