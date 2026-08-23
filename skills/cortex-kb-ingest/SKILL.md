---
name: cortex-kb-ingest
description: Explicit invocation only for adding records or an exact-v1 batch to an already configured Cortex 7.0 Bundle.
---

# Cortex KB ingest

Use this role only when the user explicitly names `cortex-kb-ingest`, or explicitly invokes `cortex` and the router selects KB ingest. Generic note, KB, or coding requests are insufficient triggers.

Use this skill only for `record.add` and the skill-local exact-v1 batch wrapper. Never initialize a Bundle, change profiles or Registry 1, edit/show/delete records, infer metadata, repair, migrate, or cut over. The embedded runtime remains the complete closed Cortex 7 CLI; these ownership boundaries are this skill's contract, not runtime route removal.

## Verified offline runtime

Set `CORTEX_PYTHON` to the lexical absolute path of the intended Python 3.11/UCD 14 executable. The launcher verifies that path is an ordinary non-reparse file reached through ordinary non-reparse ancestors and is the same filesystem entry as `sys.executable`. On POSIX invoke `"$CORTEX_PYTHON" -I <ABSOLUTE-SKILL>/scripts/run_cortex.py`; on Windows use the identical convenience launcher `<ABSOLUTE-SKILL>\scripts\run_cortex.cmd`. First require `--version` to emit exactly `cortex 7.0.0` on stdout and empty stderr. Do not use PATH or fall back to a global command, ambient package, installation, sibling skill, network, or update.

Require explicit `--workspace`, or explicit `--kb-root` plus `--bundle-id`. Inspect Record 1, Tag 2, and Layout 4. Require nonnull `partition_tag_group`, `partition_name_strategy: tag`, `unit_name_strategy: tag-title-date`, `duplicate_name_strategy: reject`, and exactly one record tag in the partition group. Never infer or add tags.

Invoke the owned single-record operation with unchanged full operands:

```text
"$CORTEX_PYTHON" -I <ABSOLUTE-SKILL>/scripts/run_cortex.py --json --workspace <bundle> record add --source <file> [--conversion <dir>] --metadata <json>
```

Confirm the Result's exact `partition`, `record`, and `path`; do not rename, move, search, or perform any unowned route. Full conversions must have the canonical Markdown/JSON/src shape; source-only input must be Markdown.

For an explicitly supplied batch, prefer stdin and use only this skill's generic helper:

```text
"$CORTEX_PYTHON" -I <ABSOLUTE-SKILL>/scripts/batch_record_add.py --workspace <bundle> --job -
```

The job is exact JSON v1: `{"version":1,"items":[...]}`. Each item has a unique nonempty `id`, absolute `source`, optional absolute `conversion`, and complete inline `metadata` containing exactly `title`, `timestamp`, and `tags`. The helper validates the whole job before calling the verified runner, executes sequentially without rollback, and emits one `record.add.batch` wrapper object. Valid failed Cortex Results are collected and later items continue; bootstrap, non-Result, and interrupt failures abort. This helper is not a Cortex route, creates no persistent job state, and exists only in `cortex-kb-ingest` and its deprecated compatibility alias `cortex-build`.
