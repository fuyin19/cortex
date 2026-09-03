
# Cortex KB ingest

Use this role only when the user explicitly invokes `cortex` and the router selects KB ingest. Generic note, KB, or coding requests are insufficient triggers.

Use this skill only for `record.add` and the skill-local exact-v1/v2 batch wrapper. Never initialize a Bundle, change profiles or Registry 1, edit/show/delete records, infer metadata, repair, migrate, or cut over. The embedded runtime remains the complete closed Cortex 8.1 CLI; these ownership boundaries are this skill's contract, not runtime route removal.

## Verified offline runtime

Set `CORTEX_PYTHON` to the lexical absolute path of the intended Python 3.11/UCD 14 executable. The launcher verifies that path is an ordinary non-reparse file reached through ordinary non-reparse ancestors and is the same filesystem entry as `sys.executable`. On POSIX invoke `"$CORTEX_PYTHON" -I <ABSOLUTE-CORTEX-SKILL>/scripts/kb/run_cortex.py`; on Windows use the identical convenience launcher `<ABSOLUTE-CORTEX-SKILL>\scripts\kb\run_cortex.cmd`. First require `--version` to emit exactly `cortex 8.1.1` on stdout and empty stderr. Do not use PATH or fall back to a global command, ambient package, installation, another skill, network, or update.

The installed launcher binds the sibling `anti-entropy-core` skill at `<cortex-skill-parent>/anti-entropy-core/scripts/knowledge_unit_runner.py`. An explicit `ANTI_ENTROPY_CORE_RUNNER` absolute path overrides that default; a present empty, relative, missing, linked/reparse, or nonregular value fails without fallback. Core-dependent operations preflight ABI `anti-entropy-core.runner/v1` and exact Core version `1.2.1` within 30 seconds before business writes, then retain that runner for the operation. Update Core and the consumer to their matching releases if this check fails. Direct source/library use requires the explicit runner setting; it does not infer installation roots.

Require explicit `--workspace`, or explicit `--kb-root` plus `--bundle-id`. Inspect Record 1, Tag 2, and Layout 5. Require nonnull `partition_tag_group`, `partition_name_strategy: tag`, `unit_name_strategy: tag-title-date`, `duplicate_name_strategy: reject`, and exactly one record tag in the partition group. Never infer or add tags.

Invoke the owned single-record operation with unchanged full operands:

```text
"$CORTEX_PYTHON" -I <ABSOLUTE-CORTEX-SKILL>/scripts/kb/run_cortex.py --json --workspace <bundle> record add --metadata <json> [--source <file>] [--conversion <dir>]
```

Require at least one payload operand. Confirm the Result's exact `partition`, `record`, and `path`; do not rename, move, search, or perform any unowned route. Source-only retains one root representation; conversion-only fills missing envelope state; both fills empty `src/` or requires an exact retained-source match.

For an explicitly supplied batch, prefer stdin and use only this skill's generic helper:

```text
"$CORTEX_PYTHON" -I <ABSOLUTE-CORTEX-SKILL>/scripts/kb/batch_record_add.py --workspace <bundle> --job -
```

Job v1 remains exact: every item has absolute `source`, optional absolute `conversion`, and complete metadata. Job v2 permits source-only, conversion-only, or both and still requires a unique nonempty `id` plus complete inline `metadata`. The helper validates the whole job before calling the verified runner, executes sequentially without rollback, and emits one `record.add.batch` wrapper object. Valid failed Cortex Results are collected and later items continue; bootstrap, non-Result, and interrupt failures abort. This helper is not a Cortex route and creates no persistent job state.
