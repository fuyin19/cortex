---
name: cortex-build
description: Add a source and optional conversion to an explicitly selected registered Cortex 5.1 Bundle.
---

# Build Cortex records

Require the user to supply both a KB root and an explicit Bundle ID. Never infer a Bundle from content, choose a default, scan for a likely directory, or construct a Bundle path. Resolve the ID first:

```text
cortex --json --kb-root <root> registry resolve --bundle-id <id>
cortex --json --kb-root <root> --bundle-id <id> manage status
cortex --json --kb-root <root> --bundle-id <id> manage config show --profile record
cortex --json --kb-root <root> --bundle-id <id> manage config show --profile tags
cortex --json --kb-root <root> --bundle-id <id> manage config show --profile layout
```

Prepare metadata containing only `title`, optional `timestamp`, and ordered unique `tags`, including exactly one tag from the Layout Profile's partition group. Cortex 5.1.0 is the only write boundary.

If any requested tag is absent, stop before `record add`. Do not invent the tag, its description, or its group. Prepare a complete Tag Profile 2 replacement that preserves every existing entry and adds the user's proposed tag and description to the user-selected group. Show the full replacement and a complete before/after diff, then obtain explicit user confirmation. Only after confirmation, submit that whole profile through `manage config set`. Treat the profile update and record addition as separate mutations; if either returns `busy/5`, report it and retry only with user direction.

```text
<confirmed-complete-tags-json> | cortex --json --kb-root <root> --bundle-id <id> manage config set --profile tags --file -
<metadata-json> | cortex --json --kb-root <root> --bundle-id <id> record add --source <file> [--conversion <file-or-dir>] --metadata -
```

Use the returned `data.record` as the stable edit operand. Never write registry, profile, metadata, source, conversion, staging, or lock files directly.
