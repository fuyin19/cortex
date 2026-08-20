---
name: cortex-build
description: Add a source and optional canonical conversion to an explicitly selected Cortex 6.0 Bundle.
---

# Build Cortex 6 records

Canonicalize the chosen executable to one absolute path and require an ordinary non-reparse executable. Probe that exact quoted path; require exit 0, exactly one `cortex 6.0.0` stdout line, and empty stderr. Do not fall back, re-resolve, invoke a bare PATH command, set `PYTHONPATH`, or use `python -m`.

```text
"<CORTEX-ABSOLUTE-EXECUTABLE>" --version
```

Require an explicit `--workspace`, or explicit KB root plus Bundle ID. Inspect the complete Layout Profile. Require a nonnull `unit_name_tag_group`, exactly one Tag 2 value from it, a caller-supplied timezone-aware RFC3339 timestamp, and `tag-title-date`/`reject`. Stop on unknown tags; never invent or auto-add them.

Full input is an exact canonical conversion directory containing one same-stem top-level `.md`/`.json` pair, `src/` with one source equal by basename and SHA-256 to `--source`, and optional safe `assets/`. Markdown-only input is one `.md` source and no conversion. Invoke only:

```text
<metadata-json> | "<CORTEX-ABSOLUTE-EXECUTABLE>" --json --workspace <bundle> record add --source <absolute-file> [--conversion <absolute-directory>] --metadata -
```

Treat `busy/5`, duplicate name, and `bundle_not_operational` as stop conditions. Never write directly into a Bundle.
