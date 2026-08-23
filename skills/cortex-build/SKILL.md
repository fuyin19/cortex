---
name: cortex-build
description: Deprecated compatibility alias for exact Cortex ingestion; invoke explicitly only and use cortex-kb-ingest for new work.
disable-model-invocation: true
---

# Cortex build

Set `CORTEX_PYTHON` to the lexical absolute path of the intended Python 3.11/UCD 14 executable. The launcher verifies that path is an ordinary non-reparse file reached through ordinary non-reparse ancestors and is the same filesystem entry as `sys.executable`. On POSIX invoke `"$CORTEX_PYTHON" -I <ABSOLUTE-SKILL>/scripts/run_cortex.py`; on Windows use the identical convenience launcher `<ABSOLUTE-SKILL>\scripts\run_cortex.cmd`. First require `--version` to emit exactly `cortex 7.0.0` on stdout and empty stderr. Do not use PATH or fall back to a global command, ambient package, installation, sibling skill, network, or update.

Require explicit `--workspace`, or explicit `--kb-root` plus `--bundle-id`. Inspect Record 1, Tag 2, Layout 4. Require nonnull `partition_tag_group`, `partition_name_strategy: tag`, `unit_name_strategy: tag-title-date`, `duplicate_name_strategy: reject`, and exactly one record tag in the partition group. Never infer or add tags.

Invoke add with unchanged arguments:

```text
"$CORTEX_PYTHON" -I <ABSOLUTE-SKILL>/scripts/run_cortex.py --json --workspace <bundle> record add --source <file> [--conversion <dir>] --metadata <json>
```

Confirm the Result's exact `partition`, `record`, and `path`; do not rename, move, search, repair, migrate, or cut over. Full conversions must have the canonical Markdown/JSON/src shape; source-only input must be Markdown.

For an explicitly supplied batch, prefer stdin and the build-only helper:

```text
"$CORTEX_PYTHON" -I <ABSOLUTE-SKILL>/scripts/batch_record_add.py --workspace <bundle> --job -
```

The job is exact JSON v1: `{"version":1,"items":[...]}`. Each item has a unique nonempty `id`, absolute `source`, optional absolute `conversion`, and complete inline `metadata` containing exactly `title`, `timestamp`, and `tags`. The helper validates the whole job before calling the verified runner, executes sequentially without rollback, and emits one `record.add.batch` wrapper object. Valid failed Cortex Results are collected and later items continue; bootstrap, non-Result, and interrupt failures abort. This helper is not a Cortex route and exists only in `cortex-build`; create no persistent job state.
