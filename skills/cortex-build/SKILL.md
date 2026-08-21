---
name: cortex-build
description: Add a source and optional canonical conversion to an explicitly selected Cortex 7.0 Bundle.
---

# Cortex build

Use the complete skill-local runtime with `<ABSOLUTE-PYTHON-3.11> -I <ABSOLUTE-SKILL>/scripts/run_cortex.py`. First require `--version` to emit exactly `cortex 7.0.0` on stdout and empty stderr. Do not fall back to a global command, ambient package, installation, sibling skill, network, or update.

Require explicit `--workspace`, or explicit `--kb-root` plus `--bundle-id`. Inspect Record 1, Tag 2, Layout 4. Require nonnull `partition_tag_group`, `partition_name_strategy: tag`, `unit_name_strategy: tag-title-date`, `duplicate_name_strategy: reject`, and exactly one record tag in the partition group. Never infer or add tags.

Invoke add with unchanged arguments:

```text
<ABSOLUTE-PYTHON-3.11> -I <ABSOLUTE-SKILL>/scripts/run_cortex.py --json --workspace <bundle> record add --source <file> [--conversion <dir>] --metadata <json>
```

Confirm the Result's exact `partition`, `record`, and `path`; do not rename, move, batch, search, repair, migrate, or cut over. Full conversions must have the canonical Markdown/JSON/src shape; source-only input must be Markdown.
