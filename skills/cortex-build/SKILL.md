---
name: cortex-build
description: Add a source and optional canonical conversion to an explicitly selected Cortex 6.0 Bundle.
---

# Build Cortex 6 records

Use this skill's complete local runtime through one absolute Python 3.11 interpreter and this skill's absolute runner path. Require exit 0, exactly one `cortex 6.0.0` stdout line, and empty stderr.

```text
"<ABSOLUTE-PYTHON-3.11>" -I "<ABSOLUTE-CORTEX-BUILD-SKILL-DIR>/scripts/run_cortex.py" --version
```

The runner verifies its pinned wheel before import and never resolves a PATH command. Do not fall back to a bare `cortex`, `python -m cortex`, `PYTHONPATH`, an ambient package, a sibling skill, pip, network access, installation, caching, or an update/latest check. A skill update must install this entire skill directory, including `scripts/run_cortex.py`, `scripts/runtime-manifest.json`, and `scripts/vendor/`.

Require an explicit `--workspace`, or explicit KB root plus Bundle ID. Inspect the complete Layout Profile. Require a nonnull `unit_name_tag_group`, exactly one Tag 2 value from it, a caller-supplied timezone-aware RFC3339 timestamp, and `tag-title-date`/`reject`. Stop on unknown tags; never invent or auto-add them.

Full input is an exact canonical conversion directory containing one same-stem top-level `.md`/`.json` pair, `src/` with one source equal by basename and SHA-256 to `--source`, and optional safe `assets/`. Markdown-only input is one `.md` source and no conversion. Invoke only:

```text
<metadata-json> | "<ABSOLUTE-PYTHON-3.11>" -I "<ABSOLUTE-CORTEX-BUILD-SKILL-DIR>/scripts/run_cortex.py" --json --workspace <bundle> record add --source <absolute-file> [--conversion <absolute-directory>] --metadata -
```

Treat `busy/5`, duplicate name, and `bundle_not_operational` as stop conditions. Never write directly into a Bundle.
