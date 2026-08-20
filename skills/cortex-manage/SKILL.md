---
name: cortex-manage
description: Inspect and manage Cortex 6.0 Bundles, profiles, exact records, and registries through the closed CLI.
---

# Manage Cortex 6

Canonicalize one absolute executable, require it to be an ordinary non-reparse executable, and require exactly one `cortex 6.0.0` line with empty stderr. Do not fall back, re-resolve, call a bare PATH command, set `PYTHONPATH`, or use `python -m`.

Use explicit Bundle selection. Layout 3 has only `tag-title-date` with duplicate `reject`; null naming group is valid only for an empty Bundle. After the first record Layout is immutable. Record edit may change only non-naming tags.

For an authorized exact record inspection run:

```text
"<CORTEX-ABSOLUTE-EXECUTABLE>" --json --workspace <bundle> record show --record <exact-unit>
```

Deletion is destructive. Show the exact unit, `tree_sha256`, metadata, and manifest to the user and obtain explicit authorization for that exact digest. Then run:

```text
"<CORTEX-ABSOLUTE-EXECUTABLE>" --json --workspace <bundle> record delete --record <exact-unit> --expected-tree-sha256 <lowercase64>
```

Never broaden to prefix/glob/batch deletion. A mismatch stops safely. `delete_incomplete` means manual recovery is required; do not invent trash, tombstones, journals, or direct filesystem repair.

The repository migration script is not an installed/public capability. The `project-summer` pilot requires a pinned Cortex 6.0.0 Candidate, read-only plan, exact 27/25/2 count gate, detached digest approval, and separate authority for build, cutover, and Registry adoption.
