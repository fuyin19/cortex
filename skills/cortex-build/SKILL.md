---
name: cortex-build
description: Add a source and optional conversion to an explicitly selected registered Cortex 5.1 Bundle.
---

# Build Cortex records

Resolve the installed Cortex application command exactly once. Canonicalize it to one canonical absolute path, require that path to be an available ordinary non-reparse executable, and apply the platform's executable check. Probe the exact quoted path with `--version`; require exit 0, stdout consisting of exactly one `cortex 5.1.0` line, and empty stderr. Reuse that same quoted absolute path for every call below. Stop if any check fails: do not fall back, re-resolve, invoke a bare PATH command, set `PYTHONPATH`, use `python -m`, or try an alternate command.

```text
"<CORTEX-ABSOLUTE-EXECUTABLE>" --version
```

Require the user to supply both a KB root and an explicit Bundle ID. Never infer a Bundle from content, choose a default, scan for a likely directory, or construct a Bundle path. Resolve the ID first:

```text
"<CORTEX-ABSOLUTE-EXECUTABLE>" --json --kb-root <root> registry resolve --bundle-id <id>
"<CORTEX-ABSOLUTE-EXECUTABLE>" --json --kb-root <root> --bundle-id <id> manage status
"<CORTEX-ABSOLUTE-EXECUTABLE>" --json --kb-root <root> --bundle-id <id> manage config show --profile record
"<CORTEX-ABSOLUTE-EXECUTABLE>" --json --kb-root <root> --bundle-id <id> manage config show --profile tags
"<CORTEX-ABSOLUTE-EXECUTABLE>" --json --kb-root <root> --bundle-id <id> manage config show --profile layout
```

Inspect the complete Layout Profile before preparing metadata. For `title-slug`, prepare only `title`, optional `timestamp`, and ordered unique `tags`. For `partition-title-date`, require and preserve a caller-supplied timezone-aware RFC3339 `timestamp`; never synthesize it. In both cases include exactly one tag from the Layout Profile's partition group. `partition-title-date` is valid only with `duplicate_name_strategy: reject`. Cortex 5.1.0 is the only write boundary.

If any requested tag is absent, stop before `record add`. Do not invent the tag, its description, or its group. Prepare a complete Tag Profile 2 replacement that preserves every existing entry and adds the user's proposed tag and description to the user-selected group. Show the full replacement and a complete before/after diff, then obtain explicit user confirmation. Only after confirmation, submit that whole profile through `manage config set`. Treat the profile update and record addition as separate mutations; if either returns `busy/5`, report it and retry only with user direction.

```text
<confirmed-complete-tags-json> | "<CORTEX-ABSOLUTE-EXECUTABLE>" --json --kb-root <root> --bundle-id <id> manage config set --profile tags --file -
<metadata-json> | "<CORTEX-ABSOLUTE-EXECUTABLE>" --json --kb-root <root> --bundle-id <id> record add --source <file> [--conversion <file-or-dir>] --metadata -
```

Use the returned `data.record` as the stable edit operand. Never write registry, profile, metadata, source, conversion, staging, or lock files directly.
