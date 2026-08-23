---
name: cortex-notes-build
description: Initialize and structurally expand an explicit Cortex Notes Registry and its Bundles.
---

# Cortex Notes build

Use this skill only for `notes.registry.init`, `notes.bundle.init`, `notes.bundle.partition.add`, and structural validation. Never add, edit, archive, or delete notes.

Invoke `scripts/run_notes.py` with the absolute `CORTEX_PYTHON` Python 3.11/UCD 14 interpreter and `-I`. Every root and operand must be explicit and absolute. Initialize the Registry once, then initialize exactly `daily-notes`, `tools-feedback`, and `ideas`. Tools partitions expand monotonically and only after the runtime validates an ordinary direct-child Git repository under the explicit tools root. Stop at the first non-`ok` Result; do not roll back or infer a different root.

The default Notes root is `C:\Users\fuyin\Desktop\anti-entropy\notes`; the default tools root is `C:\Users\fuyin\Desktop\anti-entropy\tools`. The runtime is offline and skill-local. It creates only the canonical file structure; it does not initialize Git, install skills, migrate data, or manage external plugins.
